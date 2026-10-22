"""Graph adapter for the abstention policy. The policy is :mod:`governed_bi.serve.abstention`.

Registered ``stream=False``, so a disabled policy adds no timeline rows. No ``try``: a policy
that fails open stops being one when something is wrong, and ``wrap_node`` records the crash.
See ADR 0013.
"""

from __future__ import annotations

from langchain_core.runnables import RunnableConfig

from governed_bi.registry.stages import Stage
from governed_bi.serve.abstention import AbstentionInputs, apply_policy
from governed_bi.serve.events import emit, rail_event_id
from governed_bi.serve.state import TERMINAL_PATH_KINDS

__all__ = ["abstain_node"]


def abstain_node(state: dict, config: RunnableConfig) -> dict:
    """Project state, call :func:`~governed_bi.serve.abstention.apply_policy`, write the update.

    Declares ``config`` so the wrapper forwards it. Reads ``path_kind`` and ``turn_id`` here;
    every other fact goes through :class:`~governed_bi.serve.abstention.AbstentionInputs`.
    """
    # Before the projection, and this order is: `bool_setting` raises on a
    # non-boolean setting, and a turn that already ended must not be crashed by a policy that was
    # never going to run on it.
    if state.get("path_kind") in TERMINAL_PATH_KINDS:
        return {}

    patch = apply_policy(AbstentionInputs.from_state(state))
    if patch.rail_status is not None:
        emit(
            kind="rail",
            step=Stage.abstain.value,
            status=patch.rail_status,
            event_id=rail_event_id(Stage.abstain.value, state),
            detail={"policy": patch.result["policy"], "reason": patch.result["reason"]},
        )
    if patch.path_kind is None:
        return {"abstention": patch.result}
    return {
        "abstention": patch.result,
        "path_kind": patch.path_kind,
        # Same channel `route` and `connect` use, so a decline has one vocabulary. See ADR 0013.
        "terminal_reason": patch.result["reason"],
    }
