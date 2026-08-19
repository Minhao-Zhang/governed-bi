"""Unit tests for :mod:`governed_bi.serve.structured_check` (ported from DetentAI v1,
Experiment 007 Round H)."""

from __future__ import annotations

from governed_bi.serve.structured_check import percentage_scale_suffix


def test_no_suffix_when_question_does_not_mention_percentage() -> None:
    assert percentage_scale_suffix("how many customers?", "SELECT COUNT(*) FROM t") == ""


def test_no_suffix_when_sql_scales_by_100_suffix_form() -> None:
    sql = "SELECT (a::float / b) * 100 AS pct FROM t"
    assert percentage_scale_suffix("what percentage of orders are late?", sql) == ""


def test_no_suffix_when_sql_scales_by_100_prefix_form() -> None:
    """The first version of this check (Round H's throwaway eval script) only matched
    "X * 100" and missed "100 * X" — over-triggering on already-correct queries."""
    sql = "SELECT 100 * (a::float / b) AS pct FROM t"
    assert percentage_scale_suffix("what percentage of orders are late?", sql) == ""


def test_fires_when_percentage_question_has_unscaled_ratio() -> None:
    sql = "SELECT a::float / b AS ratio FROM t"
    suffix = percentage_scale_suffix("what percentage of orders are late?", sql)
    assert "structured check" in suffix
    assert "PERCENTAGE" in suffix


def test_fires_on_percent_spelling_too() -> None:
    sql = "SELECT a::float / b FROM t"
    assert percentage_scale_suffix("what percent of orders are late?", sql) != ""


def test_fires_when_sql_is_none() -> None:
    """No SQL to scan for a scaling factor is not evidence of one, so this still fires."""
    assert percentage_scale_suffix("what percentage of orders are late?", None) != ""


def test_no_suffix_when_question_is_none() -> None:
    assert percentage_scale_suffix(None, "SELECT a / b FROM t") == ""
