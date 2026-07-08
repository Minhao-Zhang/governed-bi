"""Server step 4 — SQL semantic-cache fast path.

Embed the question → cosine similarity ≥ ``cache_hit_cosine_gate`` (0.92)
against the cached-SQL library → hit skips retrieval / planning / generation but
**always re-executes** the cached SQL (freshness over latency). Cache **SQL text
only, never results**, and scope by identity (D7) — results leak across users.
TTL = ``sql_cache_ttl_minutes`` (15). Miss → full pipeline, then write back on
success.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..gateway import Identity


def lookup(question: str, identity: "Identity") -> str | None:
    """Return cached SQL text for a ≥gate cosine match (identity-scoped), else None."""
    raise NotImplementedError("semantic SQL cache pending; needs an embedder + store")


def write_back(question: str, sql: str, identity: "Identity") -> None:
    """Cache SQL text (never results) on a successful answer."""
    raise NotImplementedError("semantic SQL cache pending")
