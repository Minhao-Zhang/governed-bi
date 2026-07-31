"""Narrator must not fold usage into refusal turns (no model call)."""

from __future__ import annotations

from governed_bi.analyst.answer import refusal
from governed_bi.analyst.governance import narrate_answer


class _Narrator:
    def narrate(self, question, sql, result):  # pragma: no cover - must not be called
        raise AssertionError("narrator must not run on refusals")


def test_narrate_answer_skips_refusal_without_calling_model():
    ans = refusal(escalation="x", provenance={"refused_by": "refuse_gate"})
    assert ans.result is None
    out, usage = narrate_answer(ans, "q", _Narrator())
    assert out is ans
    assert usage is None


def test_stale_usage_not_folded_when_narrator_skipped():
    """Mirrors narrate_node: when narrate_answer returns the same object, skip amend."""
    ans = refusal(escalation="x", provenance={"refused_by": "refuse_gate"})
    narrated, usage = narrate_answer(ans, "q", _Narrator())
    assert narrated is ans
    assert usage is None
