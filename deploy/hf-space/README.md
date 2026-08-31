---
title: NEXUS — Autonomous Operations Intelligence
emoji: 🏭
colorFrom: gray
colorTo: blue
sdk: docker
app_port: 8000
pinned: true
license: apache-2.0
short_description: AI-native digital twin of a warehouse with agents that simulate before they act
---

# NEXUS backend — live digital twin API

This Space runs the **NEXUS** backend: an event-sourced warehouse digital twin with a deterministic
simulation engine, OR-Tools CP-SAT optimization, Holt-Winters forecasting, and a multi-agent runtime
that simulates every candidate plan in forked worlds before a risk-gated execution.

- **Control room UI**: https://nexus-twin-psi.vercel.app (Next.js + Three.js, streams this Space over WebSocket)
- **API docs**: `/docs` on this Space
- **Repository**: https://github.com/raunitgrey7/nexus
- **Benchmarks**: SLA breach 41.7% (naive baseline) → **1.8%** (full agentic loop) on identical worlds — see `docs/BENCHMARKS.md`

The LLM layer is disabled in this deployment (no GPU); the platform degrades by design to its
deterministic planner playbooks — every number you see is simulated, optimized and risk-checked, not generated.

Note: this is a free CPU Space — the twin pauses after 48 h without visitors and wakes on the next request.
