"""Smoke tests for the corpus layer: schemas, IDs, validator, loader contract."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from governed_bi.corpus import (
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
