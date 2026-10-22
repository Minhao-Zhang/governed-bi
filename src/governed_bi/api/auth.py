"""No credential (ADR 0007). ``authenticate`` admits everyone, or LangGraph runs no ``@auth.on`` handler.
Loopback is the boundary (Studio sends no custom headers).
``update`` and ``goto`` write ``ServeState``; ``resume`` passes.
"""

from __future__ import annotations

from collections.abc import Mapping

from langgraph_sdk import Auth

__all__ = ["auth"]

auth = Auth()

_LOCAL_IDENTITY = "governed-bi-local"


@auth.authenticate
async def _authenticate() -> Auth.types.MinimalUserDict:
    """Admit every caller. It reads nothing, so a signature that suggested otherwise would lie."""
    return {"identity": _LOCAL_IDENTITY, "permissions": []}


# ``type: ignore``: the SDK types ``value`` as ``ThreadsUpdate``, but the runtime nests the
# payload where that model does not. ``dict`` is deliberate; narrowing it reopens A4.
@auth.on.threads.update  # type: ignore[arg-type]
async def _no_state_writes(ctx: Auth.types.AuthContext, value: dict) -> None:
    """Deny thread-state writes. A filter would only choose which thread.
    Forging tool bounds is unacceptable on any of them."""
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
    """Nested under ``kwargs`` (``RunsCreate``), not ``value["command"]``. Both sites are read.
    An unreadable shape, including ciphertext, is refused: failing open allowed every request (A4)."""
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


#: ``update`` and ``goto`` write state: ``map_command`` emits every key with no input-schema check.
#: ``resume`` stays, because it answers ``ask_user``.
_STATE_WRITING_COMMANDS = ("update", "goto")


# Same ``type: ignore`` as the state hook: ``RunsCreate`` is not the shape the runtime hands over.
@auth.on.threads.create_run  # type: ignore[arg-type]
async def _no_state_writes_on_a_new_run(ctx: Auth.types.AuthContext, value: dict) -> None:
    """``create_run`` stayed allowed after the state route closed. ``resume`` still passes."""
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
