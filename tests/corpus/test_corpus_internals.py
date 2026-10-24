"""Guards the store contract does not reach.

Assert the effect, not the presence of a constant, and pair each negative with a control.
"""

from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


TABLE_YAML = (
    "asset_type: table\n"
    "id: beer_factory.customers\n"
    "schema: beer_factory\n"
    "physical_name: customers\n"
    "summary: customers - one row per registered buyer\n"
)

JOIN_YAML = (
    "asset_type: join\n"
    "id: join_beer_factory_kunden_geoposition_1a2b3c4d\n"
    "left_table: kunden\n"
    "right_table: geoposition\n"
    "on: kunden.betrieb_id = geoposition.standort_id\n"
    "summary: kunden joins geoposition on betrieb_id\n"
)


# ── path components: the trailing-newline bug ────────────────────────────────


@pytest.mark.parametrize("bad", ["beer_factory\n", "../evil", "beer/factory", "..", ""])
def test_a_schema_name_that_could_name_another_directory_is_refused(bad: str) -> None:
    """A trailing newline must fail. ``$`` matches before it, so the pattern is anchored at both ends."""
    from governed_bi.corpus.identity import UnsafeName, validate_path_component

    with pytest.raises(UnsafeName):
        validate_path_component(bad, what="schema")


def test_a_plain_identifier_is_accepted() -> None:
    from governed_bi.corpus.identity import validate_path_component

    assert validate_path_component("beer_factory", what="schema") == "beer_factory"


@pytest.mark.parametrize(
    "hostile", ["cust/omers", "customers\n", "..", "../../etc/passwd", "cust omers"]
)
def test_a_path_unsafe_physical_name_is_carried_but_cannot_name_a_path(hostile: str) -> None:
    """The engine name is kept. The slug is what must be a safe path component. See ADR 0008."""
    from governed_bi.corpus.identity import (
        slug,
        table_id,
        validate_asset_id,
        validate_path_component,
    )

    component = slug(hostile)
    assert validate_path_component(component, what="slug") == component
    assert ".." not in component and "/" not in component and "\\" not in component
    assert validate_asset_id(table_id("beer_factory", hostile))
    # ``a b`` and ``a_b`` are different tables. A slug that collapsed them would name one and query the other.
    assert slug("a b") != slug("a_b")


def test_an_empty_physical_name_is_still_a_problem() -> None:
    from governed_bi.corpus.identity import UnsafeName, slug
    from governed_bi.corpus.schema import TableAsset
    from governed_bi.corpus.validate import problems_with

    with pytest.raises(UnsafeName):
        slug("")
    asset = TableAsset(
        id="beer_factory.customers",
        schema="beer_factory",
        physical_name="",
        summary="customers - one row per registered buyer",
    )
    assert any("physical_name" in r for r in problems_with(asset)), problems_with(asset)


def test_physical_name_accepts_a_bare_identifier() -> None:
    from governed_bi.corpus.schema import TableAsset
    from governed_bi.corpus.validate import problems_with

    asset = TableAsset(
        id="beer_factory.customers",
        schema="beer_factory",
        physical_name="customers",
        summary="customers - one row per registered buyer",
    )
    assert not any("physical_name" in r for r in problems_with(asset))


# ── the YAML 1.1 `on:` trap ──────────────────────────────────────────────────


def test_the_library_really_does_resolve_on_as_a_boolean() -> None:
    """Control against PyYAML itself, so the loader test cannot pass on an unfixed parser."""
    assert yaml.safe_load("on: kunden.a = geo.b") == {True: "kunden.a = geo.b"}


def test_a_join_keeps_its_on_clause_through_the_loader(tmp_path) -> None:
    """YAML 1.1 would turn the key ``on`` into ``True`` and drop the clause."""
    from governed_bi.corpus import store
    from governed_bi.corpus.parse import from_mapping

    join = from_mapping(yaml.safe_load(JOIN_YAML.replace("on:", "'on':")))
    store.write(tmp_path, "beer_factory", [*_model(), join])
    assets, problems = store.load(tmp_path)
    assert not problems
    assert next(a for a in assets if a.id == join.id).on == "kunden.betrieb_id = geoposition.standort_id"


# ── the manifest is the contamination fix ────────────────────────────────────


def _model(schema: str = "beer_factory"):
    from governed_bi.corpus.parse import from_mapping
    from governed_bi.corpus.schema import SchemaAsset

    return [
        SchemaAsset(id=schema, name=schema, summary=f"{schema}: a schema of customers and orders"),
        from_mapping(yaml.safe_load(TABLE_YAML.replace("beer_factory", schema))),
    ]


def test_a_subtree_outside_the_manifest_is_not_loaded(tmp_path) -> None:
    from governed_bi.corpus import store

    for schema in ("beer_factory", "leftover"):
        store.write(tmp_path, schema, _model(schema))

    everything, _ = store.load(tmp_path)
    assert len(everything) == 4, "with no manifest the tree is the manifest"

    listed, problems = store.load(tmp_path, schemas=["beer_factory"])
    assert sorted(a.id for a in listed) == ["beer_factory", "beer_factory.customers"]
    assert not problems


def test_a_manifest_schema_with_no_model_is_reported(tmp_path) -> None:
    from governed_bi.corpus import store

    store.write(tmp_path, "beer_factory", _model())
    assets, problems = store.load(tmp_path, schemas=["beer_factory", "absent_schema"])
    assert len(assets) == 2
    assert len(problems) == 1 and "absent_schema" in str(problems[0])


def test_a_near_miss_suffix_is_reported_rather_than_skipped(tmp_path) -> None:
    from governed_bi.corpus import store

    path = store.write(tmp_path, "beer_factory", _model())
    path.rename(path.with_suffix(".yml"))
    assets, problems = store.load(tmp_path)
    assert not assets
    assert len(problems) == 1 and "beer_factory.yml" in str(problems[0])


# ── validation: each rule fires, and a valid asset reports nothing ────────────


def _table(**overrides):
    from governed_bi.corpus.schema import TableAsset

    fields = {
        "id": "beer_factory.customers",
        "schema": "beer_factory",
        "physical_name": "customers",
        "summary": "customers - one row per registered buyer",
    }
    return TableAsset(**{**fields, **overrides})


def test_a_valid_asset_reports_nothing() -> None:
    """The control every negative below needs."""
    from governed_bi.corpus.validate import problems_with

    assert problems_with(_table()) == []


@pytest.mark.parametrize(
    "overrides, expected",
    [
        ({"summary": ""}, "empty"),
        ({"summary": "customers " + "x" * 250}, "over the 250 cap"),
        ({"summary": "one row per registered buyer"}, "does not contain physical_name"),
        ({"schema": ""}, "schema is missing"),
    ],
    ids=["empty", "over_cap", "identifier_absent", "untagged"],
)
def test_each_rule_fires(overrides: dict, expected: str) -> None:
    """One asset, one rule broken at a time, so a validator that failed everything
    for one reason could not satisfy the whole set."""
    from governed_bi.corpus.validate import problems_with

    reasons = problems_with(_table(**overrides))
    assert any(expected in reason for reason in reasons), reasons


def test_a_body_is_never_a_reason() -> None:
    """Body length is not a rule. The seed emits assets with no body."""
    from governed_bi.corpus.validate import problems_with

    assert problems_with(_table(body=None)) == []
    assert problems_with(_table(body="line. " * 20_000)) == []


def test_every_reason_names_the_asset() -> None:
    from governed_bi.corpus.validate import problems_with

    reasons = problems_with(_table(summary=""))
    assert reasons and all("beer_factory.customers" in reason for reason in reasons)


def test_the_tag_rule_predicate_guard_fires_on_a_rule_with_no_predicate(monkeypatch) -> None:
    """A missing predicate would be a check that never runs."""
    from governed_bi.corpus import validate
    from governed_bi.registry.assets import TagRule

    monkeypatch.delitem(validate.TAG_RULE_FIELDS, TagRule.own_schema)  # type: ignore[arg-type]
    with pytest.raises(AssertionError, match="own_schema"):
        validate._assert_every_tag_rule_has_a_predicate()


def test_the_common_field_guard_fires_on_a_class_that_forgets_governance(monkeypatch) -> None:
    """Common fields are copied per class because a base class would order them wrongly."""
    from governed_bi.corpus import schema as module
    from governed_bi.registry.assets import AssetType

    @dataclasses.dataclass(frozen=True)
    class Forgetful:
        asset_type = AssetType.term
        id: str = ""
        summary: str = ""
        body: str | None = None
        audit: object = None
        name: str = ""

    monkeypatch.setitem(module.ASSET_CLASSES, AssetType.term, Forgetful)  # type: ignore[arg-type]
    with pytest.raises(AssertionError, match="governance"):
        module._assert_every_asset_carries_the_common_fields()


# ── the hash honours the manifest, and absence is out of band ─────────────────


def test_the_hash_ignores_a_schema_outside_the_manifest(tmp_path) -> None:
    """A leftover schema must not move a digest limited to the schemas that were served."""
    from governed_bi.corpus import store
    from governed_bi.corpus.hash import corpus_content_hash

    for schema in ("beer_factory", "leftover"):
        store.write(tmp_path, schema, _model(schema))

    before = corpus_content_hash(tmp_path, schemas=["beer_factory"])
    (tmp_path / "leftover" / "leftover.yaml").write_text("version: 0.2.0.dev0\n", encoding="utf-8")
    assert corpus_content_hash(tmp_path, schemas=["beer_factory"]) == before
