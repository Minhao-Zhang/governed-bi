"""Identity-bound clarification resume (ADR 0006 B9 / ADR 0005 HITL).

**The gate has to fire inside the graph, because the platform resume never asks the HTTP layer.**
``POST /chat/resume`` used to be the only way to answer an ``ask_user`` interrupt, and
:func:`resume_clarification` compared identities before issuing ``Command(resume=)``. LangGraph
Server does not go through it: the client posts a run with ``{"command": {"resume": …}}`` and the
runtime applies the resume to the pending interrupt directly. Retiring ``/chat/resume`` would
therefore have deleted B9 rather than moved it.

**``api/auth.py`` cannot hold it, and the reason is what an auth handler is handed.** It sees the
right *request*: ``@auth.on.threads.create_run`` receives ``Auth.types.RunsCreate``, so
``kwargs.command.resume`` and ``thread_id`` are both readable — ``auth.py::_command_of`` already
reads the first. What it does not receive is any way to read the *thread*.
``Auth.types.AuthContext`` carries four fields — ``permissions``, ``user``, ``resource``,
``action`` — and the checkpointed ``identity`` this gate compares against is graph state.
Reaching it means ``langgraph_runtime…ops.Threads.State.get(conn, config, …)``, which takes a
runtime connection the handler does not have and calls ``langgraph_api.graph.get_graph`` to
instantiate the compiled graph — while nested inside the ``Runs.put`` that dispatched the hook,
and while ``Threads.get`` would re-enter ``handle_event`` for ``threads.read``. There is no
supported reading of the auth surface that answers "who was asked". And a blanket refusal is not
available either: ``command.resume`` is exactly what the paused-turn protocol needs allowed.

So the enforcement point is :func:`authorise_resume`, called by ``serve/tools.py``'s ``ask_user``
at the line ``interrupt()`` returns on. That is the first instruction in the process that has both
halves — the paused turn's checkpointed ``identity`` in ``state``, and the caller the transport
attached to the *resuming* run in ``config`` — and it covers every resume, not only the ones that
arrive as an HTTP run-create.
"""

from __future__ import annotations

from typing import Any, Mapping

from langgraph.types import Command

from governed_bi.govern.bounds import resume_authorised
from governed_bi.serve.runtime import configurable

__all__ = [
    "CALLER_KEY",
    "ResumeRejected",
    "authorise_resume",
    "caller_identity",
    "resume_clarification",
    "identity_token",
]


#: ``configurable`` key naming the caller that posted **this** run.
#:
#: **The only slot this module will read, and forgeability is the whole reason.**
#: ``langgraph_api/models/run.py`` writes it from ``get_auth_ctx()`` — i.e. from whatever
#: ``api/auth.py``'s ``@auth.authenticate`` returned — *after* the client's own ``config`` and
#: ``context`` have been merged into ``configurable``, so a request cannot win the merge. It is
#: also in ``langgraph_api/validation.py::RESERVED_CONFIGURABLE_KEYS``, which rejects any
#: run / assistant / cron write that names it, and ``POST /threads`` carries no ``config`` at all,
#: so a thread cannot smuggle one in for later runs to inherit.
#:
#: A serve-owned key (``"caller_identity"``, say) would be writable by any HTTP client, and this
#: gate compares the caller against the *victim's* stored identity — so a caller who can name
#: themselves can name the victim and pass. Reading one unforgeable slot is the control; reading
#: a second, forgeable one would delete it.
CALLER_KEY = "langgraph_auth_user_id"


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


def caller_identity(config: Mapping[str, Any] | None) -> str | None:
    """The caller the transport authenticated for this run, or ``None``.

    ``None`` is not "anyone": :func:`resume_authorised` refuses it against every stored identity
    including another ``None``, so a run that reaches the graph with nobody named cannot answer a
    paused clarification. That is why the two transports both fill :data:`CALLER_KEY` —
    LangGraph Server from its auth context, :func:`resume_clarification` from its ``identity``
    argument — rather than each inventing a place to say who is calling.
    """
    return identity_token(configurable(config).get(CALLER_KEY))


def authorise_resume(state: Mapping[str, Any] | None, config: Mapping[str, Any] | None) -> None:
    """Raise :class:`ResumeRejected` unless this run's caller is the one that was asked (B9).

    Called from inside ``ask_user`` on the instruction after ``interrupt()`` returns, which on the
    initial pass raises and so never reaches it: this runs on resumes only. ``state`` is the
    paused turn's checkpoint and ``config`` belongs to the run applying the resume, so the two
    tokens come from different requests — which is the whole content of the check.

    **It raises rather than returning a refusal to the model.** A tool reply would hand the turn
    to the caller who was not asked and let it answer; a raise ends it. ``langgraph``'s
    ``_default_handle_tool_errors`` re-raises anything that is not a ``ToolInvocationError``, so
    this leaves ``agent_core`` as ``error_type="ResumeRejected"`` with no clarification recorded
    and the answer text nowhere — ``interrupt()``'s return value is dropped on the floor.

    What it cannot undo is that the resume was already *consumed*: the platform writes it into
    the checkpoint before the task re-runs, so a caller who guesses a ``thread_id`` can destroy
    someone else's pending question even though they can never read or answer it. B9 says
    namespacing a thread id is a mitigation and not authentication; this is the residue of that,
    and it is a denial of service rather than a disclosure.
    """
    stored = identity_token((state or {}).get("identity"))
    caller = caller_identity(config)
    if not resume_authorised(stored_identity=stored, caller_identity=caller):
        raise ResumeRejected(
            "resume identity mismatch: the caller answering this clarification is not the "
            "caller that was asked (ADR 0006 B9). An absent identity on either side is a "
            "mismatch — two unknowns are not the same person."
        )


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

    The pre-check stays, and it also publishes ``identity`` into :data:`CALLER_KEY` so
    :func:`authorise_resume` inside the graph sees the same caller LangGraph Server would have
    named. Without that this in-process transport would fail the in-graph gate — correctly, since
    a run with nobody named may not answer a question — and the two checks would disagree about
    the same call.
    """
    cfg = dict(config)
    conf = dict(configurable(cfg))
    caller = identity_token(identity)
    if caller is not None:
        conf[CALLER_KEY] = caller
    cfg["configurable"] = conf

    stored = _stored_identity(graph, cfg)
    if not resume_authorised(stored_identity=stored, caller_identity=caller):
        raise ResumeRejected("resume identity mismatch")
    return graph.invoke(Command(resume=answer), cfg)


def _stored_identity(graph: Any, config: Mapping[str, Any]) -> str | None:
    snap = graph.get_state(config)
    values = getattr(snap, "values", None) or {}
    if isinstance(values, Mapping):
        return identity_token(values.get("identity"))
    return None
