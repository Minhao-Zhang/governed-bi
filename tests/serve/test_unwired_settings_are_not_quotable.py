"""A turn whose settings were never wired must not be quotable.

Found by the 2026-08-10 audit (C5). ``finish`` substituted ``{}`` for an absent
``settings_resolved``, and ``{}`` is the one value ``measure.checks`` names as the thing it must
never be handed: it is a ``Mapping``, so the drift check reads it as a real configuration in
which all 54 declared keys resolved to ``None``. Every row then shares one signature, and an
config of empties **passes**.

Two comments in the tree already forbade this and neither was enforced —
``checks.py::_settings_resolved_check`` ("Absent ``settings_resolved`` is unmeasured, not passing … one
config of empties would *pass* the check") and ``eval/harness.py`` ("absent stays absent"). The
writer at the other end of the pipe defeated both, which is why the test lives here rather than
in ``tests/measure``: pinning the check alone would leave the substitution free to come back.

Paired, per this parcel's authoring rule — the ``{}`` case is kept precisely to show the check
*would* pass it, so the assertion below cannot be satisfied by the check simply refusing
everything.
"""

from __future__ import annotations

from typing import Any

from governed_bi.measure import checks
from governed_bi.measure.population import Population
from governed_bi.serve.nodes.finish import finish

_EMPTY_EXECUTION: dict[str, Any] = {
    "attempts": [],
    "terminal": "no_sql",
    "guardrail_errors": 0,
}


def _finish_without_settings() -> dict[str, Any]:
    """A turn where nothing ever wrote ``settings_resolved`` — the wiring failure under test."""
    out = finish(
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


def _config(value: Any) -> Population:
    return Population.of("config", [{"question_id": f"q{i}", "settings_resolved": value} for i in range(3)])


def test_finish_does_not_substitute_an_empty_configuration() -> None:
    record = _finish_without_settings()

    assert record["settings_resolved"] != {}, (
        "finish substituted `{}` for an absent settings_resolved. That is not a configuration, "
        "it is the absence of one, and measure.checks cannot tell the difference: `{}` is a "
        "Mapping, so every row's drift signature matches and the config reports quotable."
    )
    assert record["settings_resolved"] is None


def test_an_unwired_config_cannot_be_evaluated_while_an_empty_one_would_pass() -> None:
    """The consequence, and the reason the substitution mattered rather than merely being untidy."""
    unwired = checks._settings_resolved_check(_config(_finish_without_settings()["settings_resolved"]))
    substituted = checks._settings_resolved_check(_config({}))

    assert unwired.result is not substituted.result, (
        "the check gives absent and empty the same result, so the substitution this file "
        "guards against would be undetectable here"
    )
    assert unwired.result.name == "cannot_evaluate", unwired.result
    # Not an endorsement — this is the hazard, pinned so the pair above stays meaningful.
    assert substituted.result.name == "passed", substituted.result
