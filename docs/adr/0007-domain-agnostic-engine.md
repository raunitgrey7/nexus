# ADR 0007 — Domain-agnostic engine, domain-specific model

**Status:** accepted (2026-08-30)

## Context

The product is *autonomous operations intelligence*, not a warehouse simulator. The same core should eventually model a
factory, a hospital, an airport, a data centre or a fleet. A warehouse is the first domain because it is a controlled,
visually compelling environment.

## Decision

The engine (events, simulation, optimization, forecasting, agents, what-if, API) depends only on the entity *shapes* in
`nexus/twin/entities.py` — zones, mobile agents with energy, jobs with pick/deliver locations and deadlines, sinks,
chargers, workers. A `DomainModel` (`nexus/twin/domain.py`) builds the concrete world, supplies vocabulary and a
description; the engine never imports the warehouse layout module.

## Consequences

* Adding a domain means a layout builder, a demand model, playbooks/SOPs and presets — not engine changes
  (`docs/DOMAIN_EXTENSION.md`).
* Some warehouse assumptions remain in the *edges* (fault/what-if presets, playbook wording, NL defaults, UI glyphs);
  they are catalogued so a new domain knows what to replace.
* The abstraction is validated by one domain only; the second domain will refine the protocol (e.g. multi-sink jobs,
  non-grid spaces).
