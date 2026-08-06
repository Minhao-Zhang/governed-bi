"""mine_mistake_from_execution: only a real failure-then-pass pair yields a draft."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from contracts import needs  # noqa: E402

pytestmark = needs("D")


def _attempt(*, passed: bool, verdict_layer: str | None, executed_sql: str | None) -> dict:
    return {
        "verdict_layer": verdict_layer,
        "passed": passed,
        "reason_code": "r_ok" if passed else "r_column_not_allowed",
        "path": "agent",
        "executed_sql": executed_sql,
    }


def test_no_draft_when_the_first_attempt_already_passed() -> None:
    from governed_bi.curator.mistake_memory import mine_mistake_from_execution

    execution = {
        "attempts": [_attempt(passed=True, verdict_layer=None, executed_sql="SELECT 1")],
        "terminal": "answered",
        "guardrail_errors": 0,
    }
    assert mine_mistake_from_execution("how many rows?", "s", execution) is None


def test_no_draft_when_nothing_ever_passed() -> None:
    from governed_bi.curator.mistake_memory import mine_mistake_from_execution

    execution = {
        "attempts": [_attempt(passed=False, verdict_layer="COLUMNS", executed_sql=None)],
        "terminal": "refused",
        "guardrail_errors": 0,
    }
    assert mine_mistake_from_execution("how many rows?", "s", execution) is None


def test_draft_mined_from_a_real_fail_then_pass_sequence() -> None:
    from governed_bi.curator.mistake_memory import mine_mistake_from_execution

    execution = {
        "attempts": [
            _attempt(passed=False, verdict_layer="COLUMNS", executed_sql=None),
            _attempt(passed=True, verdict_layer=None, executed_sql="SELECT COUNT(*) FROM t"),
        ],
        "terminal": "answered",
        "guardrail_errors": 0,
    }
    draft = mine_mistake_from_execution("how many rows in t?", "beer_factory", execution)
    assert draft is not None
    assert draft.asset_type.value == "few_shot"
    assert draft.schema == "beer_factory"
    assert draft.sql == "SELECT COUNT(*) FROM t"
    assert draft.summary == "how many rows in t?"
    assert "COLUMNS" in draft.body
    assert "SELECT COUNT(*) FROM t" in draft.body


def test_mined_id_is_deterministic_and_a_safe_asset_id() -> None:
    from governed_bi.corpus.identity import validate_asset_id
    from governed_bi.curator.mistake_memory import mine_mistake_from_execution

    execution = {
        "attempts": [
            _attempt(passed=False, verdict_layer="COLUMNS", executed_sql=None),
            _attempt(passed=True, verdict_layer=None, executed_sql="SELECT 1"),
        ],
        "terminal": "answered",
        "guardrail_errors": 0,
    }
    a = mine_mistake_from_execution("same question", "s", execution)
    b = mine_mistake_from_execution("same question", "s", execution)
    assert a.id == b.id
    assert validate_asset_id(a.id) == a.id


def test_long_question_summary_is_truncated_to_the_registered_cap() -> None:
    from governed_bi.curator.mistake_memory import mine_mistake_from_execution
    from governed_bi.register.knobs import knob_default

    execution = {
        "attempts": [
            _attempt(passed=False, verdict_layer="COLUMNS", executed_sql=None),
            _attempt(passed=True, verdict_layer=None, executed_sql="SELECT 1"),
        ],
        "terminal": "answered",
        "guardrail_errors": 0,
    }
    question = "why " * 200
    draft = mine_mistake_from_execution(question, "s", execution)
    assert len(draft.summary) <= int(knob_default("summary_max_chars"))
    # The full question still reaches the model on a hit, per I1 (body is the only
    # unbounded field), so truncating the summary does not lose it.
    assert question.strip() in draft.body
