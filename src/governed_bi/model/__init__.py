"""Model adapters (parcel I). All three :class:`~governed_bi.ports.Embedder` ones.

``ports.py:107`` names three — ``openai_embedder``, ``bedrock_embedder``,
``deterministic_embedder``. **``bedrock_embedder.py`` was deliberately absent** ("an
adapter no caller reaches is an adapter whose contract nothing checks") until UtkuAI
(ported: see ``utku-ai-v2-porting-spec.md``) added the caller — ``langchain-aws`` is now
a direct dependency for the chat-model side (``ChatBedrockConverse``), so the embedder's
"optional extra" reason no longer holds either. The empty-string hazard that every other
docstring here cites Bedrock for was always enforced regardless, in
``embedder.refuse_blank``.

**Where this package sits.** ``tools/check_imports.py`` puts ``model`` between
``datasource`` and ``serve``, so an adapter may import ``ports``, ``register``,
``measure``, ``corpus``, ``retrieve``, ``govern`` and ``datasource``, and nothing here may
be imported by any of them. That is the inversion ``ports.py:10`` describes: the ports sit
at the bottom so pure computation can be typed against a capability without importing
anything able to perform it, which is what keeps ``retrieve/`` free of a provider SDK.

Importing this package does **not** import the OpenAI SDK. ``openai`` is imported inside
``OpenAIEmbedder._openai_client``, so a bare interpreter and every model-free test can
reach ``DeterministicEmbedder`` without the provider tree.
"""

from __future__ import annotations

from .bedrock_embedder import (
    BEDROCK_EMBEDDING_DIMENSIONS,
    BEDROCK_EMBEDDING_MODEL,
    BedrockEmbedder,
)
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
    "BEDROCK_EMBEDDING_DIMENSIONS",
    "BEDROCK_EMBEDDING_MODEL",
    "DEFAULT_BATCH_SIZE",
    "DETERMINISTIC_DIMENSIONS",
    "OPENAI_API_KEY_VAR",
    "OPENAI_EMBEDDING_MODEL",
    "BaseEmbedder",
    "BedrockEmbedder",
    "DeterministicEmbedder",
    "OpenAIEmbedder",
    "embedding_knobs",
    "refuse_blank",
]
