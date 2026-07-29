"""Memory service (D8): Working / Profile / Episodic / Correction.

Policy: **working memory always on** (session, identity-scoped); episodic and
correction **off by default**, adopted per-domain only when eval earns it.
Durable memory is PR-gated exactly like the corpus, so the memory/corpus
distinction collapses: correction memory is a PR to a reference doc; promoted
episodic is a gated few-shot. Only working/ephemeral memory is outside the gate.

Identity-scoping covers the live query and anything durable keyed off it (D7):
episodic memory leaks across users if not scoped. There is no result cache and no
SQL cache — the semantic cache this module used to name was never wired into the
serve path and has been deleted, so the D7 argument for caching SQL text rather
than results is history, not a description of the code.

TTLs, gates and route budgets are design targets in ``docs/architecture.md`` §7;
they are not knobs on ``Settings`` today.
"""

from __future__ import annotations

from .store import (
    InMemoryWorkingMemory,
    Turn,
    WorkingMemory,
)

__all__ = [
    "InMemoryWorkingMemory",
    "Turn",
    "WorkingMemory",
]
