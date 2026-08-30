# ADR 0008 — Local LLM only, zero API keys

**Status:** accepted (2026-08-30)

## Context

Operational data (orders, inventory, incidents) is sensitive; paid APIs add cost, latency variance and a hard dependency;
the project constraint is a ₹0 stack that anyone can run on a laptop.

## Decision

The only LLM integration is a local Ollama endpoint (`NEXUS_OLLAMA_URL`, default model `qwen2.5:7b`) accessed through
`nexus/llm/client.py` with availability probing, bounded timeouts, schema-constrained structured output and a single
retry. `NullLLM` represents "no model"; every feature — planning, explanations, the console — has a deterministic path
without a model, and tests/benchmarks use it. Optional embeddings also go through Ollama.

## Consequences

* No API keys, no egress, reproducible CI; the demo runs offline.
* Small local models produce less sophisticated plans than frontier models; the closed vocabulary, validation and
  simulation make that safe rather than fatal.
* Swapping providers is a client change only; the pipeline contract (structured `PlanSet`) stays.
