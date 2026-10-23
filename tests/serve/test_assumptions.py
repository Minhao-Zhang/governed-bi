"""The pure assumption checks. The answer tool's turn-level tests live elsewhere."""

from __future__ import annotations

from typing import Literal

import pytest

from governed_bi.serve.assumptions import (
    AssumptionClaim,
    check_layer_claims,
    delivered_statement,
    found_in_query,
)

_QUESTION = "how many rows are there"


def _claim(
    basis: Literal["layer", "convention", "guess"],
    asset_id: str | None,
    about: str | None = "rows",
) -> AssumptionClaim:
    return AssumptionClaim(
        statement="Every row is counted.",
        about=about,
        basis=basis,
        asset_id=asset_id,
    )


def test_a_fabricated_asset_id_is_downgraded_to_guess() -> None:
    checked = check_layer_claims(
        [_claim("layer", "no.such.asset"), _claim("layer", None)],
        known_assets={"sales.orders"},
        retrieved_assets={"sales.orders"},
    )
    assert [row["basis"] for row in checked] == ["guess", "guess"]
    assert checked[0]["asset_id"] == "no.such.asset"
    assert checked[1]["asset_id"] is None


def test_a_known_asset_that_was_not_retrieved_is_downgraded_to_guess() -> None:
    checked = check_layer_claims(
        [_claim("layer", "sales.orders")],
        known_assets={"sales.orders", "sales.customers"},
        retrieved_assets={"sales.customers"},
    )
    assert checked[0]["basis"] == "guess"
    assert checked[0]["asset_id"] == "sales.orders"


def test_a_retrieved_asset_stays_layer() -> None:
    checked = check_layer_claims(
        [_claim("layer", "sales.orders", about="orders")],
        known_assets={"sales.orders"},
        retrieved_assets={"sales.orders"},
    )
    assert checked == [
        {
            "statement": "Every row is counted.",
            "about": "orders",
            "basis": "layer",
            "asset_id": "sales.orders",
        }
    ]


def test_convention_and_guess_pass_through() -> None:
    claims = [
        _claim("convention", None, about="year"),
        _claim("guess", "not.a.layer", about=None),
    ]
    checked = check_layer_claims(
        claims, known_assets=set(), retrieved_assets=set()
    )
    assert [row["basis"] for row in checked] == ["convention", "guess"]
    assert checked[0]["about"] == "year"
    assert checked[1]["asset_id"] == "not.a.layer"
    assert checked[1]["about"] is None


@pytest.mark.parametrize(
    ("sql", "needle"),
    [
        ("SELECT id FROM t WHERE status = 'closed'", "'closed'"),
        ("SELECT id FROM t WHERE year = 2024", "2024"),
        ("SELECT id FROM t GROUP BY id HAVING id > 3", "3"),
        ("SELECT a.id FROM t a INNER JOIN u b ON a.id = b.id", "Unmatched rows between t and u"),
        ("SELECT DISTINCT id FROM t", "DISTINCT"),
        ("SELECT COUNT(DISTINCT id) FROM t", "DISTINCT"),
        ("SELECT id FROM t LIMIT 10", "LIMIT"),
        ("SELECT c.ssn FROM sales.customers c", "sales.customers.ssn"),
    ],
)
def test_each_query_pattern_produces_one_finding(sql: str, needle: str) -> None:
    suspects = ["sales.customers.ssn"] if "ssn" in sql else []
    found = found_in_query(sql, question=_QUESTION, suspect_columns=suspects)
    assert len(found) == 1, found
    assert found[0]["basis"] == "found_in_query"
    assert found[0]["about"] is None
    assert found[0]["asset_id"] is None
    assert needle in found[0]["statement"]


def test_a_literal_the_question_mentions_is_not_a_finding() -> None:
    sql = "SELECT id FROM t WHERE status = 'closed'"
    assert found_in_query(sql, question="how many Closed rows") == []
    assert found_in_query(sql, question=_QUESTION) != []


def test_a_comma_join_with_a_condition_drops_unmatched_rows() -> None:
    linked = found_in_query(
        "SELECT a.id FROM t a, u b WHERE a.id = b.id", question=_QUESTION
    )
    assert len(linked) == 1
    assert linked[0]["statement"] == "Unmatched rows between t and u are dropped."
    assert found_in_query("SELECT a.id FROM t a, u b", question=_QUESTION) == []


def test_a_left_join_is_not_reported() -> None:
    sql = "SELECT a.id FROM t a LEFT JOIN u b ON a.id = b.id"
    assert found_in_query(sql, question=_QUESTION) == []


def test_a_join_condition_on_a_literal_is_both_a_filter_and_an_inner_join() -> None:
    found = found_in_query(
        "SELECT a.id FROM t a JOIN u b ON a.n = 7", question=_QUESTION
    )
    assert [row["statement"] for row in found] == [
        "The statement compares a.n to 7, which the question does not mention.",
        "Unmatched rows between t and u are dropped.",
    ]


def test_findings_keep_category_order_then_position() -> None:
    sql = (
        "SELECT DISTINCT a.id FROM t a "
        "INNER JOIN u b ON a.id = b.id "
        "WHERE a.name = 'zeta' AND a.status = 'closed' LIMIT 5"
    )
    found = found_in_query(sql, question="list the ids", suspect_columns=["t.id"])
    assert [row["statement"] for row in found] == [
        "The statement compares a.name to 'zeta', which the question does not mention.",
        "The statement compares a.status to 'closed', which the question does not mention.",
        "Unmatched rows between t and u are dropped.",
        "The statement uses DISTINCT, so duplicate rows are collapsed.",
        "The statement has its own LIMIT.",
        "The statement reads t.id, which the layer marks as suspect.",
    ]


def test_the_servers_limit_is_not_a_finding_when_the_statement_had_none() -> None:
    checked = "SELECT id FROM t"
    executed = "SELECT id FROM t LIMIT 200001"
    attempts = [
        {"path": "sample", "executed_sql": executed, "checked_sql": checked},
        {"path": "agent", "executed_sql": None, "checked_sql": None},
        {"path": "agent", "executed_sql": executed, "checked_sql": checked},
    ]
    assert delivered_statement(attempts) == checked
    assert found_in_query(delivered_statement(attempts), question=_QUESTION) == []
    limited = found_in_query(executed, question=_QUESTION)
    assert len(limited) == 1 and "LIMIT" in limited[0]["statement"]


def test_an_old_checkpoint_falls_back_to_the_executed_text() -> None:
    executed = "SELECT id FROM t LIMIT 200001"
    assert delivered_statement([{"path": "agent", "executed_sql": executed}]) == executed
    later = [
        {"path": "agent", "executed_sql": "SELECT 1 LIMIT 9", "checked_sql": "SELECT old"},
        {"path": "agent", "executed_sql": "SELECT 2 LIMIT 9", "checked_sql": "SELECT new"},
    ]
    assert delivered_statement(later) == "SELECT new"


def test_nothing_executed_has_no_delivered_statement() -> None:
    assert delivered_statement([]) is None
    assert delivered_statement([{"path": "agent", "executed_sql": None}]) is None
    assert delivered_statement(
        [{"path": "sample", "executed_sql": "SELECT 1", "checked_sql": "SELECT 1"}]
    ) is None


def test_no_query_and_unparseable_text_produce_no_findings() -> None:
    assert found_in_query(None, question=_QUESTION) == []
    assert found_in_query("   ", question=_QUESTION) == []
    assert found_in_query("SELECT ((", question=_QUESTION) == []


def test_a_number_is_mentioned_only_as_a_whole_number() -> None:
    sql = "SELECT id FROM t WHERE grade = 1"
    assert found_in_query(sql, question="top 10 by grade 1") == []
    assert len(found_in_query(sql, question="top 10 by grade")) == 1
    assert len(found_in_query(sql, question="grade 1.5 or above")) == 1
