"""The state-write denial. **No credential is required to reach this engine.**

Wired by ``langgraph.json``'s ``auth.path``. ``@auth.authenticate`` admits every caller with one
constant identity: without it LangGraph Server runs no ``@auth.on`` handler, and the denials go.
No shared key (Studio's fetches carry no custom headers): the ``127.0.0.1`` binding is the control.

The denials are about payloads, not callers: ``threads.update`` and a run carrying
``command.update`` or ``command.goto`` would write ``ServeState`` (widening ``retrieved_tables``, forging
``corpus_content_hash``), so they are refused; ``command.resume`` passes.
"""

from __future__ import annotations

from collections.abc import Mapping

from langgraph_sdk import Auth

__all__ = ["auth"]

auth = Auth()

#: The one identity every request carries. Nothing reads it.
_LOCAL_IDENTITY = "governed-bi-local"


@auth.authenticate
async def _authenticate() -> Auth.types.MinimalUserDict:
    """Admit every caller. It reads nothing, so a signature that suggested otherwise would lie."""
    return {"identity": _LOCAL_IDENTITY, "permissions": []}


# ``type: ignore`` on the decorator, not a signature change: the SDK's ``_ActionHandler``
# protocol types ``value`` as ``ThreadsUpdate``, and reading it at that type is the defect
# ``_command_of`` exists to document — the runtime nests the real payload somewhere the typed
# model does not describe, and the first version of that function trusted the shape and allowed
# every request. ``dict`` here is deliberate width, and the ``isinstance`` check below is what
# replaces the type. Narrowing to satisfy mypy would reopen A4.
@auth.on.threads.update  # type: ignore[arg-type]
async def _no_state_writes(ctx: Auth.types.AuthContext, value: dict) -> None:
    """Deny ``POST /threads/{id}/state`` and ``PATCH /threads/{id}``.

    The action name is what ``ops.Threads.State.post`` passes to ``handle_event`` ("update"), so
    this is the hook that sits in front of the write. It also covers thread-metadata patches,
    which nothing in this system performs.

    A denial rather than a filter: a filter would restrict *which* threads may be written, and
    the finding is not about ownership. `ServeState` is the engine's own record — `retrieved_tables`,
    `settings_resolved`, `corpus_content_hash` — and a client that can set those can forge an
    audit record and widen the tool bounds the layer stack enforces against. There is no
    value of "which thread" that makes that acceptable.
    """
    raise Auth.exceptions.HTTPException(
        status_code=403,
        detail=(
            "thread state is not writable over HTTP. It carries the tool bounds the layer "
            "stack enforces against and the corpus hash every quotability check reads; a "
            "client-supplied as_node bypasses the graph's input schema entirely (audit A2/A3). "
            "State is written from inside the graph."
        ),
    )

def _command_of(value: object) -> dict | None:
    """The ``command`` a run-creation event carries, from **where the runtime puts it**.

    ``None`` means there is none to inspect. Anything unexpected **raises**, which is the whole
    lesson of this function: the first version read ``value["command"]`` and returned early when it
    found nothing, so it allowed every request. ``langgraph_api`` nests it —
    ``models/run.py`` hands ``command`` to ``Runs.put`` inside the third positional argument and
    ``langgraph_runtime_inmem/ops.py`` wraps that whole dict as ``RunsCreate(kwargs=kwargs)``. The
    real value is ``{"thread_id": …, "assistant_id": …, "kwargs": {"input": …, "command": {…}}}``.
    Verified end to end: the exact payload the audit row claimed was refused was accepted, and the
    forged ``retrieved_tables`` reached the stored run. The gRPC/Postgres runtime nests it identically.

    **A security check that fails open on a shape it did not expect is how that happened**, so a
    ``command`` present under a type this cannot read is a refusal rather than a pass. That covers
    the case ``models/run.py`` creates when request encryption is on: ``command`` is then ciphertext
    rather than a dict, and returning early there would silently reopen this.

    Both locations are read, newest first, so a future version moving the key back to the top level
    does not reopen it either. What no reading of a payload can protect is the key being *renamed*;
    the end-to-end test is what covers that.
    """
    if not isinstance(value, Mapping):
        raise Auth.exceptions.HTTPException(
            status_code=403,
            detail=(
                f"run creation received {type(value).__name__}, which this authorisation hook "
                "cannot inspect for a state-writing command. Refused rather than allowed: a check "
                "that fails open on an unexpected shape is not a check (audit A4)."
            ),
        )
    for holder in (value.get("kwargs"), value):
        if not isinstance(holder, Mapping) or "command" not in holder:
            continue
        command = holder["command"]
        if command is None:
            return None
        if not isinstance(command, Mapping):
            raise Auth.exceptions.HTTPException(
                status_code=403,
                detail=(
                    f"run creation carries a command of type {type(command).__name__}, which this "
                    "hook cannot read — request encryption is the likely cause. Refused rather "
                    "than allowed, because `command.update` writes the tool bounds the layer stack "
                    "enforces against (audit A4)."
                ),
            )
        return dict(command)
    return None


#: Keys of a run-creation ``command`` that write state instead of resuming one.
#:
#: ``resume`` is the whole point of the paused-turn protocol and must stay: ``ask_user`` interrupts
#: and the client answers. ``update`` and ``goto`` are the two that write, and LangGraph applies
#: ``update`` through ``map_command``, which — unlike ``map_input`` — emits a write for **every** key
#: it is handed with no reference to the graph's input schema.
_STATE_WRITING_COMMANDS = ("update", "goto")


# Same ``type: ignore`` as the state hook above, same reason: ``RunsCreate`` is not the shape
# the runtime hands this hook, and ``_command_of`` reads both locations precisely because the
# typed model is wrong about where ``command`` lives.
@auth.on.threads.create_run  # type: ignore[arg-type]
async def _no_state_writes_on_a_new_run(ctx: Auth.types.AuthContext, value: dict) -> None:
    """Deny ``POST /threads/{id}/runs`` when it carries a state-writing ``command`` (audit A4).

    **The same defect as A2/A3, through a door that closing them left open.** Those closed
    ``POST /threads/{id}/state`` and the ``as_node`` write behind it; this is run creation, which
    dispatches ``("threads", "create_run")`` — an action no handler covered, so
    ``langgraph_api``'s default allowed it. The payload
    ``{"command": {"update": {"retrieved_tables": ["public.salaries"], "corpus_content_hash": "forged"}}}``
    therefore reached ``map_command``, which writes every key it is given.

    Both halves of that example matter and neither is a nuisance: ``retrieved_tables`` is the tool bound the
    layer stack enforces against, so widening it and then resuming executes SQL against tables the
    corpus never retrieved_tables — the model does not need a database handle if the caller can hand it one.
    And ``corpus_content_hash`` is the setting under test identity every quotability check reads, so a forged
    one finishes an audit record with a corpus the run never served.

    A denial and not a filter, for the reason the state hook gives: there is no value of "which
    thread" that makes forging the engine's own record acceptable. ``resume`` passes, because
    answering a clarification is what this endpoint is for.
    """
    command = _command_of(value)
    if command is None:
        return
    offending = sorted(k for k in _STATE_WRITING_COMMANDS if command.get(k) is not None)
    if not offending:
        return
    raise Auth.exceptions.HTTPException(
        status_code=403,
        detail=(
            f"command.{{{', '.join(offending)}}} is not accepted on run creation. `update` and "
            "`goto` write thread state, which carries the tool bounds the layer stack enforces "
            "against and the corpus hash every quotability check reads (audit A4, the same defect "
            "as A2/A3 through a different route). `command.resume` is accepted — answering a "
            "paused turn is what this endpoint is for. State is written from inside the graph."
        ),
    )
