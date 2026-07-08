"""Memory stores. Working memory is the one universal win; the durable stores
are off until the BIRD eval earns them per-domain (D8).

Dev backing = SQLite/files; prod = Postgres + pgvector (a config flip).
"""

from __future__ import annotations

from typing import Protocol


class WorkingMemory(Protocol):
    """Verbatim per-session context (checkpointer). Ephemeral, identity-scoped."""

    def append(self, session_id: str, role: str, content: str) -> None: ...
    def history(self, session_id: str) -> list[tuple[str, str]]: ...
    def clear(self, session_id: str) -> None: ...


# Profile / Episodic / Correction stores are deferred (off by default, D8).
# They arrive as gated projections of the corpus when eval justifies them.
