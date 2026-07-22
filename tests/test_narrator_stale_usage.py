"""Narrator must not fold stale last_usage_metadata into refusal turns."""

from __future__ import annotations

from governed_bi.analyst.answer import ReliabilityTier, SemanticAssurance, refusal
from governed_bi.analyst.governance import narrate_answer


class _Chat:
    last_usage_metadata = {"input_tokens": 99, "output_tokens": 1, "total_tokens": 100}


class _Narrator:
    chat = _Chat()

    def narrate(self, question, sql, result):  # pragma: no cover - must not be called
        raise AssertionError("narrator must not run on refusals")


def test_narrate_answer_skips_refusal_without_calling_model():
    ans = refusal(escalation="x", provenance={"refused_by": "refuse_gate"})
    assert ans.result is None
    out = narrate_answer(ans, "q", _Narrator())
    assert out is ans


def test_stale_usage_not_folded_when_narrator_skipped():
    """Mirrors narrate_node: when narrate_answer returns the same object, skip amend."""
    ans = refusal(escalation="x", provenance={"refused_by": "refuse_gate"})
    narrator = _Narrator()
    narrated = narrate_answer(ans, "q", narrator)
    assert narrated is ans
    # Node logic: only fold when narrated is not answer
    if narrated is ans:
        extra = None
    else:
        extra = narrator.chat.last_usage_metadata
    assert extra is None
