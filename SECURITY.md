# Security policy

## Scope

NEXUS runs a digital twin and an agent runtime that can **recommend and execute actions** against an
operation. The safety architecture (see `docs/SAFETY.md`) is the primary security control:

* every plan passes constraint validation, forked-world simulation, and a risk assessment;
* auto-approval is restricted to `LOW` risk plans with a measurable gain; everything else requires a human;
* all executed actions are attributable events (`origin`, `cause`, idempotency `key`) in the append-only log.

## Reporting a vulnerability

Please email **raunit.thakur@gmail.com** with details and a reproduction. You will receive an
acknowledgement within 72 hours. Do not open public issues for security problems.

## Deployment guidance

* The API has no built-in authentication: put it behind your gateway (OIDC / mTLS) before exposing it.
* Keep the LLM local (Ollama) or route through a gateway with zero data retention.
* Persist events to PostgreSQL for audit; snapshots contain full world state and may include order data.
* Run the containers as non-root (the provided Dockerfiles do) and pin image digests in production.
