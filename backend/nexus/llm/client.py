"""Local LLM client (Ollama).

Principles:

* **Optional.** Every caller must work when the LLM is unavailable (``available()`` is cheap and cached);
  the agents fall back to deterministic planners and templates.
* **Structured.** Planning output is requested as JSON constrained by a JSON schema (Ollama structured
  outputs) and validated with pydantic; invalid output is retried once with the validation error fed
  back, then discarded.
* **Bounded.** Hard timeouts, small token budgets, and per-call latency accounting.
"""

from __future__ import annotations

import copy
import json
import time
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from nexus.core.config import settings
from nexus.core.logging import get_logger

log = get_logger("nexus.llm")
T = TypeVar("T", bound=BaseModel)


class LLMClient:
    def __init__(
        self,
        url: str | None = None,
        model: str | None = None,
        timeout_s: float | None = None,
        enabled: bool | None = None,
        temperature: float | None = None,
    ) -> None:
        self.url = (url or settings.ollama_url).rstrip("/")
        self.model = model or settings.llm_model
        self.fallback_model = settings.llm_fallback_model
        self.active_model = self.model
        self._models: list[str] = []
        self.timeout_s = timeout_s or settings.llm_timeout_s
        self.enabled = settings.llm_enabled if enabled is None else enabled
        self.temperature = settings.llm_temperature if temperature is None else temperature
        self._available: bool | None = None
        self._checked_at = 0.0
        self._cooldown_until = 0.0
        self.cooldown_s = 60.0
        self.calls = 0
        self.failures = 0
        self.total_latency_ms = 0.0
        self._client = httpx.Client(timeout=httpx.Timeout(self.timeout_s, connect=3.0))

    # ---- availability --------------------------------------------------------------------------
    def available(self, ttl_s: float = 30.0) -> bool:
        if not self.enabled:
            return False
        now = time.monotonic()
        if now < self._cooldown_until:
            return False
        if self._available is not None and now - self._checked_at < ttl_s:
            return self._available
        try:
            r = self._client.get(f"{self.url}/api/tags", timeout=3.0)
            r.raise_for_status()
            models = [m.get("name", "") for m in r.json().get("models", [])]
            self._models = models
            base = self.model.split(":")[0]
            ok = any(m == self.model or m.split(":")[0] == base for m in models)
            if not ok and models:
                log.warning("llm.model_missing", wanted=self.model, have=models[:5])
            self._available = ok
        except Exception as exc:
            log.info("llm.unavailable", error=str(exc)[:120])
            self._available = False
        self._checked_at = now
        return self._available

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "model": self.active_model,
            "primary_model": self.model,
            "available": self.available(),
            "url": self.url,
            "calls": self.calls,
            "failures": self.failures,
            "avg_latency_ms": round(self.total_latency_ms / self.calls, 1) if self.calls else 0.0,
        }

    # ---- calls ---------------------------------------------------------------------------------
    def chat(
        self,
        messages: list[dict[str, str]],
        json_schema: dict[str, Any] | None = None,
        temperature: float | None = None,
        max_tokens: int = 1200,
        timeout_s: float | None = None,
    ) -> str | None:
        if not self.available():
            return None
        body: dict[str, Any] = {
            "model": self.active_model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": self.temperature if temperature is None else temperature,
                "num_predict": max_tokens,
            },
        }
        if json_schema is not None:
            body["format"] = inline_schema(json_schema)
        t0 = time.perf_counter()
        self.calls += 1
        try:
            r = self._client.post(f"{self.url}/api/chat", json=body, timeout=timeout_s or self.timeout_s)
            r.raise_for_status()
            content = r.json().get("message", {}).get("content", "")
            self.total_latency_ms += (time.perf_counter() - t0) * 1000
            return content
        except httpx.HTTPStatusError as exc:
            self.failures += 1
            self.total_latency_ms += (time.perf_counter() - t0) * 1000
            detail = exc.response.text[:200]
            log.warning(
                "llm.call_failed", status=exc.response.status_code, error=detail, model=self.active_model
            )
            if "memory" in detail.lower() and self._switch_to_fallback():
                return self.chat(messages, json_schema, temperature, max_tokens, timeout_s)
            self._available = None  # re-probe next time
            self._cooldown_until = time.monotonic() + self.cooldown_s
            return None
        except Exception as exc:
            self.failures += 1
            self.total_latency_ms += (time.perf_counter() - t0) * 1000
            log.warning("llm.call_failed", error=str(exc)[:200])
            self._available = None  # re-probe next time
            self._cooldown_until = time.monotonic() + self.cooldown_s
            return None

    def _switch_to_fallback(self) -> bool:
        """Switch to the smaller fallback model (once) when the primary cannot be loaded."""
        fb = self.fallback_model
        if not fb or self.active_model == fb:
            return False
        base = fb.split(":")[0]
        if self._models and not any(m == fb or m.split(":")[0] == base for m in self._models):
            return False
        log.warning("llm.fallback_model", from_model=self.active_model, to_model=fb)
        self.active_model = fb
        return True

    def complete(
        self,
        prompt: str,
        system: str | None = None,
        temperature: float | None = None,
        max_tokens: int = 600,
        timeout_s: float | None = None,
    ) -> str | None:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        return self.chat(messages, temperature=temperature, max_tokens=max_tokens, timeout_s=timeout_s)

    def structured(
        self,
        messages: list[dict[str, str]],
        model_cls: type[T],
        retries: int = 1,
        temperature: float | None = None,
        max_tokens: int = 1600,
        timeout_s: float | None = None,
    ) -> T | None:
        """Chat constrained to ``model_cls``'s JSON schema; validated; retried once on invalid output."""
        schema = model_cls.model_json_schema()
        attempt = 0
        history = list(messages)
        while attempt <= retries:
            attempt += 1
            raw = self.chat(
                history,
                json_schema=schema,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout_s=timeout_s,
            )
            if raw is None:
                return None
            try:
                data = _extract_json(raw)
                return model_cls.model_validate(data)
            except (ValidationError, ValueError) as exc:
                log.info("llm.invalid_structured_output", attempt=attempt, error=str(exc)[:200])
                history = [
                    *history,
                    {"role": "assistant", "content": raw},
                    {
                        "role": "user",
                        "content": f"Your JSON was invalid: {str(exc)[:400]}. Reply with corrected JSON only.",
                    },
                ]
        self.failures += 1
        return None

    def embed(self, texts: list[str], model: str | None = None) -> list[list[float]] | None:
        if not self.available():
            return None
        try:
            r = self._client.post(
                f"{self.url}/api/embed", json={"model": model or self.model, "input": texts}
            )
            r.raise_for_status()
            return r.json().get("embeddings")
        except Exception as exc:
            log.info("llm.embed_failed", error=str(exc)[:120])
            return None

    def close(self) -> None:
        self._client.close()


def inline_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Resolve local ``$ref``s so the schema has no ``$defs`` (older Ollama builds reject them)."""
    defs = schema.get("$defs", {})

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                name = str(node["$ref"]).split("/")[-1]
                return walk(copy.deepcopy(defs.get(name, {})))
            return {k: walk(v) for k, v in node.items() if k != "$defs"}
        if isinstance(node, list):
            return [walk(x) for x in node]
        return node

    return walk(schema)  # type: ignore[no-any-return]


def _extract_json(text: str) -> Any:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start : end + 1])
        raise


class NullLLM(LLMClient):
    """An always-unavailable client (tests, benchmarks, air-gapped deployments)."""

    def __init__(self) -> None:
        super().__init__(enabled=False)

    def available(self, ttl_s: float = 30.0) -> bool:
        return False
