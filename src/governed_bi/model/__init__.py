"""Model adapters (parcel I). Three :class:`~governed_bi.ports.Embedder` ones, plus the internal proxy
proxy's chat-model builder — the only non-embedder here, because the 2026-08-07 BIRD run went
through it and an engine that exists only on a server reproduces nothing.

No Bedrock adapter: ``langchain-aws`` pulls a boto3 tree and nothing here selects a Bedrock
provider, so its contract would go unchecked.

Importing this package does **not** import the OpenAI SDK, ``boto3`` or ``httpx`` — every
provider tree is imported inside the function that needs it, so a bare interpreter and every
model-free test can reach ``DeterministicEmbedder``.
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
from .proxy_embedder import ProxyEmbedder
from .proxy_gateway import (
    PROXY_CA_BUNDLE_VAR,
    PROXY_REGION_VAR,
    PROXY_SECRET_NAME_VAR,
)

__all__ = [
    "DEFAULT_BATCH_SIZE",
    "DETERMINISTIC_DIMENSIONS",
    "PROXY_CA_BUNDLE_VAR",
    "PROXY_REGION_VAR",
    "PROXY_SECRET_NAME_VAR",
    "OPENAI_API_KEY_VAR",
    "OPENAI_EMBEDDING_MODEL",
    "BaseEmbedder",
    "DeterministicEmbedder",
    "ProxyEmbedder",
    "OpenAIEmbedder",
    "embedding_knobs",
    "refuse_blank",
]
