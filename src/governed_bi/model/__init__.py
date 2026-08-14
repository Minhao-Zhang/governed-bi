"""Model adapters (parcel I). Today: the two :class:`~governed_bi.ports.Embedder` ones.

``ports.py:107`` names three — ``openai_embedder``, ``bedrock_embedder``,
``deterministic_embedder``. Two are here. **``bedrock_embedder.py`` is deliberately
absent**: ``langchain-aws`` is an optional extra that pulls a boto3 tree, nothing in the
repository selects a Bedrock provider, and an adapter no caller reaches is an adapter
whose contract nothing checks. ``ports.py`` naming it is a plan, not a debt — the port
already has the two independent implementations its "every port here has at least two
adapters" rule asks for, and the empty-string hazard that docstring cites Bedrock for is
enforced here anyway, in ``embedder.refuse_blank``.

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
