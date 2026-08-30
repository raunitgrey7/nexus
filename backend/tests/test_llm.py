import httpx

from nexus.agents.planner import PlanSet
from nexus.llm.client import LLMClient, NullLLM, inline_schema


def test_inline_schema_removes_refs():
    schema = PlanSet.model_json_schema()
    assert "$defs" in schema
    flat = inline_schema(schema)
    text = str(flat)
    assert "$ref" not in text and "$defs" not in text
    assert (
        flat["properties"]["plans"]["items"]["properties"]["actions"]["items"]["properties"]["type"]["type"]
        == "string"
    )


def test_null_llm_is_never_available():
    llm = NullLLM()
    assert llm.available() is False and llm.chat([{"role": "user", "content": "hi"}]) is None
    assert llm.structured([{"role": "user", "content": "hi"}], PlanSet) is None


def test_fallback_model_on_memory_error(monkeypatch):
    llm = LLMClient(url="http://llm.invalid", model="big:7b", enabled=True)
    llm.fallback_model = "small:3b"
    llm._models = ["big:7b", "small:3b"]
    llm._available, llm._checked_at = True, 1e18  # skip the probe
    calls = []

    def fake_post(url, json=None, **kwargs):
        calls.append(json["model"])
        if json["model"] == "big:7b":
            req = httpx.Request("POST", url)
            raise httpx.HTTPStatusError(
                "boom",
                request=req,
                response=httpx.Response(
                    500, text='{"error":"model requires more system memory"}', request=req
                ),
            )
        return httpx.Response(
            200, json={"message": {"content": '{"ok": true}'}}, request=httpx.Request("POST", url)
        )

    monkeypatch.setattr(llm._client, "post", fake_post)
    out = llm.chat([{"role": "user", "content": "x"}], json_schema={"type": "object"})
    assert out == '{"ok": true}' and calls == ["big:7b", "small:3b"] and llm.active_model == "small:3b"
    assert llm.status()["model"] == "small:3b" and llm.status()["primary_model"] == "big:7b"
