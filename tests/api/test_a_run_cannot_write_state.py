"""Auth denials go through ``handle_event``, the path a request takes.

Calling the handler directly stayed green while it read ``value["command"]`` and the runtime nests
the command under ``kwargs``. An unregistered action is fail-open: ``_get_handler`` returns ``None``
and ``handle_event`` allows it.
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
    "retrieved_tables": ["public.salaries"],
    "corpus_content_hash": "forged",
}


def _event(action: str, value: object) -> None:
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
    # The shape `langgraph_runtime_inmem/ops.py` builds: the command is nested under `kwargs`.
    kwargs: dict = {"input": {"question": "how many"}, "config": {}, "context": {}}
    if command is not None:
        kwargs["command"] = command
    _event("create_run", {"thread_id": "t-1", "assistant_id": "a-1", "kwargs": kwargs})


def test_the_runtime_dispatch_refuses_a_forged_retrieved_tables_and_corpus_hash() -> None:
    """Widening ``retrieved_tables`` and resuming would run SQL the corpus never retrieved."""
    with pytest.raises(Exception) as caught:
        _dispatch({"update": FORGED})
    assert getattr(caught.value, "status_code", None) == 403, caught.value
    assert "command." in str(getattr(caught.value, "detail", caught.value))


def test_the_runtime_dispatch_refuses_a_goto() -> None:
    """``map_cmd`` treats ``goto`` as a state write, the same as ``update``."""
    with pytest.raises(Exception) as caught:
        _dispatch({"goto": "check"})
    assert getattr(caught.value, "status_code", None) == 403


def test_the_runtime_dispatch_still_allows_a_resume() -> None:
    """A blanket deny would delete ``ask_user``, which answers with ``command.resume``."""
    _dispatch({"resume": "the sales schema"})
    _dispatch(None)


def test_a_command_shape_this_hook_cannot_read_is_refused_not_allowed() -> None:
    """Request encryption delivers ``command`` as ciphertext, and returning early would allow it."""
    with pytest.raises(Exception) as caught:
        _dispatch("Z0FBQUFBQm9wYXFl")  # type: ignore[arg-type]
    assert getattr(caught.value, "status_code", None) == 403


def test_the_runtime_dispatch_refuses_a_thread_state_write() -> None:
    """``as_node`` bypasses the input schema, so this route writes ``ServeState`` directly."""
    with pytest.raises(Exception) as caught:
        _event(
            "update",
            {"thread_id": "t-1", "values": FORGED, "as_node": "accept"},
        )
    assert getattr(caught.value, "status_code", None) == 403, caught.value
    assert "as_node" in str(getattr(caught.value, "detail", caught.value))


def test_both_handlers_are_registered() -> None:
    """An unregistered action is fail-open, and every other test here only asserts a refusal."""
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
