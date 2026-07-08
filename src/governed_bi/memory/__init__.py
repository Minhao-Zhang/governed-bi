"""Memory service (D8): Working / Profile / Episodic / Correction.

Policy: **working memory always on** (session, identity-scoped); episodic and
correction **off by default**, adopted per-domain only when eval earns it.
Durable memory is PR-gated exactly like the corpus, so the memory/corpus
distinction collapses: correction memory is a PR to a reference doc; promoted
episodic is a gated few-shot. Only working/ephemeral memory is outside the gate.

Identity-scoping covers memory + cache, not just the live query (D7): episodic
memory and result caching leak across users if not scoped, which is why we
cache SQL text (re-run per user), never results.

TTLs / gates / route budgets live in ``governed_bi.config``.
"""

from __future__ import annotations

from .store import (
    CorrectionMemory,
    EpisodicMemory,
    InMemoryWorkingMemory,
    Turn,
    WorkingMemory,
)

__all__ = [
    "CorrectionMemory",
    "EpisodicMemory",
    "InMemoryWorkingMemory",
    "Turn",
    "WorkingMemory",
]
