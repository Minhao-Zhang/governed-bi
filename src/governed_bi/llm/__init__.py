"""LLM + embedding clients (the model seams).

OpenAI-backed by project decision, but nothing outside this package imports
``openai``: the rest of the system depends only on the :class:`ChatClient` and
:class:`Embedder` protocols, so the provider is swappable. Deterministic
offline implementations (:class:`StaticChatClient`, :class:`HashingEmbedder`)
let the whole pipeline run and be tested without a model, a key, or a network.

See ``governed_bi.config.ModelConfig`` for what these clients are told to use.
"""

from __future__ import annotations

from .client import (
    ChatClient,
    Embedder,
    HashingEmbedder,
    OpenAiChatClient,
    OpenAiEmbedder,
    StaticChatClient,
    cosine,
)
from .langchain_client import LangChainChatClient, LangChainEmbedder

__all__ = [
    "ChatClient",
    "Embedder",
    "HashingEmbedder",
    "LangChainChatClient",
    "LangChainEmbedder",
    "OpenAiChatClient",
    "OpenAiEmbedder",
    "StaticChatClient",
    "cosine",
]
