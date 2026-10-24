"""The corpus as Ossie: lossless, canonical, valid, and strict about what it reads (ADR 0022)."""

from __future__ import annotations

import dataclasses
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

from governed_bi.corpus import ossie
from governed_bi.corpus.parse import from_mapping
from governed_bi.corpus.schema import (
    Asset,
    Audit,
    Binding,
    Cardinality,
    ColumnAsset,
    ColumnRole,
    Governance,
    Provenance,
    ProvenanceSource,
    ProvenanceStatus,
    Reliability,
    ReliabilityStatus,
    TableAsset,
    TermAsset,
)
from governed_bi.registry.assets import AssetType

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ossie_fixture import fixture_assets  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
VALIDATE = REPO.parent / "ossie" / "validation" / "validate.py"


def _key(asset: Asset) -> tuple[str, str]:
    return (asset.asset_type.value, asset.id)


def _richer() -> list[Asset]:
    """The fixture plus every placement BIRD does not use."""
    audit = Audit(
        provenance=Provenance(source=ProvenanceSource.human, status=ProvenanceStatus.proposed), evidence="by hand"
    )
    out: list[Asset] = []
    for asset in fixture_assets():
        if isinstance(asset, TableAsset) and asset.id == "shop.orders":
            asset = dataclasses.replace(asset, grain="one row per order", row_count=12, audit=audit)
        if asset.id == "j_one_to_many":
            # A relationship's text is rebuilt from its columns, so only a qualified `on` stays one.
            asset = dataclasses.replace(asset, on="shop.customers.id = shop.orders.customer_id")
        if isinstance(asset, ColumnAsset) and asset.id.endswith(".id"):
            asset = dataclasses.replace(asset, role=ColumnRole.primary_key)
        if isinstance(asset, ColumnAsset) and asset.id == "shop.orders.customer_id":
            asset = dataclasses.replace(
                asset,
                role=ColumnRole.foreign_key,
                nullable=False,
                reliability=Reliability(status=ReliabilityStatus.suspect, note="often blank"),
                governance=Governance(excluded=True, reason="private", by="owner", at="2026-10-23"),
            )
        out.append(asset)
    out.append(
        TermAsset(
            id="term_shop_takings",
            name="takings",
            summary="takings: revenue.\n",
            synonyms=("take",),
            binding=Binding(AssetType.metric, "metric_revenue"),
        )
    )
    out.append(
        dataclasses.replace(
            next(a for a in fixture_assets() if a.id == "j_many_to_one"),
            id="j_one_to_one",
            right_table="shop.notes",
            on="shop.orders.id = shop.notes.order_id",
            cardinality=Cardinality.one_to_one,
            body="Each order has at most one note.",
        )
    )
    return out


def _round_trip(assets: list[Asset]) -> list[Asset]:
    back: list[Asset] = []
    for name, group in ossie.group_by_schema(assets).items():
        text = ossie.model_text(name, group)
        document = yaml.safe_load(text)
        back.extend(from_mapping(m) for m in ossie.read_model(document, where=name))
        assert ossie.model_text(name, sorted(back, key=_key)) == text, "write, read, write is not the same bytes"
    return back


def test_the_round_trip_loses_nothing() -> None:
    assets = _richer()
    assert sorted(_round_trip(assets), key=_key) == sorted(assets, key=_key)


def test_ai_context_holds_only_instructions() -> None:
    document = yaml.safe_load(ossie.model_text("shop", _richer()))

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if "ai_context" in node:
                assert set(node["ai_context"]) == {"instructions"}
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(document)


def test_one_to_many_is_stored_from_the_many_side_and_read_back_as_written() -> None:
    document = yaml.safe_load(ossie.model_text("shop", _richer()))
    relationship = next(r for r in document["relationships"] if r["name"] == "j_one_to_many")
    assert (relationship["from"], relationship["to"]) == ("orders", "customers")
    assert json.loads(relationship["custom_extensions"][0]["data"]) == {"cardinality": "one_to_many"}


def test_joins_ossie_cannot_express_stay_out_of_relationships() -> None:
    document = yaml.safe_load(ossie.model_text("shop", _richer()))
    names = {r["name"] for r in document["relationships"]}
    data = json.loads(document["custom_extensions"][0]["data"])
    assert {"j_many_to_many", "j_no_cardinality", "j_not_equality"}.isdisjoint(names)
    assert {j["id"] for j in data["joins"]} == {"j_many_to_many", "j_no_cardinality", "j_not_equality"}


def test_a_metric_over_its_own_fields_is_dataset_scoped() -> None:
    document = yaml.safe_load(ossie.model_text("shop", _richer()))
    orders = next(d for d in document["datasets"] if d["name"] == "orders")
    assert [m["name"] for m in orders["metrics"]] == ["metric_revenue"]
    assert [m["name"] for m in document["metrics"]] == ["metric_broken"]


@pytest.mark.parametrize(
    ("path", "value", "reason"),
    [
        (("ai_context",), {"instructions": "x", "synonyms": ["y"]}, "only `instructions`"),
        (("datasets", 0, "label"), "x", "are not read"),
        (("custom_extensions",), [{"vendor_name": "GOVERNED_BI", "data": '{"typo": 1}'}], "unknown GOVERNED_BI"),
        (("relationships", 0, "from_columns"), ["nope"], "must name fields"),
        (("version",), "0.1.1", "is not 0.2.0.dev0"),
    ],
)
def test_the_reader_refuses_what_it_does_not_place(path: tuple[Any, ...], value: Any, reason: str) -> None:
    document = yaml.safe_load(ossie.model_text("shop", _richer()))
    node = document
    for step in path[:-1]:
        node = node[step]
    node[path[-1]] = value
    with pytest.raises(ValueError, match=reason):
        ossie.read_model(document, where="shop")


def test_an_unbound_term_has_no_file() -> None:
    term = TermAsset(id="term_loose", name="loose", summary="loose: nothing.")
    with pytest.raises(ValueError, match="no schema claims"):
        ossie.group_by_schema([*fixture_assets(), term])


def test_the_written_model_passes_the_upstream_validator(tmp_path: Path) -> None:
    if not VALIDATE.is_file():
        pytest.skip("../ossie is absent, so there is no validate.py to run")
    path = tmp_path / "shop.yaml"
    assets = [a for a in _richer() if a.id != "metric_broken"]
    path.write_text(ossie.model_text("shop", assets), encoding="utf-8")
    result = subprocess.run([sys.executable, str(VALIDATE), str(path)], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr


BIRD = REPO.parent / "BIRD-corpus"


def test_bird_is_canonical_and_valid() -> None:
    """Every BIRD model loads with no problem, rewrites to its own bytes, and passes the upstream validator."""
    if not BIRD.is_dir():
        pytest.skip("../BIRD-corpus is absent")
    from governed_bi.corpus.store import load

    assets, problems = load(BIRD)
    assert problems == []
    groups = ossie.group_by_schema(assets)
    assert len(groups) == 57
    for name, group in groups.items():
        on_disk = (BIRD / name / f"{name}.yaml").read_text(encoding="utf-8")
        assert ossie.model_text(name, group) == on_disk, f"{name} is not in canonical form"
    if VALIDATE.is_file():
        paths = sorted(str(p) for p in BIRD.glob("*/*.yaml"))
        result = subprocess.run([sys.executable, str(VALIDATE), *paths], capture_output=True, text=True, check=False)
        assert result.returncode == 0, result.stdout[-2000:]
