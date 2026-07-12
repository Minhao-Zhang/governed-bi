"""D15: the schema-qualified guardrail + mode-conditional qualification.

These tests exercise the MULTI-SCHEMA capability (Postgres/Redshift default at
serve time). They run against a SYNTHETIC two-schema corpus built directly from
``TableAsset`` objects: a same-named table ``orders`` in ``schema="schema_a"`` and
``schema="schema_b"``, with a curated cross-schema ``JoinAsset``.

The single-schema / SQLite / BIRD path is covered (byte-for-byte) by
``test_guardrails.py`` and the rest of the suite; the final test here asserts that
``multi_schema=False`` reproduces today's single-schema behavior exactly.
"""

from __future__ import annotations

import pytest

from governed_bi.corpus import Corpus
from governed_bi.corpus.schemas import (
    Cardinality,
    Column,
    JoinAsset,
    LogicalType,
    Reliability,
    ReliabilityStatus,
    TableAsset,
)
from governed_bi.corpus.validate import validate_corpus
from governed_bi.gateway import GuardrailLayer, check, column_allowlist
from governed_bi.retrieval import RetrievalResult
from governed_bi.server.context import assemble_context

SCHEMA_A_ORDERS = "tbl_schema_a_orders"
SCHEMA_B_ORDERS = "tbl_schema_b_orders"


def _col(name: str, *, suspect: bool = False) -> Column:
    reliability = (
        Reliability(status=ReliabilityStatus.suspect, note="decoy")
        if suspect
        else Reliability()
    )
    return Column(
        physical_name=name,
        physical_type="INTEGER",
        logical_type=LogicalType.integer,
        nullable=True,
        is_unique=False,
        reliability=reliability,
    )


def _corpus() -> Corpus:
    """A two-schema corpus: ``orders`` in schema_a and schema_b, same physical name.

    ``amount`` is deliberately OK in schema_a but SUSPECT in schema_b, to prove the
    L3 allowlist keys do not collide across schemas.
    """
    schema_a = TableAsset(
        id=SCHEMA_A_ORDERS,
        schema="schema_a",
        physical_name="orders",
        columns=[_col("order_id"), _col("amount"), _col("region", suspect=True)],
    )
    schema_b = TableAsset(
        id=SCHEMA_B_ORDERS,
        schema="schema_b",
        physical_name="orders",
        columns=[_col("order_id"), _col("amount", suspect=True), _col("status")],
    )
    cross_join = JoinAsset(
        id="join_schema_a_orders_schema_b_orders",
        left_table=SCHEMA_A_ORDERS,
        right_table=SCHEMA_B_ORDERS,
        on="schema_a.orders.order_id = schema_b.orders.order_id",
        cardinality=Cardinality.one_to_one,
        confidence=0.99,
    )
    return Corpus(assets=[schema_a, schema_b, cross_join])


@pytest.fixture
def corpus() -> Corpus:
    return _corpus()


@pytest.fixture
def allowlist(corpus):
    return column_allowlist(corpus, multi_schema=True)


def _check_ms(sql, allowlist, allowed_tables, *, hard_block_suspect=True, default_schema=None):
    return check(
        sql,
        allowed_columns=set(allowlist.allowed),
        suspect_columns=allowlist.suspect,
        allowed_tables=frozenset(allowed_tables),
        hard_block_suspect=hard_block_suspect,
        dialect="sqlite",
        multi_schema=True,
        default_schema=default_schema,
    )


# --------------------------------------------------------------------------- #
# (a) The allowlist / licensed set is schema-qualified
# --------------------------------------------------------------------------- #


def test_allowlist_keys_are_schema_qualified(allowlist):
    # Three-part {schema}.{physical}.{column} keys; the two schemas never collide.
    assert "schema_a.orders.amount" in allowlist.allowed
    assert "schema_b.orders.amount" in allowlist.suspect
    assert "schema_a.orders.region" in allowlist.suspect
    assert all(ref.count(".") == 2 for ref in allowlist.allowed | allowlist.suspect)


def test_allowed_table_names_are_schema_qualified(corpus):
    retrieval = RetrievalResult(question="orders", table_ids=[SCHEMA_A_ORDERS, SCHEMA_B_ORDERS])
    ctx = assemble_context(
        corpus,
        retrieval,
        licensed_table_ids=frozenset({SCHEMA_A_ORDERS, SCHEMA_B_ORDERS}),
        multi_schema=True,
    )
    assert ctx.allowed_table_names() == {"schema_a.orders", "schema_b.orders"}
    assert ctx.physical_to_id()["schema_a.orders"] == SCHEMA_A_ORDERS
    assert ctx.physical_to_id()["schema_b.orders"] == SCHEMA_B_ORDERS


# --------------------------------------------------------------------------- #
# (b) A qualified name outside the licensed set is blocked at L4
# --------------------------------------------------------------------------- #


def test_off_scope_schema_is_blocked_at_l4(allowlist):
    # Only schema_a.orders is licensed; naming schema_b.orders reaches outside it.
    verdict = _check_ms(
        "SELECT o.order_id FROM schema_b.orders AS o", allowlist, {"schema_a.orders"}
    )
    assert not verdict.passed
    assert verdict.failed_layer is GuardrailLayer.term_semantics


def test_three_part_catalog_qualified_name_is_blocked(allowlist):
    # A catalog.schema.table reference still names one database; the catalog
    # qualifier reaches outside it and is rejected.
    verdict = _check_ms(
        "SELECT o.order_id FROM cat.schema_a.orders AS o", allowlist, {"schema_a.orders"}
    )
    assert not verdict.passed
    assert verdict.failed_layer is GuardrailLayer.term_semantics


# --------------------------------------------------------------------------- #
# (c) A bare name licensed in >1 schema is refused as ambiguous
# --------------------------------------------------------------------------- #


def test_bare_name_in_two_schemas_is_ambiguous(allowlist):
    verdict = _check_ms(
        "SELECT COUNT(*) AS n FROM orders", allowlist, {"schema_a.orders", "schema_b.orders"}
    )
    assert not verdict.passed
    assert verdict.failed_layer is GuardrailLayer.term_semantics
    assert "ambiguous" in (verdict.reason or "")


def test_bare_name_resolves_to_default_schema(allowlist):
    # A bare reference resolves ONLY to the designated default schema.
    ok = _check_ms(
        "SELECT o.order_id FROM orders AS o",
        allowlist,
        {"schema_a.orders"},
        default_schema="schema_a",
    )
    assert ok.passed
    # If the default schema is not the licensed one, the bare name is out of scope.
    blocked = _check_ms(
        "SELECT o.order_id FROM orders AS o",
        allowlist,
        {"schema_a.orders"},
        default_schema="schema_b",
    )
    assert not blocked.passed
    assert blocked.failed_layer is GuardrailLayer.term_semantics


# --------------------------------------------------------------------------- #
# (d) Same-named columns in the two schemas do not collide at L3
# --------------------------------------------------------------------------- #


def test_same_named_column_ok_in_one_schema(allowlist):
    # schema_a.orders.amount is OK -> allowed.
    verdict = _check_ms(
        "SELECT o.amount FROM schema_a.orders AS o",
        allowlist,
        {"schema_a.orders", "schema_b.orders"},
    )
    assert verdict.passed


def test_fully_qualified_column_resolves_against_its_schema(allowlist):
    # A ``schema.table.column`` reference (no alias) keys the allowlist on the
    # written schema: schema_a.orders.amount is OK, schema_b.orders.amount is not.
    ok = _check_ms(
        "SELECT schema_a.orders.amount FROM schema_a.orders",
        allowlist,
        {"schema_a.orders", "schema_b.orders"},
    )
    assert ok.passed
    blocked = _check_ms(
        "SELECT schema_b.orders.amount FROM schema_b.orders",
        allowlist,
        {"schema_a.orders", "schema_b.orders"},
    )
    assert not blocked.passed
    assert blocked.failed_layer is GuardrailLayer.ast_column_allowlist


def test_same_named_column_suspect_in_other_schema_is_blocked(allowlist):
    # schema_b.orders.amount is SUSPECT -> hard-blocked in dev, despite schema_a's
    # amount being fine. The three-part key keeps them distinct.
    verdict = _check_ms(
        "SELECT o.amount FROM schema_b.orders AS o",
        allowlist,
        {"schema_a.orders", "schema_b.orders"},
    )
    assert not verdict.passed
    assert verdict.failed_layer is GuardrailLayer.ast_column_allowlist
    assert "suspect" in (verdict.reason or "")


def test_bare_suspect_column_across_schemas_fails_closed(allowlist):
    # A BARE `amount` with BOTH schemas in scope: OK in schema_a but SUSPECT in
    # schema_b, and the DB could bind the bare name to the decoy (leftmost-table
    # resolution). Multi-schema must fail closed and force qualification, rather
    # than let schema_a's allowed match paper over schema_b's suspect column.
    sql = (
        "SELECT amount "
        "FROM schema_a.orders AS a JOIN schema_b.orders AS b ON a.order_id = b.order_id"
    )
    verdict = _check_ms(sql, allowlist, {"schema_a.orders", "schema_b.orders"})
    assert not verdict.passed
    assert verdict.failed_layer is GuardrailLayer.ast_column_allowlist
    assert "suspect" in (verdict.reason or "")


def test_qualified_column_of_table_not_in_from_is_blocked(allowlist):
    # `schema_b.orders.status` names a table absent from FROM (only schema_a is).
    # The column exists corpus-wide, so L3's allowlist would admit it and L4 never
    # sees the reference; the guardrail must fail closed instead of relying on the
    # DB to reject the out-of-FROM reference.
    verdict = _check_ms(
        "SELECT schema_b.orders.status FROM schema_a.orders AS a", allowlist, {"schema_a.orders"}
    )
    assert not verdict.passed
    assert verdict.failed_layer is GuardrailLayer.ast_column_allowlist


# --------------------------------------------------------------------------- #
# (e) A cross-schema join over the curated JoinAsset is licensed / allowed
# (f) L5 does not misattribute the cross-schema same-name pair as a self-join
# --------------------------------------------------------------------------- #


def test_cross_schema_join_is_licensed_and_allowed(corpus, allowlist):
    retrieval = RetrievalResult(question="orders", table_ids=[SCHEMA_A_ORDERS, SCHEMA_B_ORDERS])
    ctx = assemble_context(
        corpus,
        retrieval,
        licensed_table_ids=frozenset({SCHEMA_A_ORDERS, SCHEMA_B_ORDERS}),
        multi_schema=True,
    )
    # The curated JoinAsset (both endpoints licensed) is presented as a join path.
    assert any(j.on == "schema_a.orders.order_id = schema_b.orders.order_id" for j in ctx.joins)

    sql = (
        "SELECT a.amount, b.status "
        "FROM schema_a.orders AS a JOIN schema_b.orders AS b ON a.order_id = b.order_id"
    )
    verdict = _check_ms(sql, allowlist, ctx.allowed_table_names())
    assert verdict.passed


def test_cross_schema_same_name_pair_is_not_scored_as_self_join(allowlist):
    # schema_a.orders and schema_b.orders share the physical name 'orders'. Keying
    # L5's self-join counter on the schema-qualified physical keeps them distinct,
    # so this legitimately-linked cross-schema join is NOT flagged as a cartesian
    # product (cost_estimate).
    sql = (
        "SELECT a.amount, b.status "
        "FROM schema_a.orders AS a JOIN schema_b.orders AS b ON a.order_id = b.order_id"
    )
    verdict = _check_ms(sql, allowlist, {"schema_a.orders", "schema_b.orders"})
    assert verdict.passed
    assert verdict.failed_layer is not GuardrailLayer.cost_estimate


def test_cross_schema_pair_without_link_is_still_a_cartesian(allowlist):
    # Two distinct (cross-schema) tables with no connecting predicate is still an
    # unconstrained cross join - L5 treats them as two tables, not a self-join.
    sql = "SELECT a.amount, b.status FROM schema_a.orders AS a, schema_b.orders AS b"
    verdict = _check_ms(sql, allowlist, {"schema_a.orders", "schema_b.orders"})
    assert not verdict.passed
    assert verdict.failed_layer is GuardrailLayer.cost_estimate


# --------------------------------------------------------------------------- #
# multi_schema=False reproduces today's single-schema behavior exactly
# --------------------------------------------------------------------------- #


def test_single_schema_mode_reproduces_today_behavior(corpus):
    single = column_allowlist(corpus)  # multi_schema=False (default): two-part keys
    assert all(ref.count(".") == 1 for ref in single.allowed | single.suspect)

    def _single(sql, allowed_tables):
        return check(
            sql,
            allowed_columns=set(single.allowed),
            suspect_columns=single.suspect,
            allowed_tables=frozenset(allowed_tables),
            hard_block_suspect=True,
            dialect="sqlite",
        )  # multi_schema defaults to False

    # A schema-qualified name is rejected as cross-namespace - exactly today's L4.
    blocked = _single("SELECT o.order_id FROM schema_a.orders AS o", {"orders"})
    assert not blocked.passed
    assert blocked.failed_layer is GuardrailLayer.term_semantics

    # A bare name in scope passes (bare column resolves against the sole base table).
    assert _single("SELECT o.order_id FROM orders AS o", {"orders"}).passed


def test_multi_schema_allows_what_single_schema_blocks(allowlist):
    # The same schema-qualified query single-schema rejects is allowed under the
    # mode gate when its (schema, table) is licensed.
    verdict = _check_ms(
        "SELECT o.order_id FROM schema_a.orders AS o", allowlist, {"schema_a.orders"}
    )
    assert verdict.passed


# --------------------------------------------------------------------------- #
# CI: (db, physical_name) uniqueness
# --------------------------------------------------------------------------- #


def test_validate_flags_duplicate_db_physical_name():
    dup = _corpus()
    # A second table in the SAME schema with the SAME physical name: the qualified
    # allowlist key would be ambiguous.
    dup.assets.append(
        TableAsset(
            id="tbl_schema_a_orders_dup",
            schema="schema_a",
            physical_name="orders",
            columns=[_col("order_id")],
        )
    )
    findings = validate_corpus(dup.assets)
    assert any(f.code == "ambiguous-physical-table" for f in findings)


def test_validate_allows_same_name_across_schemas():
    # schema_a.orders + schema_b.orders (different schemas) is legitimate multi-schema.
    findings = validate_corpus(_corpus().assets)
    assert not any(f.code == "ambiguous-physical-table" for f in findings)
