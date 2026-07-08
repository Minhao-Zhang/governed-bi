"""Smoke tests for the corpus layer: schemas, IDs, validator, loader contract."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from governed_bi.corpus import (
    TableAsset,
    is_green,
    load_corpus,
    parse_asset,
    validate_corpus,
)
from governed_bi.corpus.ids import derive_column_id, is_valid_id

EXAMPLE_DB = Path(__file__).resolve().parents[1] / "corpus" / "california_schools"


# --------------------------------------------------------------------------- #
# IDs
# --------------------------------------------------------------------------- #


def test_id_conventions():
    assert is_valid_id("table", "tbl_california_schools_frpm")
    assert is_valid_id("join", "join_frpm_schools")
    assert is_valid_id("few_shot", "fs_california_schools_003")
    assert is_valid_id("negative_example", "neg_california_schools_002")
    # wrong prefix / shape
    assert not is_valid_id("table", "frpm")
    assert not is_valid_id("few_shot", "fs_california_schools")  # missing numeric suffix
    assert not is_valid_id("table", "Tbl_Upper")  # not lowercase


def test_derive_column_id():
    assert (
        derive_column_id("tbl_california_schools_schools", "lie_0")
        == "col_california_schools_schools_lie_0"
    )


# --------------------------------------------------------------------------- #
# Schemas (parse / validation)
# --------------------------------------------------------------------------- #


def test_parse_asset_discriminates_by_type():
    asset = parse_asset(
        {
            "asset_type": "table",
            "id": "tbl_demo_t",
            "db": "demo",
            "physical_name": "biao_1",
        }
    )
    assert isinstance(asset, TableAsset)


def test_parse_asset_rejects_unknown_field():
    with pytest.raises(ValidationError):
        parse_asset(
            {
                "asset_type": "table",
                "id": "tbl_demo_t",
                "db": "demo",
                "physical_name": "biao_1",
                "nonsense_field": True,  # extra="forbid"
            }
        )


def test_parse_asset_rejects_bad_enum():
    with pytest.raises(ValidationError):
        parse_asset(
            {
                "asset_type": "rule",
                "id": "rule_x",
                "kind": "not_a_rule_kind",  # invalid enum
                "statement": "x",
            }
        )


# --------------------------------------------------------------------------- #
# Example corpus: load + validate green
# --------------------------------------------------------------------------- #


def test_example_corpus_is_ci_green():
    corpus = load_corpus(EXAMPLE_DB.parent, db=EXAMPLE_DB.name)
    findings = validate_corpus(corpus.assets)
    assert is_green(findings), "\n".join(str(f) for f in findings)
    assert len(corpus.tables()) == 2
    assert len(corpus.skills) == 1


def test_validator_catches_dangling_reference():
    corpus = load_corpus(EXAMPLE_DB.parent, db=EXAMPLE_DB.name)
    metric = next(a for a in corpus.assets if a.id == "metric_frpm_rate")
    metric.base_table = "tbl_does_not_exist"
    findings = validate_corpus(corpus.assets)
    assert any(f.code == "dangling-ref" for f in findings)


# --------------------------------------------------------------------------- #
# Consumption contract (loader)
# --------------------------------------------------------------------------- #


def test_for_server_strips_audit():
    corpus = load_corpus(EXAMPLE_DB.parent, db=EXAMPLE_DB.name)
    server_view = corpus.for_server()
    for asset in server_view.assets:
        assert getattr(asset, "audit", None) is None
        if isinstance(asset, TableAsset):
            for col in asset.columns:
                assert col.audit is None


def test_for_server_drops_excluded_columns():
    corpus = load_corpus(EXAMPLE_DB.parent, db=EXAMPLE_DB.name)
    frpm = next(a for a in corpus.assets if a.id == "tbl_california_schools_frpm")
    # exclude the suspect column, then confirm it disappears from the server view
    suspect = next(c for c in frpm.columns if c.physical_name == "lie_12")
    suspect.governance.excluded = True

    server_view = corpus.for_server()
    frpm_view = next(a for a in server_view.assets if a.id == "tbl_california_schools_frpm")
    assert all(c.physical_name != "lie_12" for c in frpm_view.columns)
