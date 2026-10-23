"""The few-shot body is derived, and the text the Ossie file joins stays on one line (ADR 0022)."""

from __future__ import annotations

import pytest

from governed_bi.corpus.parse import from_mapping, to_mapping
from governed_bi.corpus.schema import FewShotAsset, SchemaAsset
from governed_bi.corpus.validate import problems_with


def _shot() -> FewShotAsset:
    return FewShotAsset(id="fs_beer_0000", schema="beer", sql="SELECT 1", summary="How many?")


def test_the_few_shot_body_is_built_from_the_question_and_the_sql() -> None:
    assert _shot().body == "Question: How many?\nSQL:\nSELECT 1"


def test_the_few_shot_body_is_not_written_and_not_accepted() -> None:
    mapping = to_mapping(_shot())
    assert "body" not in mapping
    assert from_mapping(mapping) == _shot()
    with pytest.raises(ValueError, match="unknown field"):
        from_mapping({**mapping, "body": "anything"})


def test_a_summary_with_a_newline_is_a_problem() -> None:
    schema = SchemaAsset(id="beer", name="beer", summary="beer: a schema of brands and\nsales and more words")
    assert any("summary contains a newline" in p for p in problems_with(schema))


def test_a_rule_with_a_newline_is_a_problem() -> None:
    schema = SchemaAsset(
        id="beer", name="beer", summary="beer: a schema of brands and sales", rules=("one\ntwo",)
    )
    assert any("a rule contains a newline" in p for p in problems_with(schema))
