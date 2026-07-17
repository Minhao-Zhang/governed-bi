"""Tests for the curator authoring scaffold: proposer, adversary, loop.

Deterministic and offline. Fast unit cases build small ``TableAsset`` inputs
inline; two cases exercise the committed artefacts (the authored
``corpus/beer_factory`` tree and, when present, the vendored SQLite DB).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from governed_bi.corpus import load_corpus
from governed_bi.corpus.schemas import (
    Audit,
    Column,
    ColumnRole,
    LogicalType,
    Provenance,
    ProvenanceSource,
    ProvenanceStatus,
    TableAsset,
)
from governed_bi.curator import (
    CurationResult,
    HeuristicProposer,
    Proposer,
    curate,
    profile_database,
    review,
)

EXAMPLE_CORPUS = Path(__file__).resolve().parents[1] / "corpus" / "beer_factory"
BIRD_DB = Path(__file__).resolve().parents[1] / "data" / "bird" / "beer_factory.sqlite"


def _facts_column(name: str, logical: LogicalType, *, is_unique: bool, references=None) -> Column:
    """A Facts-only column (Inference tier empty), as the profiler emits."""
    return Column(
        physical_name=name,
        physical_type=logical.value.upper(),
        logical_type=logical,
        nullable=True,
        is_unique=is_unique,
        references=references,
    )


def _orders_table() -> TableAsset:
    """A Facts-only table: one unique *ID, one numeric non-key, one string."""
    return TableAsset(
        id="tbl_demo_orders",
        schema="demo",
        physical_name="orders",
        columns=[
            _facts_column("OrderID", LogicalType.integer, is_unique=True),
            _facts_column("amount", LogicalType.decimal, is_unique=False),
            _facts_column("note", LogicalType.string, is_unique=False),
        ],
    )


# --------------------------------------------------------------------------- #
# Proposer
# --------------------------------------------------------------------------- #


def test_heuristic_proposer_satisfies_protocol():
    assert isinstance(HeuristicProposer(), Proposer)


def test_heuristic_proposer_fills_roles_and_provenance():
    [table] = HeuristicProposer().propose([_orders_table()])

    by_name = {c.physical_name: c for c in table.columns}
    assert by_name["OrderID"].role is ColumnRole.primary_key  # unique *ID
    assert by_name["amount"].role is ColumnRole.measure  # numeric non-key
    assert by_name["note"].role is ColumnRole.dimension  # string

    for col in table.columns:
        assert col.description is None  # prose is the LLM proposer's job
        assert col.confidence == 0.5
        assert col.audit is not None
        assert col.audit.provenance.source is ProvenanceSource.curator
        assert col.audit.provenance.status is ProvenanceStatus.proposed

    # The table itself is stamped so it is a promotable proposed unit.
    assert table.audit is not None
    assert table.audit.provenance.status is ProvenanceStatus.proposed


def test_heuristic_proposer_marks_foreign_key_when_references_set():
    table = TableAsset(
        id="tbl_demo_lines",
        schema="demo",
        physical_name="lines",
        columns=[
            _facts_column(
                "CustomerID",
                LogicalType.integer,
                is_unique=False,
                references="col_demo_customers_CustomerID",
            )
        ],
    )
    [proposed] = HeuristicProposer().propose([table])
    assert proposed.columns[0].role is ColumnRole.foreign_key


def test_heuristic_proposer_sole_unique_non_id_is_primary_key():
    """A unique column that is the table's only unique one is a key even without
    an *ID name."""
    table = TableAsset(
        id="tbl_demo_codes",
        schema="demo",
        physical_name="codes",
        columns=[
            _facts_column("slug", LogicalType.string, is_unique=True),
            _facts_column("label", LogicalType.string, is_unique=False),
        ],
    )
    [proposed] = HeuristicProposer().propose([table])
    by_name = {c.physical_name: c for c in proposed.columns}
    assert by_name["slug"].role is ColumnRole.primary_key
    assert by_name["label"].role is ColumnRole.dimension


def test_heuristic_proposer_does_not_mutate_input():
    table = _orders_table()
    HeuristicProposer().propose([table])
    assert table.audit is None
    assert all(c.role is None and c.confidence is None for c in table.columns)


# --------------------------------------------------------------------------- #
# AssetBag.read_corpus
# --------------------------------------------------------------------------- #


def test_read_corpus_unknown_table_returns_error_not_raises():
    """Regression: the fix-pass agent calls ``read_corpus(table="restaurant")``
    using the *schema* name (the bag is keyed by physical table names), which
    used to raise ``KeyError`` before the unknown-table guard ran — crashing the
    whole curated_sme fix-pass. It must return a recoverable error string instead."""
    from governed_bi.curator.asset_bag import AssetBag

    bag = AssetBag.from_tables("restaurant", [_orders_table()])
    # ``restaurant`` is the schema, not a physical table (which is ``orders``).
    out = bag.read_corpus(table="restaurant")
    assert out.startswith("error: unknown table='restaurant'")
    assert "orders" in out  # lists known tables so the agent can self-correct
    # The valid table still renders.
    assert "[table] orders" in bag.read_corpus(table="orders")


def test_upsert_term_coerces_and_rejects_column_binding():
    """Regression: the curator agent does not know the ``col_<table>_<column>``
    id derivation, so left to free text it wrote ``term.binding.asset_id`` as
    ``tbl_x.col`` / ``physical.col`` — a dangling reference that the (retired)
    stochastic fix-pass could not repair and doubled instead. ``upsert_term`` now
    coerces a resolvable ``table.column`` spelling to the canonical id and rejects
    an unresolvable one outright (never persisting a dangling binding)."""
    from governed_bi.corpus.validate import validate_corpus
    from governed_bi.curator.asset_bag import AssetBag

    bag = AssetBag.from_tables("demo", [_orders_table()])

    # physical 'table.column' is coerced to the loader-derived id.
    msg = bag.upsert_term(
        "amount", binding_asset_type="column", binding_asset_id="orders.amount"
    )
    assert msg.startswith("ok:")
    assert bag.terms["term_demo_amount"].binding.asset_id == "col_demo_orders_amount"

    # the '<table_id>.col' shape the agent produced in prod is coerced too.
    bag.upsert_term(
        "amt2", binding_asset_type="column", binding_asset_id="tbl_demo_orders.amount"
    )
    assert bag.terms["term_demo_amt2"].binding.asset_id == "col_demo_orders_amount"

    # an unresolvable binding is refused and NOT persisted.
    bad = bag.upsert_term(
        "ghost", binding_asset_type="column", binding_asset_id="orders.nope"
    )
    assert bad.startswith("error:")
    assert "term_demo_ghost" not in bag.terms

    assert validate_corpus(bag.all_assets()) == []


def test_repair_term_bindings_resolves_malformed_in_place():
    """Deterministic reference repair: a term carrying a legacy malformed column
    binding is rewritten to the canonical id in place, so the fix-pass never has
    to hand a machine-fixable dangling ref to a stochastic agent."""
    from governed_bi.corpus.schemas import TermAsset, TermBinding
    from governed_bi.corpus.validate import validate_corpus
    from governed_bi.curator.asset_bag import AssetBag

    bag = AssetBag.from_tables("demo", [_orders_table()])
    # Plant a term with the malformed binding the old fix-pass produced.
    bag.terms["term_demo_total"] = TermAsset(
        id="term_demo_total",
        name="total",
        binding=TermBinding(asset_type="column", asset_id="tbl_demo_orders.amount"),
    )
    assert len(validate_corpus(bag.all_assets())) == 1  # dangling before repair

    assert bag.repair_term_bindings() == 1
    assert bag.terms["term_demo_total"].binding.asset_id == "col_demo_orders_amount"
    assert validate_corpus(bag.all_assets()) == []


# --------------------------------------------------------------------------- #
# Adversary
# --------------------------------------------------------------------------- #


def test_review_green_on_example_corpus():
    corpus = load_corpus(EXAMPLE_CORPUS.parent, schema=EXAMPLE_CORPUS.name)
    findings = review(corpus.assets)
    assert findings == [], "\n".join(str(f) for f in findings)


def test_review_flags_foreign_key_without_references():
    table = TableAsset(
        id="tbl_demo_bad",
        schema="demo",
        physical_name="bad",
        columns=[
            Column(
                physical_name="RefID",
                physical_type="INTEGER",
                logical_type=LogicalType.integer,
                nullable=True,
                is_unique=False,
                role=ColumnRole.foreign_key,  # claims FK but names no target
            )
        ],
        audit=Audit(
            provenance=Provenance(
                source=ProvenanceSource.curator, status=ProvenanceStatus.proposed
            )
        ),
    )
    findings = review([table])
    assert any(f.code == "fk-missing-ref" for f in findings)


def test_review_flags_missing_provenance():
    table = TableAsset(id="tbl_demo_noprov", schema="demo", physical_name="noprov")  # audit is None
    findings = review([table])
    assert any(f.code == "missing-provenance" for f in findings)


# --------------------------------------------------------------------------- #
# Loop
# --------------------------------------------------------------------------- #


def test_curate_reaches_green_and_promotes_to_draft():
    result = curate([_orders_table()], HeuristicProposer())

    assert isinstance(result, CurationResult)
    assert result.green is True
    assert result.findings == []
    assert result.rounds == 1

    [table] = result.assets
    assert table.audit.provenance.status is ProvenanceStatus.draft
    for col in table.columns:
        assert col.audit.provenance.status is ProvenanceStatus.draft


# --------------------------------------------------------------------------- #
# Integration: profile the vendored BIRD DB, then curate (skipped if absent)
# --------------------------------------------------------------------------- #


@pytest.mark.skipif(not BIRD_DB.exists(), reason="vendored beer_factory.sqlite not present")
def test_curate_end_to_end_from_profiled_facts():
    from governed_bi.gateway import SqliteConnector

    conn = SqliteConnector(BIRD_DB)
    try:
        tables = profile_database(conn, schema="beer_factory")
        result = curate(tables, HeuristicProposer(), connector=conn)
    finally:
        conn.close()

    assert result.green is True, "\n".join(str(f) for f in result.findings)
    assert result.assets, "profiling produced no tables"
    for table in result.assets:
        assert table.audit.provenance.status is ProvenanceStatus.draft
        # Every column got a role, and none carries invented prose.
        for col in table.columns:
            assert col.role is not None
            assert col.description is None
