"""Tests for the agent-authored clarifications.jsonl ledger."""

from __future__ import annotations

from pathlib import Path

import pytest

from governed_bi.curator.clarifications import (
    ClarificationRecord,
    ClarificationRecordStatus,
    StaticResponder,
    fill_clarifications_with_responder,
    load_clarifications,
    next_clarification_id,
    parse_line,
    parse_scope,
    upsert_clarification_record,
    write_clarifications,
)


def test_round_trip_jsonl(tmp_path: Path):
    records = [
        ClarificationRecord(
            id="q001",
            scope="table:customers",
            question="Who are the customers?",
            raised_by=["t1"],
        ),
        ClarificationRecord(
            id="q002",
            scope="table:customers.CustomerID",
            question="Is CustomerID the PK?",
            status=ClarificationRecordStatus.answered,
            raised_by=["t1", "t2"],
            answer="Yes, surrogate key.",
            answered_by="sme",
        ),
    ]
    path = tmp_path / "clarifications.jsonl"
    write_clarifications(path, records)
    loaded = load_clarifications(path)
    assert len(loaded) == 2
    assert loaded[0].id == "q001"
    assert loaded[1].answer == "Yes, surrogate key."


def test_parse_line_rejects_bad_json():
    with pytest.raises(Exception):
        parse_line('{"id": "q001"}')  # missing required fields


def test_parse_scope():
    assert parse_scope("table:customers") == ("customers", None)
    assert parse_scope("table:customers.CustomerID") == ("customers", "CustomerID")
    with pytest.raises(ValueError):
        parse_scope("join:foo")


def test_next_clarification_id():
    assert next_clarification_id([]) == "q001"
    assert (
        next_clarification_id(
            [ClarificationRecord(id="q003", scope="table:t", question="?")]
        )
        == "q004"
    )


def test_upsert_broadens_same_scope_same_id():
    """Acceptance (b): broadening a prior question edits the same id, no duplicate."""
    once = upsert_clarification_record(
        [],
        scope="table:customers.height",
        question="Is height a literal?",
        raised_by="t14",
    )
    twice = upsert_clarification_record(
        once,
        scope="table:customers.height",
        question="Or an FK into height_info?",
        raised_by="t22",
    )
    assert len(twice) == 1
    assert twice[0].id == once[0].id == "q001"
    assert twice[0].raised_by == ["t14", "t22"]
    assert "literal" in twice[0].question
    assert "FK" in twice[0].question or "height_info" in twice[0].question

    other = upsert_clarification_record(
        twice,
        scope="table:customers",
        question="Who are customers?",
        raised_by="t14",
    )
    assert len(other) == 2
    assert other[1].id == "q002"


def test_fill_with_responder():
    records = [
        ClarificationRecord(id="q001", scope="table:t", question="What is t?"),
        ClarificationRecord(
            id="q002",
            scope="table:t.c",
            question="What is c?",
            status=ClarificationRecordStatus.answered,
            answer="already",
            answered_by="prior",
        ),
    ]
    out = fill_clarifications_with_responder(
        records, StaticResponder(default="A table of things.")
    )
    assert out[0].status is ClarificationRecordStatus.answered
    assert out[0].answer == "A table of things."
    assert out[1].answer == "already"
