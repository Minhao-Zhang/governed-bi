"""One test per bypass in ADR 0006. Do not weaken a test to match the code. ``failed_layer=None`` means passed."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


BLOCKED = "must be blocked, and by a named layer"


@pytest.fixture
def layer():
    from governed_bi.govern.layers import Layer  # type: ignore[import-not-found]

    return Layer


# ── the enum's shape is ─────────────────────────────────────────


def test_layers_are_ordered_and_have_no_fractional_members(layer) -> None:
    """A block that reports no layer is read as passed, so every layer has an integer place."""
    assert [m.value for m in layer] == [1, 2, 3, 4, 5, 6]
    assert layer.FUNCTIONS < layer.BINDING < layer.COLUMNS < layer.TABLES


def test_a_result_never_reports_none_for_a_block(check, layer) -> None:
    """B3. ``failed_layer=None`` must mean **passed**, with no third state.

    v1's cap path produced a result with no layer, graded delivery read it as
    non-hard, and card-number SQL would have reached the gateway.
    """
    result = check("DROP TABLE customers", retrieved_tables=frozenset({"customers"}))
    assert result["passed"] is False
    assert result["failed_layer"] is not None, BLOCKED


def test_layers_evaluated_distinguishes_did_not_run_from_passed(check) -> None:
    """A layer with no entry **did not run**. Absence is not agreement -- L-R1."""
    result = check("DROP TABLE customers", retrieved_tables=frozenset({"customers"}))
    assert len(result["layers_evaluated"]) < 7


# ── B1-B10 ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT pg_read_file('/etc/passwd')",
        "SELECT query_to_xml('SELECT * FROM customers', true, false, '')",
        "SELECT table_to_xml('customers', true, false, '')",
        "SELECT database_to_xml(true, false, '')",
        "SELECT setval('customers_id_seq', 1)",
        "SELECT nextval('customers_id_seq')",
    ],
)
def test_b1_column_less_calls_are_blocked(check, sql) -> None:
    """B1. No table and no column referenced, so the column and table layers are
    structurally blind. A read-only connection does not help: these are reads. The
    XML-export family takes its target as a **string literal**, which sqlglot parses as
    ``exp.Anonymous``, and one call dumps a whole table."""
    assert check(sql, retrieved_tables=frozenset({"customers"}))["passed"] is False, BLOCKED


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT json_agg(t) FROM customers t",
        "SELECT row_to_json(t) FROM customers t",
        "SELECT array_agg(t) FROM customers t",
        "SELECT to_jsonb(t) FROM customers t",
        "SELECT count(t.*) FROM customers t",
    ],
)
def test_b2_whole_row_aggregates_are_blocked(check, sql) -> None:
    """These emit every column and produce no ``Column`` node, so a walk that looks for columns passes them."""
    assert check(sql, retrieved_tables=frozenset({"customers"}))["passed"] is False, BLOCKED


def test_b4_a_missing_allowlist_raises_rather_than_falling_through(check) -> None:
    """A missing retrieved set raises. A blocked result would record a caller error as an unsafe query."""
    with pytest.raises(Exception):
        check("SELECT id FROM customers", retrieved_tables=None)


def test_b4_sibling_an_empty_retrieved_tables_set_is_not_a_wildcard(check) -> None:
    """The `if not allowlist` reading of the same defect. An empty set licenses
    nothing; it is not "no restriction"."""
    assert check("SELECT id FROM customers", retrieved_tables=frozenset())["passed"] is False, BLOCKED


@pytest.mark.parametrize("spelling", ["customerid", "CUSTOMERID", "CustomerId"])
def test_b5_case_folding_does_not_clear_an_allowlist(check, spelling) -> None:
    """B5. Postgres folds unquoted identifiers, so ``customerid`` clears a
    ``CustomerID`` allowlist — and quoting the model's spelling then sends the engine a
    column that does not exist. Fold **both** sides consistently; do not quote to
    compensate."""
    result = check(
        f"SELECT {spelling} FROM customers",
        retrieved_tables=frozenset({"customers"}),
        allowed_columns=frozenset({"customers.CustomerID"}),
    )
    assert result["passed"] is True, "a fold-equal column must be permitted, not quoted around"


def test_b6_three_part_references_are_bound_not_just_matched(check) -> None:
    """A three-part name can sit in a lake-wide allowlist without binding to an in-scope source."""
    result = check(
        "SELECT other_schema.secrets.token FROM customers",
        retrieved_tables=frozenset({"customers"}),
        allowed_columns=frozenset({"secrets.token", "customers.id"}),
    )
    assert result["passed"] is False, BLOCKED


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM customers",
        "SELECT c.* FROM customers c",
        "SELECT id FROM customers NATURAL JOIN orders",
    ],
)
def test_b6_siblings_star_and_natural_join(check, sql) -> None:
    """A star projection and a ``NATURAL JOIN`` both expand to columns the SQL never
    names, so neither can be column-checked as written."""
    result = check(sql, retrieved_tables=frozenset({"customers", "orders"}))
    assert result["passed"] is False, BLOCKED


@pytest.mark.parametrize(
    "raw",
    ["beer_factory\n", "../etc", "a/b", "..", "", "beer factory", "beer_factory\r\n"],
)
def test_b8_schema_identifiers_are_anchored_with_A_and_Z(raw) -> None:
    """``\\A``/``\\Z``, because ``$`` matches before a trailing newline and the remainder names a directory."""
    from governed_bi.govern.identifiers import is_valid_schema_id  # type: ignore[import-not-found]

    assert is_valid_schema_id(raw) is False, f"{raw!r} must be rejected"


def test_b8_a_legitimate_identifier_still_passes() -> None:
    """The complement. A validator that rejects everything also passes the test above,
    and would be discovered only when the corpus stopped loading."""
    from governed_bi.govern.identifiers import is_valid_schema_id  # type: ignore[import-not-found]

    assert is_valid_schema_id("beer_factory") is True


# ── the allowlist is positive, content-hashed, and version-pinned ────────────


def test_the_function_allowlist_is_positive_not_a_denylist(check) -> None:
    """An invented function name must be refused because it is **not permitted**, not
    because it appears on a list of known-bad names. A denylist cannot enumerate the
    Postgres surface, and B1's XML family is the proof."""
    result = check("SELECT pg_sleep_totally_new(1) FROM customers", retrieved_tables=frozenset({"customers"}))
    assert result["passed"] is False, BLOCKED


def test_a_plain_analytic_query_passes(check) -> None:
    """A checker that refuses every statement satisfies every refusal test above."""
    result = check(
        "SELECT c.id, count(*) AS n FROM customers c JOIN orders o ON o.cid = c.id "
        "GROUP BY c.id ORDER BY n DESC LIMIT 10",
        retrieved_tables=frozenset({"customers", "orders"}),
        allowed_columns=frozenset({"customers.id", "orders.cid"}),
    )
    assert result["passed"] is True, result
    assert result["failed_layer"] is None
