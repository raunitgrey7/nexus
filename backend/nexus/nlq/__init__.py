"""Natural-language console: intent routing, delay attribution, grounded answers."""

from nexus.nlq.explain import attribute_delay, explain_text
from nexus.nlq.router import SUGGESTIONS, build_scenario, classify, extract_params
from nexus.nlq.service import NLQService

__all__ = [
    "SUGGESTIONS",
    "NLQService",
    "attribute_delay",
    "build_scenario",
    "classify",
    "explain_text",
    "extract_params",
]
