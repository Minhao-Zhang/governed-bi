"""Smoke tests for the corpus layer: schemas, IDs, validator, loader contract."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from governed_bi.corpus import (
    MetricAsset,
    NoteAsset,
    TableAsset,
    is_green,
    load_corpus,
    parse_asset,
    validate_corpus,
    write_corpus,
)
from governed_bi.corpus.cli import main as cli_main
from governed_bi.corpus.ids import derive_column_id, is_valid_id

EXAMPLE_DB = Path(__file__).resolve().parents[1] / "corpus" / "beer_factory"


# --------------------------------------------------------------------------- #
# IDs
# --------------------------------------------------------------------------- #


def test_id_conventions():
    assert is_valid_id("table", "tbl_beer_factory_customers")
    assert is_valid_id("join", "join_transaction_customers")
    assert is_valid_id("few_shot", "fs_beer_factory_001")
    assert is_valid_id("negative_example", "neg_beer_factory_001")
    assert is_valid_id("note", "note_boolean_flags")
    assert not is_valid_id("rule", "rule_boolean_flags")
    # wrong prefix / shape
    assert not is_valid_id("table", "customers")
    assert not is_valid_id("few_shot", "fs_beer_factory")  # missing numeric suffix
    assert not is_valid_id("table", "Tbl_Upper")  # not lowercase


def test_derive_column_id():
    assert (
        derive_column_id("tbl_beer_factory_customers", "CustomerID")
        == "col_beer_factory_customers_CustomerID"
    )


# --------------------------------------------------------------------------- #
# Schemas (parse / validation)
# --------------------------------------------------------------------------- #


def test_parse_asset_discriminates_by_type():
    asset = parse_asset(
        {
            "asset_type": "table",
            "id": "tbl_demo_t",
            "schema": "demo",
            "physical_name": "t_1",
        }
    )
    assert isinstance(asset, TableAsset)


def test_parse_asset_rejects_unknown_field():
    with pytest.raises(ValidationError):
        parse_asset(
            {
                "asset_type": "table",
                "id": "tbl_demo_t",
                "schema": "demo",
                "physical_name": "t_1",
                "nonsense_field": True,  # extra="forbid"
            }
        )


def test_parse_asset_rejects_bad_enum():
    with pytest.raises(ValidationError):
        parse_asset(
            {
                "asset_type": "note",
                "id": "note_x",
                "kind": "not_a_note_kind",  # invalid enum
                "summary": "x",
            }
        )


# --------------------------------------------------------------------------- #
# Example corpus: load + validate green
# --------------------------------------------------------------------------- #


def test_example_corpus_is_ci_green():
    corpus = load_corpus(EXAMPLE_DB.parent, schema=EXAMPLE_DB.name)
    findings = validate_corpus(corpus.assets)
    assert is_green(findings), "\n".join(str(f) for f in findings)
    assert len(corpus.tables()) == 5
    assert sum(isinstance(a, NoteAsset) for a in corpus.assets) == 2


def test_validator_catches_dangling_reference():
    corpus = load_corpus(EXAMPLE_DB.parent, schema=EXAMPLE_DB.name)
    metric = next(a for a in corpus.assets if a.id == "metric_revenue")
    metric.base_table = "tbl_does_not_exist"
    findings = validate_corpus(corpus.assets)
    assert any(f.code == "dangling-ref" for f in findings)


# --------------------------------------------------------------------------- #
# Consumption contract (loader)
# --------------------------------------------------------------------------- #


def test_for_server_strips_audit():
    corpus = load_corpus(EXAMPLE_DB.parent, schema=EXAMPLE_DB.name)
    server_view = corpus.for_analyst()
    for asset in server_view.assets:
        assert getattr(asset, "audit", None) is None
        if isinstance(asset, TableAsset):
            for col in asset.columns:
                assert col.audit is None


def test_for_server_drops_excluded_columns():
    corpus = load_corpus(EXAMPLE_DB.parent, schema=EXAMPLE_DB.name)
    tx = next(a for a in corpus.assets if a.id == "tbl_beer_factory_transaction")
    # the PII column ships excluded in the corpus...
    assert any(
        c.physical_name == "CreditCardNumber" and c.governance.excluded for c in tx.columns
    )
    # ...and must be absent from the server view.
    server_view = corpus.for_analyst()
    tx_view = next(a for a in server_view.assets if a.id == "tbl_beer_factory_transaction")
    assert all(c.physical_name != "CreditCardNumber" for c in tx_view.columns)


# --------------------------------------------------------------------------- #
# Serialize (write_corpus) round-trip
# --------------------------------------------------------------------------- #


def test_write_corpus_round_trip(tmp_path):
    """Load the example, write it out, load it back: same assets, still green."""
    src = load_corpus(EXAMPLE_DB.parent, schema="beer_factory")
    write_corpus(tmp_path, "beer_factory", src.assets)
    back = load_corpus(tmp_path, schema="beer_factory")

    assert is_green(validate_corpus(back.assets))
    assert {a.id for a in back.assets} == {a.id for a in src.assets}
    # Inference details survive the round trip.
    metric = next(a for a in back.assets if a.id == "metric_revenue")
    assert metric.base_table == "tbl_beer_factory_transaction"
    join = next(a for a in back.assets if a.id == "join_transaction_customers")
    assert join.on == "transaction.CustomerID = customers.CustomerID"  # the `on:` key survives
    customers = next(a for a in back.assets if a.id == "tbl_beer_factory_customers")
    suspect = next(c for c in customers.columns if c.physical_name == "ZipCode")
    assert suspect.reliability.status.value == "suspect"
    note = next(a for a in back.assets if a.id == "note_beer_factory_routing")
    assert note.activation == "always"
    assert note.normative_force == "advisory"
    assert note.body and "Routing triggers" in note.body


def test_note_scope_sentinels_and_dangling_refs():
    table = parse_asset(
        {
            "asset_type": "table",
            "id": "tbl_demo_orders",
            "schema": "demo",
            "physical_name": "orders",
        }
    )
    valid = NoteAsset(
        id="note_sentinels",
        kind="context",
        scope=["schema:demo", "db:main", "tbl_demo_orders"],
        summary="Scoped context.",
    )
    assert validate_corpus([table, valid]) == []

    invalid = valid.model_copy(update={"id": "note_bad_scope", "scope": ["schema:nope"]})
    assert any(f.code == "dangling-ref" for f in validate_corpus([table, invalid]))


def test_note_publication_status_drift_is_reported():
    note = NoteAsset.model_validate(
        {
            "id": "note_drift",
            "kind": "context",
            "summary": "Context.",
            "publication_status": "draft",
            "audit": {"provenance": {"source": "curator", "status": "certified"}},
        }
    )
    assert [f.code for f in validate_corpus([note])] == ["publication-status-drift"]


def test_always_note_budget_is_reported():
    notes = [
        NoteAsset(id=f"note_global_{i}", kind="context", summary="x" * 250)
        for i in range(9)
    ]
    findings = validate_corpus(notes)
    assert sum(f.code == "always-note-budget" for f in findings) == 2


def _schema_with_notes(schema: str, *, count: int, chars: int) -> list:
    """A table plus ``count`` schema-scoped always-notes of ``chars`` each."""
    table = TableAsset(id=f"tbl_{schema}_t", schema=schema, physical_name="t")
    notes = [
        NoteAsset(
            id=f"note_{schema}_{i}",
            kind="context",
            scope=[f"schema:{schema}"],
            summary="x" * chars,
        )
        for i in range(count)
    ]
    return [table, *notes]


def test_always_note_budget_is_per_schema_not_pooled():
    """The 2026-07-30 false positive: the pooled data-lake corpus summed a per-turn
    budget across 57 schemas and disqualified a 1351-question run, while its worst
    single schema held 1591 chars in 4 notes. Notes are ``schema:``-scoped, so no turn
    ever sees more than one schema's worth."""
    pooled: list = []
    for i in range(6):
        pooled += _schema_with_notes(f"db{i}", count=4, chars=400)  # 1600 chars each
    always_findings = [
        f for f in validate_corpus(pooled) if f.code == "always-note-budget"
    ]
    assert always_findings == [], "\n".join(str(f) for f in always_findings)

    # ...but one schema genuinely over the char budget is still caught, and the finding
    # names the scope that blew it rather than the pool.
    pooled += _schema_with_notes("hot", count=6, chars=400)  # 2400 chars
    always_findings = [
        f for f in validate_corpus(pooled) if f.code == "always-note-budget"
    ]
    assert len(always_findings) == 1
    assert "schema:hot" in always_findings[0].message
    assert "2400 characters" in always_findings[0].message


def test_always_note_count_cap_counts_scoped_notes_like_serve():
    """``apply_always_budget`` counts every always-note toward ``global_max``; the
    validator used to count only empty-scope ones, so the cap was dead for a curated
    corpus (nothing in one is globally scoped) and short caveats piled up unflagged."""
    over = _schema_with_notes("busy", count=9, chars=10)  # 9 notes, 90 chars
    findings = [f for f in validate_corpus(over) if f.code == "always-note-budget"]
    assert len(findings) == 1
    assert "9 always notes" in findings[0].message

    # A global note is paid for by every turn, so it counts against each schema group.
    two_schemas = _schema_with_notes("a", count=8, chars=10) + _schema_with_notes(
        "b", count=1, chars=10
    )
    assert [f for f in validate_corpus(two_schemas) if f.code == "always-note-budget"] == []
    two_schemas.append(NoteAsset(id="note_everywhere", kind="context", summary="x" * 10))
    findings = [f for f in validate_corpus(two_schemas) if f.code == "always-note-budget"]
    assert len(findings) == 1  # only schema:a crosses 8; schema:b sits at 2
    assert "schema:a" in findings[0].message


def test_always_note_budget_on_match_notes_are_exempt():
    """Only ``always`` notes are always-injected; an ``on_match`` caveat is gated by
    retrieval, which is why ``AssetBag.record_caveats`` only charges always-notes."""
    assets = _schema_with_notes("s", count=1, chars=10)
    assets += [
        NoteAsset(
            id=f"note_s_match_{i}",
            kind="context",
            scope=["schema:s"],
            activation="on_match",
            summary="x" * 500,
        )
        for i in range(9)
    ]
    assert [f for f in validate_corpus(assets) if f.code == "always-note-budget"] == []


def test_metric_expression_unparseable_is_reported():
    table = TableAsset(
        id="tbl_demo_orders",
        schema="demo",
        physical_name="orders",
    )
    bad = MetricAsset(
        id="metric_bad_expr",
        name="broken",
        base_table=table.id,
        expression="NOT VALID (((",
    )
    findings = validate_corpus([table, bad])
    assert any(f.code == "metric-expression-unparseable" for f in findings)
    assert any(f.asset_id == "metric_bad_expr" for f in findings)


def test_metric_expression_sum_style_is_ok():
    table = TableAsset(
        id="tbl_demo_orders",
        schema="demo",
        physical_name="orders",
    )
    good = MetricAsset(
        id="metric_ok_sum",
        name="total",
        base_table=table.id,
        expression="SUM(x)",
    )
    findings = validate_corpus([table, good])
    assert not any(f.code == "metric-expression-unparseable" for f in findings)
    assert is_green(findings)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def test_cli_validates_example_returns_zero(capsys):
    assert cli_main([str(EXAMPLE_DB)]) == 0
    assert "CI green" in capsys.readouterr().out


def test_cli_missing_path_exits_2():
    with pytest.raises(SystemExit) as exc:
        cli_main(["definitely/not/a/real/corpus/path"])
    assert exc.value.code == 2
