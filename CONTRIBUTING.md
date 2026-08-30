# Contributing to NEXUS

Thanks for your interest. NEXUS is engineered as a production platform, so contributions are held to a
production bar: deterministic, tested, typed, documented.

## Ground rules

1. **Determinism is sacred.** Anything inside `nexus/twin`, `nexus/events`, `nexus/simulation`,
   `nexus/optimization` must be reproducible from a seed. No wall-clock, no unseeded randomness, no
   dict-ordering assumptions across processes. The test-suite has determinism, fork and replay gates.
2. **Only the reducer mutates the world.** Engines and agents emit events (`engine.emit` / `engine.inject`).
   If you need new state changes, add an `EventType` and a reducer handler (+ a test).
3. **The LLM proposes, mathematics disposes.** Anything the LLM outputs must be validated, optimized,
   simulated and risk-checked before it reaches the live world. Every feature must work with the LLM off.
4. **Domain-agnostic engine.** The engine never imports `nexus.twin.layout`. New domains implement
   `nexus.twin.domain.DomainModel`.
5. **KPIs are defined once** (`nexus/simulation/metrics.py`, mirrored in `ROADMAP.md`).

## Workflow

```bash
make setup          # uv + npm
make test           # backend tests (< 3 min)
make lint           # ruff, mypy, eslint, tsc
make bench-quick    # 1-seed small benchmark
```

* Branch from `main`, open a PR with a clear description and, for behavioural changes, before/after
  benchmark numbers (`make bench-quick`).
* Add an ADR in `docs/adr/` for architectural decisions.
* Keep PRs focused; large features are split by milestone (see `ROADMAP.md`).

## Code style

* Python 3.12+, `ruff` (line length 110), `mypy` clean, docstrings that explain *why* and the math.
* TypeScript strict, no `any`, ESLint clean.
* Commit messages: imperative mood, scoped (`sim: congestion-aware speed`, `agents: risk stability seeds`).
