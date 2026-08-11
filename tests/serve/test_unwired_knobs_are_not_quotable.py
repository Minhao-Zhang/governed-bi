"""A turn whose knobs were never wired must not be quotable.

Found by the 2026-08-10 audit (C5). ``stamp`` substituted ``{}`` for an absent
``knobs_resolved``, and ``{}`` is the one value ``measure.gates`` names as the thing it must
never be handed: it is a ``Mapping``, so the drift gate reads it as a real configuration in
which all 54 declared keys resolved to ``None``. Every row then shares one signature, and an
arm of empties **passes**.

Two comments in the tree already forbade this and neither was enforced —
``gates.py::_knobs_resolved_gate`` ("Absent ``knobs_resolved`` is unmeasured, not passing … one
arm of empties would *pass* the gate") and ``eval/harness.py`` ("absent stays absent"). The
writer at the other end of the pipe defeated both, which is why the test lives here rather than
in ``tests/measure``: pinning the gate alone would leave the substitution free to come back.

Paired, per this parcel's authoring rule — the ``{}`` case is kept precisely to show the gate
*would* pass it, so the assertion below cannot be satisfied by the gate simply refusing
everything.
"""

from __future__ import annotations

from typing import Any

from governed_bi.measure import gates
from governed_bi.measure.population import Population
from governed_bi.serve.nodes.stamp import stamp

_EMPTY_EXECUTION: dict[str, Any] = {
    "attempts": [],
    "terminal": "no_sql",
    "guardrail_errors": 0,
}


def _stamp_without_knobs() -> dict[str, Any]:
    """A turn where nothing ever wrote ``knobs_resolved`` — the wiring failure under test."""
    out = stamp(
        {
            "path_kind": "answered",
            "execution": dict(_EMPTY_EXECUTION),
            "generated_sql": None,
            "turn_id": "turn-unwired",
            "turn_index": 1,
            "usage": [],
        }
    )
    return out["answer"]["record"]


def _arm(value: Any) -> Population:
    return Population.of("arm", [{"question_id": f"q{i}", "knobs_resolved": value} for i in range(3)])


def test_stamp_does_not_substitute_an_empty_configuration() -> None:
    record = _stamp_without_knobs()

    assert record["knobs_resolved"] != {}, (
        "stamp substituted `{}` for an absent knobs_resolved. That is not a configuration, "
        "it is the absence of one, and measure.gates cannot tell the difference: `{}` is a "
        "Mapping, so every row's drift signature matches and the arm reports quotable."
    )
    assert record["knobs_resolved"] is None


def test_an_unwired_arm_cannot_be_evaluated_while_an_empty_one_would_pass() -> None:
    """The consequence, and the reason the substitution mattered rather than merely being untidy."""
    unwired = gates._knobs_resolved_gate(_arm(_stamp_without_knobs()["knobs_resolved"]))
    substituted = gates._knobs_resolved_gate(_arm({}))

    assert unwired.verdict is not substituted.verdict, (
        "the gate gives absent and empty the same verdict, so the substitution this file "
        "guards against would be undetectable here"
    )
    assert unwired.verdict.name == "cannot_evaluate", unwired.verdict
    # Not an endorsement — this is the hazard, pinned so the pair above stays meaningful.
    assert substituted.verdict.name == "passed", substituted.verdict
