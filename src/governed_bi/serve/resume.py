"""Identity-bound clarification resume (ADR 0006 B9 / ADR 0005 HITL).

**Not reachable from the real product's traffic (2026-08-10 finding, unfixed).** This
module's only caller anywhere in ``src/`` is ``POST /chat/resume`` in ``api/routes.py`` -- the
same "degradation path, streaming is the primary transport" route whose mining call
``serve/nodes/mine_corpus.py`` was relocated away from for the identical reason. The real
``governed-bi-ui`` resumes a paused ``ask_user`` interrupt through LangGraph Server's own
``/threads/{id}/runs/stream``, which invokes ``Command(resume=...)`` on the compiled graph
directly and never passes through ``routes.py``'s FastAPI app. Confirmed live: a native-path
turn never has ``identity`` populated in state at all (``api/graph_app.py``'s ``_accept_node``
never passes one to ``session.turn()``), and the native resume payload has no identity field
to check even in principle -- ``resume_authorised`` here is not being *bypassed* on that path,
it is simply never invoked, protecting only ``/chat/resume``'s own separate, ephemeral
``InMemorySaver()`` that no real caller ever touches.

Deliberately not relocated the way mining was: the one signal a graph node could compare
against (``config.configurable["langgraph_auth_user_id"]``) is only meaningful once a real
``Auth`` handler is registered in ``langgraph.json``. Under this project's current
``NoopAuthBackend`` config that field is ``""`` for every caller, so a node-level check would
compare ``""`` against ``""`` and pass for everyone -- a change that would look like a fix
while providing zero real protection. A genuine fix needs an actual authentication layer at
the LangGraph Server boundary (an identity provider, a registered ``Auth`` handler,
resource-scoped ``@auth.on.threads`` rules) -- a product decision (does a local-first SMB tool
need multi-caller thread isolation at all, and against what threat model) explicitly deferred
rather than improvised. Practical severity today: ``NoopAuthBackend`` plus no access control
on ``/threads/*`` at all means anyone who reaches the port and observes a ``thread_id`` (URL,
log, browser history) can resume any paused clarification or read any thread's state --
mitigated only by ``thread_id`` being a server-minted UUIDv7, not a purpose-built capability
token.
"""

from __future__ import annotations

from typing import Any, Mapping

from langgraph.types import Command

from governed_bi.govern.bounds import resume_authorised
from governed_bi.serve.runtime import configurable

__all__ = ["ResumeRejected", "resume_clarification", "identity_token"]


class ResumeRejected(PermissionError):
    """Caller identity does not match the paused turn's identity."""


def identity_token(identity: Any) -> str | None:
    """Reduce ``state.identity`` (str or mapping) to the compare token."""
    if identity is None:
        return None
    if isinstance(identity, str):
        return identity or None
    if isinstance(identity, Mapping):
        for key in ("token", "id", "subject", "user_id"):
            raw = identity.get(key)
            if raw:
                return str(raw)
        return None
    return str(identity) or None


def resume_clarification(
    graph: Any,
    *,
    config: Mapping[str, Any],
    identity: Any,
    answer: Any,
) -> Any:
    """Resume an ``ask_user`` interrupt after ``resume_authorised`` succeeds.

    Loads the checkpointed ``identity`` for this ``thread_id`` and compares it to
    ``identity`` with :func:`~governed_bi.govern.bounds.resume_authorised`. On
    mismatch raises :class:`ResumeRejected` and does **not** issue ``Command(resume=)``.
    """
    cfg = dict(config)
    conf = dict(configurable(cfg))
    cfg["configurable"] = conf

    stored = _stored_identity(graph, cfg)
    caller = identity_token(identity)
    if not resume_authorised(stored_identity=stored, caller_identity=caller):
        raise ResumeRejected("resume identity mismatch")
    return graph.invoke(Command(resume=answer), cfg)


def _stored_identity(graph: Any, config: Mapping[str, Any]) -> str | None:
    snap = graph.get_state(config)
    values = getattr(snap, "values", None) or {}
    if isinstance(values, Mapping):
        return identity_token(values.get("identity"))
    return None
