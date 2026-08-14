"""Audits A2/A3 and A4, through the runtime's own dispatch rather than by calling the handler.

**This file exists because the first A4 test called the handler function directly.** It passed while
the handler was dead: it read ``value["command"]`` and ``langgraph_api`` puts the command at
``value["kwargs"]["command"]``, so every real request returned early and was allowed. The test, both
declared mutations and the audit row all agreed the hole was closed, because none of them touched the
path a request takes. Deleting the ``@auth.on.threads.create_run`` decorator outright also left that
test green.

So this one goes through ``Runs.put``, which is what ``POST /threads/{id}/runs`` calls, with the real
``AuthContext`` and the committed ``auth.py`` loaded the way the server loads it. It needs no port, no
model, no corpus and no Postgres — the in-memory runtime is enough.

Every route that can apply a ``command`` funnels through this one action: ``POST /threads/{id}/runs``,
``/runs/stream``, ``/runs/wait``, the threadless ``POST /runs`` family, ``/runs/batch``, the cron
scheduler and the v2 event-streaming path. One dispatch, so one test covers them.

**A2/A3 — the ``threads.update`` denial — is here too, and it had no test at all until
2026-08-13.** It was written and audited alongside A4 and nothing in the suite ever dispatched
against it, so deleting ``@auth.on.threads.update`` was a green diff: ``_get_handler`` returns
``None`` on no match and ``handle_event`` treats that as *allow*, which is the same fail-open
shape the A4 story above is about. It became load-bearing when the transport key was removed,
since the key was the other thing standing between an arbitrary local caller and
``POST /threads/{id}/state``.
"""

from __future__ import annotations

import os

import pytest

# `langgraph_api.config` reads the environment at import and `get_auth_instance` is `lru_cache`d, so
# this has to be set before the first `langgraph_api` import. Hence its own module.
os.environ.setdefault("LANGGRAPH_RUNTIME_EDITION", "inmem")
os.environ.setdefault("REDIS_URI", "")
os.environ.setdefault("DATABASE_URI", "")
os.environ["LANGGRAPH_AUTH"] = '{"path": "./src/governed_bi/api/auth.py:auth"}'

FORGED = {
    "licensed": ["public.salaries"],
    "corpus_content_hash": "forged",
}


def _event(action: str, value: object) -> None:
    """Put one event through the real auth dispatch. Raises the SDK's 403 when refused."""
    import asyncio

    from langgraph_api.auth.custom import handle_event
    from langgraph_sdk import Auth

    # `MinimalUser` is a Protocol and cannot be instantiated; the dispatch only reads attributes.
    class _User:
        identity = "test"
        display_name = "test"
        is_authenticated = True

        def __getitem__(self, key):  # some paths treat the user as a mapping
            return getattr(self, key)

    ctx = Auth.types.AuthContext(
        permissions=[],
        user=_User(),  # type: ignore[arg-type]
        resource="threads",
        action=action,
    )
    asyncio.run(handle_event(ctx, value))


def _dispatch(command: dict | None) -> None:
    """Put a run creation through the real auth dispatch."""
    # The shape `langgraph_runtime_inmem/ops.py` builds: the command is nested under `kwargs`.
    kwargs: dict = {"input": {"question": "how many"}, "config": {}, "context": {}}
    if command is not None:
        kwargs["command"] = command
    _event("create_run", {"thread_id": "t-1", "assistant_id": "a-1", "kwargs": kwargs})


def test_the_runtime_dispatch_refuses_a_forged_licensed_and_corpus_hash() -> None:
    """The payload the audit row claimed was refused, through the path a request takes.

    ``licensed`` is the bound ``govern/bounds.py`` enforces against, so widening it and resuming
    executes SQL against tables the corpus never licensed. ``corpus_content_hash`` is the treatment
    identity every quotability gate reads.
    """
    with pytest.raises(Exception) as caught:
        _dispatch({"update": FORGED})
    assert getattr(caught.value, "status_code", None) == 403, caught.value
    assert "command." in str(getattr(caught.value, "detail", caught.value))


def test_the_runtime_dispatch_refuses_a_goto() -> None:
    """``goto`` writes too — ``langgraph_api/command.py``'s ``map_cmd`` reads goto/update/resume."""
    with pytest.raises(Exception) as caught:
        _dispatch({"goto": "check"})
    assert getattr(caught.value, "status_code", None) == 403


def test_the_runtime_dispatch_still_allows_a_resume() -> None:
    """The paused-turn protocol, which a blanket deny would have deleted.

    ``ask_user`` interrupts and the client answers — ``ui/hooks/use-stream-chat.ts``
    submits ``{command: {resume: response}}``.
    """
    _dispatch({"resume": "the sales schema"})
    _dispatch(None)


def test_a_command_shape_this_hook_cannot_read_is_refused_not_allowed() -> None:
    """Fail **closed** on an unexpected shape, because failing open is how A4 survived a fix.

    With request encryption on, ``models/run.py`` hands ``command`` through ``encrypt_request``, so it
    arrives as ciphertext rather than a mapping. Returning early there would silently reopen this.
    """
    with pytest.raises(Exception) as caught:
        _dispatch("Z0FBQUFBQm9wYXFl")  # type: ignore[arg-type]
    assert getattr(caught.value, "status_code", None) == 403


def test_the_runtime_dispatch_refuses_a_thread_state_write() -> None:
    """Audit A2/A3, through the dispatch ``ops.Threads.State.post`` uses.

    ``POST /threads/{id}/state`` forwards the caller's ``as_node`` into ``graph.aupdate_state``,
    and ``as_node`` is what bypasses ``input_schema``: ``as_node="accept"`` writes ``ServeState``
    directly, widening ``ToolBounds.licensed`` and forging ``corpus_content_hash``. The denial is
    unconditional, so the payload here is the one the audit measured rather than an edge case.

    ``PATCH /threads/{id}`` dispatches the same ``"update"`` action, so it is covered by this.
    """
    with pytest.raises(Exception) as caught:
        _event(
            "update",
            {"thread_id": "t-1", "values": FORGED, "as_node": "accept"},
        )
    assert getattr(caught.value, "status_code", None) == 403, caught.value
    assert "as_node" in str(getattr(caught.value, "detail", caught.value))


def test_both_handlers_are_actually_registered() -> None:
    """The wiring, asserted separately — deleting a decorator left the old A4 test green.

    ``langgraph_api``'s ``_get_handler`` returns ``None`` when nothing matches and ``handle_event``
    treats that as **allow**, so an unregistered action is fail-open and silent. Nothing else in
    the suite would notice: every other test here asserts a *refusal*, and an unregistered action
    simply stops refusing.

    Both actions, because they are the whole of what this module protects now that no credential
    is required to reach the port.
    """
    from langgraph_api.auth.custom import get_auth_instance

    instance = get_auth_instance()
    assert instance is not None, "the server would load no auth at all"
    registered = {
        key for key in getattr(instance, "_handlers", {})
    }
    for action in ("create_run", "update"):
        assert ("threads", action) in registered, (
            f"threads.{action} has no handler, so it is fail-open: {sorted(registered)}"
        )
