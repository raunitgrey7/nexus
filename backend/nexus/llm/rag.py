"""Tiny retrieval layer over operating procedures (SOPs).

A curated knowledge base of operational playbooks is retrieved by TF-IDF cosine similarity (pure
Python, deterministic) and, when Ollama is available, optionally re-ranked with embeddings. Retrieved
snippets are injected into the planner prompt so the LLM's candidates follow house rules ("reassign to
robots in adjacent zones first", "pre-position hot SKUs before the afternoon peak" …).
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

TOKEN = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True, slots=True)
class Document:
    id: str
    title: str
    text: str
    tags: tuple[str, ...] = ()


SOPS: list[Document] = [
    Document(
        "sop-robot-failure",
        "Robot failure response",
        "When a robot fails, its tasks are released to the pending pool. Reassign released work to operational robots "
        "in the same or adjacent zones first to limit extra travel; prioritize HIGH and CRITICAL orders whose deadlines "
        "are closest; if the failed robot's zone becomes congested, reroute traffic through a parallel corridor.",
        ("failure", "reassign", "robot"),
    ),
    Document(
        "sop-congestion",
        "Zone congestion mitigation",
        "A zone is congested when more robots are inside than its capacity. Mitigate by (1) routing away from the zone "
        "with a penalty for 20-40 minutes, (2) preferring the adjacent corridor, (3) pre-positioning the hottest SKUs "
        "of the congested zone into a neighbouring zone with spare capacity, (4) temporarily lowering the zone's soft capacity.",
        ("congestion", "zone", "routing", "inventory"),
    ),
    Document(
        "sop-demand-spike",
        "Demand spike playbook",
        "When projected utilization exceeds 90%, enable order batching (2-3 orders per trip) before adding robots; "
        "reprioritize by deadline slack; add robots only if batching is already active; consider staging inventory "
        "near the docks for the highest-velocity SKUs.",
        ("demand", "batching", "capacity", "robots"),
    ),
    Document(
        "sop-battery",
        "Battery management",
        "Send a robot to charge when its predicted exhaustion is within 10 minutes of its charger travel time plus a safety "
        "margin. Prefer charging after the current task completes rather than abandoning picked items. Never send more "
        "than a quarter of the fleet to charge simultaneously during peak hours.",
        ("battery", "charging", "robot"),
    ),
    Document(
        "sop-dock-closure",
        "Dock closure",
        "If a loading dock closes, rebalance deliveries to the nearest open docks and dispatch a loader worker to the busiest "
        "remaining dock; unloading without a loader takes twice as long.",
        ("dock", "worker", "closure"),
    ),
    Document(
        "sop-aisle-block",
        "Blocked aisle",
        "A blocked aisle forces replanning. Route around it, and if the blockage is inside a hot zone, reposition the "
        "affected SKUs to an accessible shelf until the aisle is cleared.",
        ("aisle", "blocked", "routing", "inventory"),
    ),
    Document(
        "sop-safety",
        "Safety policy",
        "Plans must keep every zone below twice its capacity, must not route robots through closed zones, must keep at "
        "least one dock open and at least one charging station enabled. Plans failing these checks are rejected before "
        "simulation.",
        ("safety", "policy", "constraints"),
    ),
    Document(
        "sop-simulate-first",
        "Simulate before execute",
        "No plan is executed on the live operation without a forked-world simulation over the decision horizon and a risk "
        "assessment. Plans with LOW risk and a measurable SLA improvement may be auto-approved; MEDIUM and above require "
        "an operator.",
        ("simulation", "approval", "risk"),
    ),
]


def _tokens(text: str) -> list[str]:
    return TOKEN.findall(text.lower())


class SOPRetriever:
    def __init__(self, docs: list[Document] | None = None) -> None:
        self.docs = docs or SOPS
        self._df: Counter[str] = Counter()
        self._vectors: list[dict[str, float]] = []
        for d in self.docs:
            tf = Counter(_tokens(d.title + " " + d.text + " " + " ".join(d.tags)))
            self._df.update(tf.keys())
            self._vectors.append(dict(tf))
        n = len(self.docs)
        self._idf = {t: math.log((1 + n) / (1 + df)) + 1.0 for t, df in self._df.items()}
        self._vectors = [self._weigh(v) for v in self._vectors]

    def _weigh(self, tf: dict[str, float]) -> dict[str, float]:
        vec = {t: (1 + math.log(c)) * self._idf.get(t, 1.0) for t, c in tf.items()}
        norm = math.sqrt(sum(x * x for x in vec.values())) or 1.0
        return {t: x / norm for t, x in vec.items()}

    def retrieve(self, query: str, k: int = 3) -> list[tuple[Document, float]]:
        q = self._weigh(dict(Counter(_tokens(query))))
        scored = []
        for doc, vec in zip(self.docs, self._vectors, strict=True):
            score = sum(w * vec.get(t, 0.0) for t, w in q.items())
            if score > 0:
                scored.append((doc, round(score, 4)))
        scored.sort(key=lambda x: (-x[1], x[0].id))
        return scored[:k]

    def snippets(self, query: str, k: int = 3) -> list[str]:
        return [f"{d.title}: {d.text}" for d, _ in self.retrieve(query, k)]
