"""Model adapters (parcel I). Today: the two :class:`~governed_bi.ports.Embedder` ones.

No Bedrock adapter: ``langchain-aws`` pulls a boto3 tree and nothing here selects a Bedrock
provider, so its contract would go unchecked.

Importing this package does **not** import the OpenAI SDK — ``openai`` is imported inside
``OpenAIEmbedder._openai_client``, so a bare interpreter and every model-free test can reach
``DeterministicEmbedder`` without the provider tree.
"""

from __future__ import annotations

from .deterministic_embedder import DETERMINISTIC_DIMENSIONS, DeterministicEmbedder
from .embedder import (
    DEFAULT_BATCH_SIZE,
    BaseEmbedder,
    embedding_knobs,
    refuse_blank,
)
from .openai_embedder import (
    OPENAI_API_KEY_VAR,
    OPENAI_EMBEDDING_MODEL,
    OpenAIEmbedder,
)

__all__ = [
    "DEFAULT_BATCH_SIZE",
    "DETERMINISTIC_DIMENSIONS",
    "OPENAI_API_KEY_VAR",
    "OPENAI_EMBEDDING_MODEL",
    "BaseEmbedder",
    "DeterministicEmbedder",
    "OpenAIEmbedder",
    "embedding_knobs",
    "refuse_blank",
]
