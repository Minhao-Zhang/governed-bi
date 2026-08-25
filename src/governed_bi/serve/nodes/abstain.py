"""``abstain`` — the graph adapter for the declared abstention policy (ADR 0013).

**The seam, and only that.** The policy itself — its rules, its evidence, its verdict and the
argument for every one of them — is :mod:`governed_bi.serve.abstention`, which takes
:class:`~governed_bi.serve.abstention.AbstentionInputs` and never sees a state dict. This module
is what LangGraph needs in order to run it.

**Why the adapter is the interesting half.** ``(state, config) -> dict`` is the signature, and
that signature is the problem this split is about: ``ServeState`` has 47 channels, so "which of
them does this node read, which does it write, and what clears them" is nowhere in it and has to
be recovered by reading the body. That is not a small interface, it is an unwritten one — and
``measure/gates.py`` reading ``Outcome.clarification`` as a witness of "reached stamp" is what it
costs when a reader guesses wrong. So the node's whole job is to be the seam: project the state
dict into ``AbstentionInputs``, call ``apply_policy``, and turn the ``AbstentionPatch`` it gets
back into the dict the graph expects.

**Registered ``stream=False``** in ``graph.py``, so a disabled policy adds no timeline rows; it
emits its own single row when it judged something.

**A failure here is a crashed turn, not a silent answer.** No ``try`` swallows anything: unlike
``reflect``, this node decides, and a policy that fails open on its own exception is a policy
that stops being one exactly when something is wrong. ``wrap_node`` records the crash.
"""

from __future__ import annotations

from langchain_core.runnables import RunnableConfig

from governed_bi.register.stages import Stage
from governed_bi.serve.abstention import AbstentionInputs, apply_policy
from governed_bi.serve.events import emit, rail_event_id
from governed_bi.serve.state import TERMINAL_PATH_KINDS

__all__ = ["abstain_node"]


def abstain_node(state: dict, config: RunnableConfig) -> dict:
    """Decide whether this turn should be answered, before the agent spends an attempt.

    **The adapter, and only that.** LangGraph requires ``(state, config) -> dict``, so this is
    where the 47-channel dict is turned into the eight facts
    :class:`~governed_bi.serve.abstention.AbstentionInputs` declares, and where the
    :class:`~governed_bi.serve.abstention.AbstentionPatch` that comes back is turned into an
    update and a rail row. It takes no *policy* decision —
    :func:`~governed_bi.serve.abstention.apply_policy` does — so every branch below is either a
    translation of one, or the already-ended short-circuit every node in this graph has, which
    is the one judgement that genuinely belongs at the seam.

    Declares ``config`` so :func:`~governed_bi.serve.wrap.wrap_node` forwards it. The knob is
    read through :func:`~governed_bi.serve.runtime.bool_knob`, whose precedence is state, then
    ``knobs_resolved``, then the register — inside the projection, which is the one reader of
    state in the policy.

    **The two channels the projection does not carry**, read here and named here: ``path_kind``
    for the short-circuit below, and ``turn_id`` — through
    :func:`~governed_bi.serve.events.rail_event_id` — to key the rail row.
    """
    # Before the projection, and this order is load-bearing: `bool_knob` raises on a
    # non-boolean knob, and a turn that already ended must not be crashed by a policy that was
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
            detail={"policy": patch.verdict["policy"], "reason": patch.verdict["reason"]},
        )
    if patch.path_kind is None:
        return {"abstention": patch.verdict}
    return {
        "abstention": patch.verdict,
        "path_kind": patch.path_kind,
        # The reason **is** the terminal reason. One string, in one vocabulary, in the channel
        # `route` and `connect` already write their declines into — rather than a second field
        # only a new reader would know to open. Its reader is
        # `eval/report.py::refusal_histogram`, which had to be built: ADR 0013 §2 claimed three
        # existing ones and none of them read the vocabulary.
        "terminal_reason": patch.verdict["reason"],
    }
