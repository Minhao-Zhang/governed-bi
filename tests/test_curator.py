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
        db="demo",
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
        db="demo",
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
        db="demo",
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
# Adversary
# --------------------------------------------------------------------------- #


def test_review_green_on_example_corpus():
    corpus = load_corpus(EXAMPLE_CORPUS.parent, db=EXAMPLE_CORPUS.name)
    findings = review(corpus.assets)
    assert findings == [], "\n".join(str(f) for f in findings)


def test_review_flags_foreign_key_without_references():
    table = TableAsset(
        id="tbl_demo_bad",
        db="demo",
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
    table = TableAsset(id="tbl_demo_noprov", db="demo", physical_name="noprov")  # audit is None
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
        tables = profile_database(conn, db="beer_factory")
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
