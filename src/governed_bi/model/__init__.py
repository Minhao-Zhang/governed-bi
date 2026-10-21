"""Model adapters. Two :class:`~governed_bi.ports.Embedder` implementations, and the
OpenAI chat-model builder in ``provider.py``.

Importing this package does not import the OpenAI SDK. Each provider tree is
imported inside the function that needs it, so a model-free test can reach
``DeterministicEmbedder``.
"""

from __future__ import annotations

from .deterministic_embedder import DETERMINISTIC_DIMENSIONS, DeterministicEmbedder
from .embedder import (
    DEFAULT_BATCH_SIZE,
    BaseEmbedder,
    embedding_settings,
    refuse_blank,
)
from .openai_embedder import (
    OPENAI_API_KEY_VAR,
    OPENAI_EMBEDDING_MODEL,
    OpenAIEmbedder,
)
from .provider import (
    SURFACE_MODEL_VARS,
    chat_model,
    embedder,
    reasoning_effort_of,
)

__all__ = [
    "DEFAULT_BATCH_SIZE",
    "DETERMINISTIC_DIMENSIONS",
    "OPENAI_API_KEY_VAR",
    "OPENAI_EMBEDDING_MODEL",
    "SURFACE_MODEL_VARS",
    "BaseEmbedder",
    "DeterministicEmbedder",
    "OpenAIEmbedder",
    "chat_model",
    "embedder",
    "embedding_settings",
    "reasoning_effort_of",
    "refuse_blank",
]
