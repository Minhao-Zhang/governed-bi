"""Model adapters (parcel I). Three :class:`~governed_bi.ports.Embedder` ones, plus the internal proxy
proxy's chat-model builder — the only non-embedder here, because the 2026-08-07 BIRD run went
through it and an engine that exists only on a server reproduces nothing.

``provider.py`` is where a surface's gateway is chosen and where one intent — reasoning
effort, timeout, retry count — is spelled for whichever gateway that is. Call sites name a
surface, not a provider.

Importing this package does **not** import the OpenAI SDK, ``boto3``, ``langchain-aws`` or
``httpx`` — every provider tree is imported inside the function that needs it, so a bare
interpreter and every model-free test can reach ``DeterministicEmbedder``.
"""

from __future__ import annotations

from .bedrock_embedder import BEDROCK_EMBEDDING_MODEL, BedrockEmbedder
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
from .provider import (
    PROVIDER_VAR,
    SURFACE_PROVIDER_VARS,
    chat_model,
    provider_for,
    supported_providers,
)
from .proxy_embedder import ProxyEmbedder
from .proxy_gateway import (
    PROXY_CA_BUNDLE_VAR,
    PROXY_REGION_VAR,
    PROXY_SECRET_NAME_VAR,
)

__all__ = [
    "BEDROCK_EMBEDDING_MODEL",
    "DEFAULT_BATCH_SIZE",
    "DETERMINISTIC_DIMENSIONS",
    "PROVIDER_VAR",
    "PROXY_CA_BUNDLE_VAR",
    "PROXY_REGION_VAR",
    "PROXY_SECRET_NAME_VAR",
    "OPENAI_API_KEY_VAR",
    "OPENAI_EMBEDDING_MODEL",
    "SURFACE_PROVIDER_VARS",
    "BaseEmbedder",
    "BedrockEmbedder",
    "DeterministicEmbedder",
    "ProxyEmbedder",
    "OpenAIEmbedder",
    "chat_model",
    "embedding_knobs",
    "provider_for",
    "refuse_blank",
    "supported_providers",
]
