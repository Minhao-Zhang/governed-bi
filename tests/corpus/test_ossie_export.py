"""The Ossie exporter: the mapping, the report, the round trip, and the upstream validator."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

from governed_bi.corpus import ossie_export as ox
from governed_bi.corpus import store

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ossie_fixture import fixture_assets  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
VALIDATE = REPO.parent / "ossie" / "validation" / "validate.py"
BIRD = REPO.parent / "BIRD-corpus"


def _export() -> tuple[dict[str, Any], ox.DatabaseReport]:
    models, reports, unplaced = ox.export_assets(fixture_assets())
    assert unplaced == []
    return models["shop"], reports["shop"]


def _data(entity: dict[str, Any]) -> dict[str, Any]:
    return ox._extension_data(entity)


def test_version_and_commit_are_written() -> None:
    model, _ = _export()
    assert model["version"] == "0.2.0.dev0"
    assert _data(model)["ossie_commit"] == "d85e9e1"
    assert model["custom_extensions"][0]["vendor_name"] == "GOVERNED_BI"


def test_join_on_is_parsed_into_column_lists() -> None:
    model, _ = _export()
    rel = next(r for r in model["relationships"] if r["name"] == "j_many_to_one")
    assert (rel["from"], rel["to"]) == ("orders", "customers")
    assert (rel["from_columns"], rel["to_columns"]) == (["customer_id"], ["id"])


def test_one_to_many_is_flipped_so_from_is_the_many_side() -> None:
    model, _ = _export()
    rel = next(r for r in model["relationships"] if r["name"] == "j_one_to_many")
    assert (rel["from"], rel["to"]) == ("orders", "customers")
    assert (rel["from_columns"], rel["to_columns"]) == (["customer_id"], ["id"])
    assert _data(rel)["cardinality"] == "one_to_many"


def test_many_to_many_goes_to_the_model_extension_not_the_relationships() -> None:
    model, report = _export()
    assert "j_many_to_many" not in {r["name"] for r in model["relationships"]}
    assert [j["id"] for j in _data(model)["many_to_many"]] == ["j_many_to_many"]
    assert report.many_to_many == 1


def test_a_join_without_cardinality_is_reported_and_not_guessed() -> None:
    model, report = _export()
    assert report.without_cardinality == ["j_no_cardinality"]
    assert "j_no_cardinality" not in {r["name"] for r in model["relationships"]}


def test_a_join_that_is_not_column_equality_is_reported() -> None:
    _, report = _export()
    assert list(report.unmapped_joins) == ["j_not_equality"]


def test_term_synonyms_land_in_ai_context_of_what_they_bind() -> None:
    model, _ = _export()
    orders = next(d for d in model["datasets"] if d["name"] == "orders")
    total = next(f for f in orders["fields"] if f["name"] == "total")
    customers = next(d for d in model["datasets"] if d["name"] == "customers")
    assert total["ai_context"]["synonyms"] == ["outlay"]
    assert customers["ai_context"]["synonyms"] == ["purchaser", "shopper"]
    assert {t["name"] for t in model["ai_context"]["terms"]} == {"buyer", "spend"}


def test_datatypes_follow_the_logical_and_physical_types() -> None:
    model, _ = _export()
    orders = next(d for d in model["datasets"] if d["name"] == "orders")
    types = {f["name"]: f["datatype"] for f in orders["fields"]}
    assert types == {"id": "Integer", "customer_id": "Integer", "total": "Float", "placed": "DateTimeTz"}


def test_unparseable_metric_expressions_are_counted() -> None:
    _, report = _export()
    assert report.metrics == 2
    assert report.unparsed_metrics == ["metric_broken"]


def test_the_round_trip_loses_only_what_is_listed() -> None:
    lost = ox.round_trip_loss(fixture_assets())
    assert lost == {("column", "physical_type"): 9}


def test_the_reader_restores_a_flipped_join() -> None:
    models, _, _ = ox.export_assets(fixture_assets())
    back = {a.id: a for a in ox.read_model(yaml.safe_load(ox._dump(models["shop"])))}
    join = back["j_one_to_many"]
    assert (join.left_table, join.right_table) == ("shop.customers", "shop.orders")  # type: ignore[union-attr]


def _validate(paths: list[Path]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(VALIDATE), *map(str, paths)], capture_output=True, text=True, check=False
    )


def _skip_without_validator() -> None:
    if not VALIDATE.is_file():
        pytest.skip("../ossie is absent, so there is no validate.py to run")


def test_the_fixture_export_passes_the_upstream_validator(tmp_path: Path) -> None:
    _skip_without_validator()
    models, _, _ = ox.export_assets([a for a in fixture_assets() if a.id != "metric_broken"])
    path = tmp_path / "shop.yaml"
    path.write_text(ox._dump(models["shop"]), encoding="utf-8")
    result = _validate([path])
    assert result.returncode == 0, result.stdout + result.stderr


def test_a_prose_metric_is_the_one_thing_the_upstream_validator_rejects(tmp_path: Path) -> None:
    _skip_without_validator()
    corpus = tmp_path / "corpus"
    for asset in fixture_assets():
        store.write(corpus, asset, namespace="shop")
    assert ox.export_corpus(corpus, tmp_path / "out")["load_problems"] == []
    result = _validate(sorted((tmp_path / "out").glob("*.yaml")))
    errors = [line for line in result.stdout.splitlines() if line.strip().startswith("[")]
    assert result.returncode == 1
    assert len(errors) == 1 and "Metric 'metric_broken'" in errors[0]


def test_every_bird_export_is_valid(tmp_path: Path) -> None:
    _skip_without_validator()
    if not BIRD.is_dir():
        pytest.skip("../BIRD-corpus is absent")
    report = ox.export_corpus(BIRD, tmp_path)
    assert report["totals"]["models"] == 57
    assert report["totals"]["unparsed_metrics"] == 0
    assert report["totals"]["without_cardinality"] == 2
    result = _validate(sorted(tmp_path.glob("*.yaml")))
    assert result.returncode == 0, result.stdout + result.stderr
    lost = ox.round_trip_loss(store.load(BIRD)[0])
    assert set(lost) == {("column", "physical_type")}
    json.loads((tmp_path / ox.REPORT_NAME).read_text(encoding="utf-8"))
