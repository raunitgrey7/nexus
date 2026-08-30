"""Local LLM integration (Ollama): client, prompts, SOP retrieval."""

from nexus.llm.client import LLMClient, NullLLM
from nexus.llm.rag import SOPS, Document, SOPRetriever

__all__ = ["SOPS", "Document", "LLMClient", "NullLLM", "SOPRetriever"]
