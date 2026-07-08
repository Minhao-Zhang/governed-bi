"""Memory stores. Working memory is the one universal win; the durable stores
are off until the BIRD eval earns them per-domain (D8).

Dev backing = in-memory / SQLite / files; prod = Postgres + pgvector (a config
flip). This module ships the concrete working-memory store used in dev plus the
protocol seams for the durable stores (Episodic / Correction), which stay
unimplemented by design: more memory often hurts, so they are adopted per-domain
only when eval justifies them, and when adopted they are PR-gated exactly like
the corpus.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

# A single conversational turn: (role, content), e.g. ("user", "total revenue?").
Turn = tuple[str, str]


@runtime_checkable
class WorkingMemory(Protocol):
    """Verbatim per-session context (checkpointer). Ephemeral, identity-scoped."""

    def append(self, session_id: str, role: str, content: str) -> None: ...
    def history(self, session_id: str) -> list[Turn]: ...
    def clear(self, session_id: str) -> None: ...


class InMemoryWorkingMemory:
    """Process-local working memory: verbatim turns per session (D8).

    Ephemeral by design (lost on restart) and **session-scoped**, which is the
    identity scope: the server mints one session per acting identity (D7), so a
    session id never spans users. ``max_turns`` optionally caps a session's
    history to the most recent N turns to bound growth; ``None`` keeps all turns.

    This is the store the ``before_model`` middleware reads to inject prior
    context; the durable Episodic / Correction stores are deferred (see below).
    """

    def __init__(self, *, max_turns: int | None = None) -> None:
        if max_turns is not None and max_turns <= 0:
            raise ValueError("max_turns must be positive or None")
        self._max_turns = max_turns
        self._sessions: dict[str, list[Turn]] = {}

    def append(self, session_id: str, role: str, content: str) -> None:
        turns = self._sessions.setdefault(session_id, [])
        turns.append((role, content))
        if self._max_turns is not None and len(turns) > self._max_turns:
            # Keep only the most recent max_turns entries.
            del turns[: len(turns) - self._max_turns]

    def history(self, session_id: str) -> list[Turn]:
        # A copy, so callers cannot mutate the stored history in place.
        return list(self._sessions.get(session_id, ()))

    def clear(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)


# --------------------------------------------------------------------------- #
# Durable stores (off by default, D8): protocol seams only.
#
# These arrive as gated projections of the corpus when eval justifies them:
# correction memory is correction-harvesting -> PR to a reference doc; promoted
# episodic memory is a gated few-shot. One PR-gated corpus, not two governance
# models, so there is no separate durable store to implement until then.
# --------------------------------------------------------------------------- #


@runtime_checkable
class EpisodicMemory(Protocol):
    """Past question -> outcome recall, identity-scoped and value-aware (D8).

    Off by default; a promoted episode becomes a gated ``few_shot`` asset in the
    corpus rather than a parallel store.
    """

    def recall(self, identity: str, question: str, *, limit: int = 5) -> list[Turn]: ...
    def record(self, identity: str, question: str, outcome: str) -> None: ...


@runtime_checkable
class CorrectionMemory(Protocol):
    """Human corrections, identity-scoped (D8).

    Off by default; a harvested correction becomes a PR to a reference doc /
    corpus asset rather than a parallel store.
    """

    def recall(self, identity: str, question: str, *, limit: int = 5) -> list[Turn]: ...
    def record(self, identity: str, question: str, correction: str) -> None: ...
