"""``compare_column_pair``: the row-wise column comparison, through the governed path.

The gap detector this exists for (``curator/gaps.py``, near-duplicate columns whose values
disagree) asks a question no existing read answers. ``distinct_values_statement`` reports a
column's *value set*; "do these two columns hold the same thing on the same row" is a
**row-wise** predicate — ``COUNT(*) WHERE a IS DISTINCT FROM b`` — and two value sets can be
identical while every row disagrees. That is exactly the measured shape:
``transaktion.kunde_id`` and ``transaktions_kunde_id`` have the same 554 distinct customer ids
and disagree on 6 305 of 6 312 rows.

So it needs a new statement, and the reason it lives beside ``distinct_values_statement``
rather than in ``curator/`` is the cautionary tale in this module's own docstring: the deleted
``Connector.sample_values`` interpolated an unconstrained ``physical_name`` into an f-string and
called ``execute`` itself, reaching the database through no governance layer and writing no
ledger row. One home for "a governed read, built as a tree, run through ``prepare()``,
ledgered" is what keeps a second answer to that from being written.
"""

from __future__ import annotations

from typing import Any

import pytest


def _assets() -> dict[str, Any]:
    from governed_bi.corpus.schema import ColumnAsset, TableAsset

    columns = [
        ColumnAsset(
            id=f"shop.customers.{name}",
            schema="shop",
            parent_table="shop.customers",
            physical_name=name,
            summary=name,
            physical_type=physical_type,
        )
        for name, physical_type in (
            ("email", "text"), ("email_address", "text"), ("customer_id", "bigint"),
        )
    ]
    orders = ColumnAsset(
        id="shop.orders.order_id", schema="shop", parent_table="shop.orders",
        physical_name="order_id", summary="order_id", physical_type="bigint",
    )
    customers = TableAsset(
        id="shop.customers", schema="shop", physical_name="customers", summary="customers",
        columns=tuple(c.id for c in columns),
    )
    orders_table = TableAsset(
        id="shop.orders", schema="shop", physical_name="orders", summary="orders",
        columns=(orders.id,),
    )
    return {a.id: a for a in [customers, orders_table, orders, *columns]}


class _Rows:
    """The repo's governed-query test idiom: a ``dialect`` and an ``execute`` returning
    ``(columns, rows, truncated)``."""

    dialect = "postgres"

    def __init__(self, row: tuple[int, ...] = (554, 554, 554, 554)) -> None:
        self.row = row
        self.statements: list[str] = []

    def execute(self, sql: str, **_kwargs: Any) -> tuple[list[str], list[tuple[Any, ...]], bool]:
        self.statements.append(sql)
        return (["n_rows", "n_differing", "n_distinct_left", "n_distinct_right"], [self.row], False)


def _compare(
    left: str, right: str, *, connector: Any, licensed: frozenset[str] | None = None, **policy_kw: Any
) -> tuple[Any, Any]:
    from governed_bi.corpus.analyst import for_analyst
    from governed_bi.govern.bounds import ToolBounds
    from governed_bi.govern.policy import GovernancePolicy
    from governed_bi.serve.fetch import compare_column_pair

    assets = _assets()
    return compare_column_pair(
        left,
        right,
        bounds=ToolBounds(licensed=licensed if licensed is not None else frozenset({"shop.customers"})),
        assets=assets,
        connector=connector,
        corpus=for_analyst(list(assets.values())),
        policy=GovernancePolicy(**policy_kw),
    )


# ── the statement ───────────────────────────────────────────────────────────────────────────


def test_the_comparison_is_row_wise_and_null_safe() -> None:
    """``IS DISTINCT FROM``, not ``<>``. Two NULLs *agree* — a column pair that is NULL
    everywhere is redundant, not a disagreement — and ``NULL <> 5`` is NULL, so a plain
    inequality would silently under-count every row where one side is missing.
    """
    from governed_bi.serve.fetch import column_pair_agreement_statement

    sql = column_pair_agreement_statement(
        schema="shop", table="customers", left="email", right="email_address", dialect="postgres"
    )
    assert "IS DISTINCT FROM" in sql, sql
    assert "<>" not in sql, sql


def test_the_statement_reports_row_counts_and_both_cardinalities() -> None:
    """Four numbers in one statement, not four statements.

    ``n_differing``/``n_rows`` is the evidence a T1 finding is ranked on; the two distinct
    counts are the precision co-signal (two columns that are two copies of one fact have
    comparable value vocabularies — 554 and 554, not 554 and 2).
    """
    from governed_bi.serve.fetch import column_pair_agreement_statement

    sql = column_pair_agreement_statement(
        schema="shop", table="customers", left="email", right="email_address", dialect="postgres"
    )
    for alias in ("n_rows", "n_differing", "n_distinct_left", "n_distinct_right"):
        assert alias in sql, (alias, sql)
    assert sql.count("COUNT(DISTINCT") == 2, sql


def test_the_statement_cannot_escape_its_identifiers() -> None:
    """Built as a syntax tree, for ``distinct_values_statement``'s reason: ``physical_name``
    holds the engine's identifier verbatim (``corpus/identity.slug`` — "any character, any
    case, any script") and ``corpus/validate.py`` validates only its slug, so this field is
    deliberately unconstrained in content.
    """
    import sqlglot

    from governed_bi.serve.fetch import column_pair_agreement_statement

    evil = 'x" FROM "pg_catalog"."pg_shadow" -- '
    sql = column_pair_agreement_statement(
        schema="shop", table="customers", left=evil, right="email", dialect="postgres"
    )
    assert "pg_shadow" in sql, "the fixture stopped exercising the escape"
    tree = sqlglot.parse_one(sql, dialect="postgres")
    tables = {t.sql(dialect="postgres") for t in tree.find_all(sqlglot.exp.Table)}
    assert tables == {'"shop"."customers"'}, tables


def test_the_statement_clears_the_governance_layer_stack() -> None:
    """Every function in it is on ``govern/functions.PERMITTED_FUNCTIONS`` and every column
    reference is table-qualified, so it binds. A statement the pipeline refuses would make the
    detector permanently blind while looking like a data finding of zero."""
    from governed_bi.corpus.analyst import for_analyst
    from governed_bi.govern.pipeline import prepare, spellings_for
    from governed_bi.govern.policy import GovernancePolicy
    from governed_bi.serve.fetch import column_pair_agreement_statement

    assets = _assets()
    corpus = for_analyst(list(assets.values()))
    licensed = frozenset({"shop.customers"})
    spellings, ambiguous = spellings_for(corpus, licensed)
    prepared = prepare(
        column_pair_agreement_statement(
            schema="shop", table="customers", left="email", right="email_address",
            dialect="postgres",
        ),
        licensed=licensed, corpus=corpus, spellings=spellings, ambiguous_folds=ambiguous,
        dialect="postgres", policy=GovernancePolicy(),
    )
    assert prepared.verdict["passed"] is True, prepared.verdict
    assert prepared.sql is not None


# ── the governed read ───────────────────────────────────────────────────────────────────────


def test_compare_returns_the_four_counts_and_a_ledger_row() -> None:
    connector = _Rows((554, 554, 554, 554))
    agreement, attempt = _compare(
        "shop.customers.email", "shop.customers.email_address", connector=connector
    )
    assert agreement is not None
    assert (agreement.n_rows, agreement.n_differing) == (554, 554)
    assert (agreement.n_distinct_left, agreement.n_distinct_right) == (554, 554)
    assert attempt is not None and attempt["path"] == "sample" and attempt["passed"]
    assert attempt["executed_sql"], "a governed statement that ran owes the ledger its text"


def test_a_pair_spanning_two_tables_is_not_comparable_row_wise() -> None:
    """No statement is built, so there is no governance decision and no ledger row. A row-wise
    predicate over two relations needs a join key, which is the *other* detector's question."""
    connector = _Rows()
    agreement, attempt = _compare(
        "shop.customers.email", "shop.orders.order_id", connector=connector,
        licensed=frozenset({"shop.customers", "shop.orders"}),
    )
    assert agreement is None
    assert attempt is None
    assert not connector.statements


def test_an_unlicensed_table_is_refused_before_any_statement_exists() -> None:
    connector = _Rows()
    agreement, attempt = _compare(
        "shop.customers.email", "shop.customers.email_address", connector=connector,
        licensed=frozenset(),
    )
    assert agreement is None and attempt is None
    assert not connector.statements


def test_a_governance_refusal_yields_no_counts_but_still_yields_its_row() -> None:
    """The reason ``read_observed_values`` gives: a refused attempt is a governance decision the
    audit trail owes a row exactly as much as a passing one. The detector must then skip the
    pair rather than route around the refusal."""
    import dataclasses

    from governed_bi.corpus.schema import Reliability, ReliabilityStatus

    assets = _assets()
    assets["shop.customers.email_address"] = dataclasses.replace(
        assets["shop.customers.email_address"],
        reliability=Reliability(status=ReliabilityStatus.suspect),
    )

    from governed_bi.corpus.analyst import for_analyst
    from governed_bi.govern.bounds import ToolBounds
    from governed_bi.govern.policy import GovernancePolicy
    from governed_bi.serve.fetch import compare_column_pair

    connector = _Rows()
    agreement, attempt = compare_column_pair(
        "shop.customers.email",
        "shop.customers.email_address",
        bounds=ToolBounds(licensed=frozenset({"shop.customers"})),
        assets=assets,
        connector=connector,
        corpus=for_analyst(list(assets.values())),
        policy=GovernancePolicy(hard_block_suspect=True),
    )
    assert agreement is None
    assert attempt is not None and attempt["passed"] is False
    assert attempt["reason_code"] == "r_column_suspect", attempt
    assert not connector.statements, "a refused pair reached the engine"


def test_a_driver_failure_keeps_the_row_because_the_statement_was_sent() -> None:
    """A type-incompatible pair is refused by the *engine*, not by governance —
    ``bigint IS DISTINCT FROM text`` raises ``operator does not exist``. It cleared every layer
    and was sent, so the ledger owes it a row (``run_query``'s reasoning)."""

    class Broken(_Rows):
        def execute(self, sql: str, **_kwargs: Any):
            self.statements.append(sql)
            raise RuntimeError("operator does not exist: bigint = text")

    connector = Broken()
    agreement, attempt = _compare(
        "shop.customers.customer_id", "shop.customers.email", connector=connector
    )
    assert agreement is None
    assert attempt is not None and attempt["passed"] is True
    assert connector.statements, "the statement was built and sent"


def test_no_connector_is_a_refusal_with_a_row_not_a_crash() -> None:
    agreement, attempt = _compare(
        "shop.customers.email", "shop.customers.email_address", connector=None
    )
    assert agreement is None
    assert attempt is not None and attempt["passed"] is False


def test_a_missing_corpus_raises_rather_than_recording_a_verdict() -> None:
    """G1, and ``sample_rows``'s own reasoning: a missing ``AnalystCorpus`` is a wiring failure,
    and refusing on it would record a governance verdict for it."""
    from governed_bi.govern.bounds import ToolBounds
    from governed_bi.govern.check import GovernanceUsageError
    from governed_bi.govern.policy import GovernancePolicy
    from governed_bi.serve.fetch import compare_column_pair

    assets = _assets()
    with pytest.raises(GovernanceUsageError):
        compare_column_pair(
            "shop.customers.email",
            "shop.customers.email_address",
            bounds=ToolBounds(licensed=frozenset({"shop.customers"})),
            assets=assets,
            connector=_Rows(),
            corpus=None,
            policy=GovernancePolicy(),
        )
