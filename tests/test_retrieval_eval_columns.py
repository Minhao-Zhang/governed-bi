"""Column-level retrieval recall (eval/retrieval_eval.py).

The gold-SQL column parser is the whole metric: everything downstream is set
arithmetic over what it returns, so a mis-attributed reference does not show up as
a crash, it shows up as a plausible-looking wrong number. These tests pin the
attribution rules — aliases, ``SELECT *``, subquery scoping, correlated refs,
ambiguity — and the governance split that keeps a deliberately excluded column
from being scored as a retrieval miss.

All in-memory: no corpus checkout, no BIRD, no model.
"""

from __future__ import annotations

import pytest

from governed_bi.corpus import Column, Corpus, JoinAsset, TableAsset
from governed_bi.corpus.schemas import Governance, Reliability
from governed_bi.eval.dataset import EvalItem
from governed_bi.eval.retrieval_eval import (
    evaluate_retrieval,
    gold_column_refs,
    gold_table_ids,
    merge_reports,
)

SHOP = "shop"
ORDERS = "tbl_shop_orders"
CUSTOMERS = "tbl_shop_customers"
WIDE = "tbl_shop_wide"


def _col(name: str, *, excluded: bool = False, suspect: bool = False, desc: str | None = None):
    return Column(
        physical_name=name,
        physical_type="text",
        logical_type="string",
        nullable=True,
        is_unique=False,
        description=desc,
        governance=Governance(excluded=excluded),
        reliability=Reliability(status="suspect" if suspect else "ok"),
    )


@pytest.fixture
def corpus() -> Corpus:
    """Two small tables that share a column name, plus one deliberately wide one."""
    orders = TableAsset(
        id=ORDERS,
        schema=SHOP,
        physical_name="orders",
        description="Customer orders, one row per order.",
        columns=[
            _col("order_id"),
            _col("customer_id"),
            _col("amount", desc="Order amount in dollars."),
            _col("internal_note", excluded=True),
            _col("legacy_total", suspect=True),
        ],
    )
    customers = TableAsset(
        id=CUSTOMERS,
        schema=SHOP,
        physical_name="customers",
        description="Customers.",
        columns=[_col("customer_id"), _col("name"), _col("city")],
    )
    wide = TableAsset(
        id=WIDE,
        schema=SHOP,
        physical_name="wide",
        description="A wide fact table.",
        columns=[_col("id")] + [_col(f"c{i}") for i in range(1, 31)],
    )
    join = JoinAsset(
        id="jn_shop_orders_customers",
        left_table=ORDERS,
        right_table=CUSTOMERS,
        on="orders.customer_id = customers.customer_id",
        confidence=0.9,
    )
    return Corpus(assets=[orders, customers, wide, join])


def refs(corpus, sql):
    return gold_column_refs(corpus, sql, dialect="postgres")


# --------------------------------------------------------------------------- #
# Qualified references and aliases
# --------------------------------------------------------------------------- #


def test_alias_resolves_to_its_table(corpus):
    got = refs(corpus, 'SELECT "T1"."amount" FROM "shop"."orders" AS "T1"')
    assert got.refs == frozenset({(ORDERS, "amount")})
    assert got.unresolvable == () and got.derived == () and got.stars == 0


def test_two_aliases_over_two_tables_keep_their_own_columns(corpus):
    got = refs(
        corpus,
        'SELECT "T1"."amount", "T2"."city" FROM "shop"."orders" AS "T1" '
        'JOIN "shop"."customers" AS "T2" ON "T1"."customer_id" = "T2"."customer_id"',
    )
    assert got.refs == frozenset(
        {
            (ORDERS, "amount"),
            (CUSTOMERS, "city"),
            (ORDERS, "customer_id"),
            (CUSTOMERS, "customer_id"),
        }
    )


def test_column_names_are_case_folded(corpus):
    # BIRD gold and the curated corpus disagree on case constantly.
    assert refs(corpus, "SELECT T1.AMOUNT FROM orders T1").refs == frozenset(
        {(ORDERS, "amount")}
    )


def test_qualified_ref_to_a_table_outside_the_corpus(corpus):
    got = refs(corpus, "SELECT s.qty FROM shipments s")
    assert got.refs == frozenset()
    assert got.unknown_table == frozenset({("shipments", "qty")})


# --------------------------------------------------------------------------- #
# Bare names
# --------------------------------------------------------------------------- #


def test_bare_name_with_one_source_resolves(corpus):
    assert refs(corpus, "SELECT amount FROM orders").refs == frozenset(
        {(ORDERS, "amount")}
    )


def test_bare_name_disambiguated_by_schema_membership(corpus):
    # `city` exists only on customers, so SQL itself admits exactly one binding.
    got = refs(
        corpus,
        "SELECT city FROM orders o JOIN customers c ON o.customer_id = c.customer_id",
    )
    assert (CUSTOMERS, "city") in got.refs
    assert got.unresolvable == ()


def test_genuinely_ambiguous_bare_name_is_counted_not_guessed(corpus):
    # Both tables carry customer_id: no attribution is defensible, so it is
    # reported instead of being credited to whichever table sorted first.
    got = refs(
        corpus,
        "SELECT customer_id FROM orders o JOIN customers c ON o.amount = c.name",
    )
    assert got.unresolvable == ("customer_id",)
    assert not any(col == "customer_id" for _tid, col in got.refs)


def test_bare_name_no_table_declares_is_a_coverage_miss_not_unresolvable(corpus):
    # One candidate table and the corpus simply has no such column: that is
    # exactly the curation gap the coverage number exists to count, so it must
    # stay attributed rather than vanish into the unresolvable bucket.
    got = refs(corpus, "SELECT shipped_at FROM orders")
    assert got.refs == frozenset({(ORDERS, "shipped_at")})
    assert got.unresolvable == ()


# --------------------------------------------------------------------------- #
# Subquery scoping (the regression that invented 20 phantom curation gaps)
# --------------------------------------------------------------------------- #


def test_subquery_filter_column_belongs_to_the_subquery_table(corpus):
    # sqlglot's outer Scope.columns also reports the inner query's columns; read
    # naively, `city` gets blamed on `orders`, which has no such column.
    got = refs(
        corpus,
        "SELECT amount FROM orders WHERE customer_id IN "
        "(SELECT customer_id FROM customers WHERE city = 'Sydney')",
    )
    assert (CUSTOMERS, "city") in got.refs
    assert (ORDERS, "city") not in got.refs


def test_bare_name_binds_outward_when_the_inner_table_lacks_it(corpus):
    # SQL binds a bare name to the innermost scope that HAS it. `amount` is on
    # orders, not customers, so the reference inside the subquery is the OUTER
    # table's — the shape BIRD gold uses constantly. Stopping at the inner scope
    # would report `customers.amount` as a curation gap that does not exist.
    got = refs(
        corpus,
        "SELECT order_id FROM orders WHERE customer_id IN "
        "(SELECT customer_id FROM customers WHERE amount > 5)",
    )
    assert (ORDERS, "amount") in got.refs
    assert (CUSTOMERS, "amount") not in got.refs


def test_correlated_outer_alias_resolves(corpus):
    got = refs(
        corpus,
        "SELECT o.amount FROM orders o WHERE o.customer_id = "
        "(SELECT c.customer_id FROM customers c WHERE c.name = o.order_id)",
    )
    assert (ORDERS, "order_id") in got.refs  # o.order_id, from the inner scope
    assert (CUSTOMERS, "name") in got.refs


def test_nested_subqueries_each_keep_their_own_columns(corpus):
    got = refs(
        corpus,
        "SELECT order_id FROM orders WHERE customer_id IN "
        "(SELECT customer_id FROM customers WHERE city IN "
        "(SELECT city FROM customers WHERE name = 'x'))",
    )
    assert got.refs == frozenset(
        {
            (ORDERS, "order_id"),
            (ORDERS, "customer_id"),
            (CUSTOMERS, "customer_id"),
            (CUSTOMERS, "city"),
            (CUSTOMERS, "name"),
        }
    )


# --------------------------------------------------------------------------- #
# Stars
# --------------------------------------------------------------------------- #


def test_select_star_is_counted_not_expanded(corpus):
    got = refs(corpus, "SELECT * FROM orders WHERE amount > 1")
    assert got.stars == 1
    # Expanding would make corpus coverage a function of the corpus being scored.
    assert got.refs == frozenset({(ORDERS, "amount")})


def test_qualified_star_is_counted(corpus):
    got = refs(corpus, "SELECT o.* FROM orders o")
    assert got.stars == 1
    assert got.refs == frozenset()


def test_count_star_is_not_a_star_projection(corpus):
    # Every counting question in BIRD would otherwise be flagged.
    got = refs(corpus, "SELECT COUNT(*) FROM orders")
    assert got.stars == 0
    assert got.refs == frozenset()


# --------------------------------------------------------------------------- #
# CTEs / derived tables
# --------------------------------------------------------------------------- #


def test_cte_body_columns_resolve_and_the_output_ref_is_derived(corpus):
    got = refs(
        corpus,
        "WITH big AS (SELECT order_id, amount FROM orders WHERE amount > 10) "
        "SELECT big.amount FROM big",
    )
    assert (ORDERS, "amount") in got.refs and (ORDERS, "order_id") in got.refs
    assert got.derived == ("big.amount",)


def test_derived_table_output_ref_is_derived(corpus):
    got = refs(corpus, "SELECT s.x FROM (SELECT amount AS x FROM orders) s")
    assert (ORDERS, "amount") in got.refs
    assert got.derived == ("s.x",)


def test_union_arms_resolve_independently(corpus):
    got = refs(corpus, "SELECT amount FROM orders UNION SELECT name FROM customers")
    assert got.refs == frozenset({(ORDERS, "amount"), (CUSTOMERS, "name")})


# --------------------------------------------------------------------------- #
# Degenerate input
# --------------------------------------------------------------------------- #


def test_unparseable_sql_yields_empty_not_an_exception(corpus):
    got = refs(corpus, "this is not sql (((")
    assert got == type(got)()


def test_sql_with_no_columns(corpus):
    assert refs(corpus, "SELECT 1").refs == frozenset()


def test_schema_qualified_table_wins_over_a_bare_name_collision():
    """A pooled corpus repeats table names across schemas; the qualifier decides."""
    a = TableAsset(id="tbl_a_country", schema="a", physical_name="country", columns=[_col("n")])
    b = TableAsset(id="tbl_b_country", schema="b", physical_name="country", columns=[_col("n")])
    pooled = Corpus(assets=[a, b])
    assert gold_table_ids(pooled, 'SELECT "n" FROM "b"."country"', dialect="postgres") == (
        frozenset({"tbl_b_country"})
    )
    # ... and a bare, ambiguous name resolves to neither rather than to whichever
    # asset loaded last.
    assert gold_table_ids(pooled, "SELECT n FROM country", dialect="postgres") == frozenset()


# --------------------------------------------------------------------------- #
# Scoring: governance split, licensing, width curve
# --------------------------------------------------------------------------- #


def test_excluded_and_suspect_columns_are_governance_not_misses(corpus):
    analyst = corpus.for_analyst()
    item = EvalItem(
        question="order amounts and notes",
        sql="SELECT amount, internal_note, legacy_total FROM orders",
    )
    report = evaluate_retrieval(
        analyst, [item], top_k=8, dialect="postgres", raw_corpus=corpus
    )
    q = report.per_question[0]
    assert (ORDERS, "internal_note") in q.col_excluded
    assert (ORDERS, "legacy_total") in q.col_suspect
    # Both exist in the corpus, so coverage is perfect ...
    assert report.corpus_column_coverage == 1.0
    # ... and neither is in the denominator of licensed recall.
    assert q.col_serveable == frozenset({(ORDERS, "amount")})
    assert report.licensed_column_recall == 1.0


def test_licensed_recall_drops_when_the_gold_table_is_not_licensed(corpus):
    analyst = corpus.for_analyst()
    # A question with no lexical overlap with `wide` still needs one of its columns.
    item = EvalItem(question="customer city", sql="SELECT c29 FROM wide")
    report = evaluate_retrieval(
        analyst, [item], top_k=1, dialect="postgres", raw_corpus=corpus
    )
    q = report.per_question[0]
    assert q.col_serveable == frozenset({(WIDE, "c29")})
    assert WIDE not in q.licensed
    assert q.col_licensed == frozenset()
    assert report.licensed_column_recall == 0.0
    # ... while the column itself is perfectly well curated: the two numbers
    # localise the failure to routing rather than to the corpus.
    assert report.corpus_column_coverage == 1.0


def test_width_curve_reports_the_cap_that_drops_a_gold_column(corpus):
    analyst = corpus.for_analyst()
    # The question shares no vocabulary with `c30`, so the relevance ranking
    # cannot rescue it and a tight budget cuts it.
    item = EvalItem(question="a wide fact table", sql="SELECT c30 FROM wide")
    report = evaluate_retrieval(
        analyst,
        [item],
        top_k=8,
        dialect="postgres",
        raw_corpus=corpus,
        width_budgets=(4, 31),
    )
    q = report.per_question[0]
    assert q.col_licensed == frozenset({(WIDE, "c30")})
    assert q.gold_table_width == 31
    curve = {p.budget: p for p in report.width_curve()}
    assert (curve[4].questions_dropping, curve[4].columns_dropped) == (1, 1)
    assert curve[4].question_rate == 1.0
    # A budget at least as wide as the table never drops anything.
    assert (curve[31].questions_dropping, curve[31].columns_dropped) == (0, 0)


def test_width_curve_spares_a_gold_column_the_question_names(corpus):
    """The cap selects by relevance, so naming the column in the question saves it."""
    analyst = corpus.for_analyst()
    report = evaluate_retrieval(
        analyst,
        [EvalItem(question="wide table c30", sql="SELECT c30 FROM wide")],
        top_k=8,
        dialect="postgres",
        raw_corpus=corpus,
        width_budgets=(4,),
    )
    assert report.width_curve()[0].questions_dropping == 0


def test_width_curve_is_empty_when_no_budgets_are_swept(corpus):
    analyst = corpus.for_analyst()
    item = EvalItem(question="amounts", sql="SELECT amount FROM orders")
    report = evaluate_retrieval(analyst, [item], dialect="postgres", raw_corpus=corpus)
    assert report.width_curve() == []
    assert "WIDTH CURVE" not in report.format()


def test_merge_reports_pools_questions_and_skips(corpus):
    analyst = corpus.for_analyst()
    one = evaluate_retrieval(
        analyst,
        [EvalItem(question="amounts", sql="SELECT amount FROM orders")],
        dialect="postgres",
        raw_corpus=corpus,
    )
    two = evaluate_retrieval(
        analyst,
        [
            EvalItem(question="cities", sql="SELECT city FROM customers"),
            EvalItem(question="nothing", sql="SELECT 1"),
        ],
        dialect="postgres",
        raw_corpus=corpus,
    )
    pooled = merge_reports([one, two])
    assert pooled.n == 2
    assert pooled.skipped == 1
    assert pooled.n_col_gold == 2
    assert merge_reports([]).n == 0


def test_report_format_names_every_bucket(corpus):
    analyst = corpus.for_analyst()
    report = evaluate_retrieval(
        analyst,
        [EvalItem(question="all orders", sql="SELECT * FROM orders")],
        dialect="postgres",
        raw_corpus=corpus,
        width_budgets=(8,),
    )
    text = report.format()
    for fragment in (
        "corpus coverage",
        "licensed column recall",
        "governance.excluded",
        "reliability=suspect",
        "unresolvable bare names",
        "SELECT * projections",
    ):
        assert fragment in text
