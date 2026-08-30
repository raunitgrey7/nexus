# ADR 0004 — The LLM proposes, mathematics disposes

**Status:** accepted (2026-08-30)

## Context

"LLM → move robot to zone B" is weak: language models hallucinate ids, cannot solve assignment problems, and cannot be
audited. Yet they are excellent at proposing diverse, plausible strategies from a situation description and at
explaining decisions in operator language.

## Decision

The planner (`nexus/agents/planner.py`) asks the LLM for *candidate plans* expressed in a closed action vocabulary
(`ActionType`) via schema-constrained structured output, alongside deterministic playbooks that always run. Every
candidate — LLM or playbook — then passes the same pipeline: constraint validation, optimization (CP-SAT assignment,
routing policies), simulation in forked worlds, risk assessment, approval policy. The explanation is rendered from the
decision record; an LLM rewrite is discarded if it changes a number. The system must work, and be deterministic, with
the LLM switched off (`NullLLM`).

## Consequences

* Benchmarks and tests run LLM-free and reproducibly; the LLM adds candidate diversity and prose, never authority.
* Unsafe or nonsensical proposals are dropped by the validator before they cost simulation time.
* Local models (Ollama, `qwen2.5:7b`) are sufficient — no API keys, no data leaves the machine.
* Quality of LLM candidates depends on the situation text and retrieved SOPs, which are maintained as code.
