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

    (tmp_path / "j.yaml").write_text(JOIN_YAML, encoding="utf-8")
    assets, problems = store.load(tmp_path)
    assert not problems
    assert assets[0].on == "kunden.betrieb_id = geoposition.standort_id"


# ── inline columns: identity is derived, not read ─────────────────────────────


def test_inline_columns_become_their_own_assets_with_derived_ids(tmp_path) -> None:
    from governed_bi.corpus import store

    (tmp_path / "t.yaml").write_text(
        TABLE_YAML
        + "columns:\n"
        + "  - physical_name: email\n    summary: email - contact address\n"
        + "  - physical_name: id\n    summary: id - surrogate key\n",
        encoding="utf-8",
    )
    assets, problems = store.load(tmp_path)
    assert not problems, problems
    by_id = {a.id: a for a in assets}
    assert set(by_id) == {
        "beer_factory.customers",
        "beer_factory.customers.email",
        "beer_factory.customers.id",
    }
    table = by_id["beer_factory.customers"]
    assert set(table.columns) == {"beer_factory.customers.email", "beer_factory.customers.id"}
    # The table id, not the physical name: a reference names one asset (ADR 0008).
    assert by_id["beer_factory.customers.email"].parent_table == "beer_factory.customers"


def test_an_inline_column_that_states_a_conflicting_parent_is_a_problem(tmp_path) -> None:
    """A stated parent is not an override. Identity comes from where the column is stored."""
    from governed_bi.corpus import store

    (tmp_path / "t.yaml").write_text(
        TABLE_YAML
        + "columns:\n"
        + "  - physical_name: email\n    parent_table: somewhere_else\n"
        + "    summary: email - contact address\n",
        encoding="utf-8",
    )
    assets, problems = store.load(tmp_path)
    assert not assets
    assert len(problems) == 1 and "parent_table" in str(problems[0])


# ── the manifest is the contamination fix ────────────────────────────────────


def test_a_subtree_outside_the_manifest_is_not_loaded(tmp_path) -> None:
    from governed_bi.corpus import store

    for schema in ("beer_factory", "leftover"):
        (tmp_path / schema).mkdir()
        (tmp_path / schema / "t.yaml").write_text(
            TABLE_YAML.replace("beer_factory", schema), encoding="utf-8"
        )

    everything, _ = store.load(tmp_path)
    assert len(everything) == 2, "with no manifest the tree is the manifest"

    listed, problems = store.load(tmp_path, schemas=["beer_factory"])
    assert [a.id for a in listed] == ["beer_factory.customers"]
    assert not problems


def test_a_manifest_schema_with_no_directory_is_reported(tmp_path) -> None:
    from governed_bi.corpus import store

    (tmp_path / "beer_factory").mkdir()
    (tmp_path / "beer_factory" / "t.yaml").write_text(TABLE_YAML, encoding="utf-8")

    assets, problems = store.load(tmp_path, schemas=["beer_factory", "absent_schema"])
    assert len(assets) == 1
    assert len(problems) == 1 and "absent_schema" in str(problems[0])


def test_a_near_miss_suffix_is_reported_rather_than_skipped(tmp_path) -> None:
    from governed_bi.corpus import store

    (tmp_path / "t.yml").write_text(TABLE_YAML, encoding="utf-8")
    assets, problems = store.load(tmp_path)
    assert not assets
    assert len(problems) == 1 and "t.yml" in str(problems[0])


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


# ── write: the security half is not error-isolated ───────────────────────────


def test_write_then_load_returns_an_equal_asset(tmp_path) -> None:
    from governed_bi.corpus import store

    asset = _table(body="what a customer is", columns=("beer_factory.customers.email",))
    store.write(tmp_path, asset)
    loaded, problems = store.load(tmp_path, schemas=["beer_factory"])
    assert not problems
    assert loaded == [asset]


def test_write_refuses_an_invalid_asset(tmp_path) -> None:
    from governed_bi.corpus import store

    with pytest.raises(ValueError, match="cap"):
        store.write(tmp_path, _table(summary="customers " + "x" * 250))


def test_write_refuses_a_namespace_that_escapes_the_root(tmp_path) -> None:
    from governed_bi.corpus import store
    from governed_bi.corpus.identity import UnsafeName

    with pytest.raises(UnsafeName):
        store.write(tmp_path, _table(), namespace="../evil")


def test_write_refuses_to_guess_a_namespace_for_a_type_that_has_none(tmp_path) -> None:
    """No default directory: the namespace is a fact on another asset."""
    from governed_bi.corpus import store
    from governed_bi.corpus.identity import SHARED_NAMESPACE
    from governed_bi.corpus.parse import from_mapping

    join = from_mapping(yaml.safe_load(JOIN_YAML.replace("on:", "'on':")))
    with pytest.raises(ValueError, match="namespace"):
        store.write(tmp_path, join)
    assert store.write(tmp_path, join, namespace=SHARED_NAMESPACE).exists()


def test_a_metric_is_not_written_into_a_directory_named_after_the_metric(tmp_path) -> None:
    """``name`` is a business name, so it is not the directory."""
    from governed_bi.corpus import store
    from governed_bi.corpus.schema import MetricAsset

    metric = MetricAsset(
        id="metric_beer_factory_revenue",
        name="revenue",
        base_table="beer_factory.orders",
        expression="SUM(amount)",
        summary="revenue: the total value of orders in a period",
    )
    with pytest.raises(ValueError, match="namespace"):
        store.write(tmp_path, metric)


# ── the hash honours the manifest, and absence is out of band ─────────────────


def test_the_hash_ignores_a_schema_outside_the_manifest(tmp_path) -> None:
    """A leftover schema must not move a digest limited to the schemas that were served."""
    from governed_bi.corpus.hash import corpus_content_hash

    for schema in ("beer_factory", "leftover"):
        (tmp_path / schema).mkdir()
        (tmp_path / schema / "t.yaml").write_text(
            TABLE_YAML.replace("beer_factory", schema), encoding="utf-8"
        )

    before = corpus_content_hash(tmp_path, schemas=["beer_factory"])
    (tmp_path / "leftover" / "t.yaml").write_text("asset_type: table\n", encoding="utf-8")
    assert corpus_content_hash(tmp_path, schemas=["beer_factory"]) == before
    assert corpus_content_hash(tmp_path) != before, "the unrestricted digest must still move"


def test_a_missing_corpus_has_no_digest_rather_than_a_sentinel_one(tmp_path) -> None:
    from governed_bi.corpus.hash import corpus_content_hash

    with pytest.raises(FileNotFoundError):
        corpus_content_hash(tmp_path / "no_such_corpus")


def test_the_hash_ignores_the_version_controls_own_bookkeeping(tmp_path) -> None:
    """``.git`` must not move the digest, or a commit would change the corpus identity."""
    from governed_bi.corpus.hash import corpus_content_hash

    (tmp_path / "beer_factory").mkdir()
    (tmp_path / "beer_factory" / "t.yaml").write_text(TABLE_YAML, encoding="utf-8")
    for noise in (".git", "__pycache__"):
        (tmp_path / noise).mkdir()
        (tmp_path / noise / "HEAD").write_text("ref: refs/heads/v2\n", encoding="utf-8")

    before = corpus_content_hash(tmp_path)
    (tmp_path / ".git" / "HEAD").write_text("ref: refs/heads/other\n", encoding="utf-8")
    (tmp_path / ".git" / "index").write_bytes(b"\x00binary")
    assert corpus_content_hash(tmp_path) == before, "committing must not restate the corpus"

    # Corpus bytes still move the digest, so the exclusion is not the whole tree.
    (tmp_path / "beer_factory" / "t.yaml").write_text(
        TABLE_YAML + "\nnote: edited\n", encoding="utf-8"
    )
    assert corpus_content_hash(tmp_path) != before


def test_a_corpus_repositorys_readme_is_still_corpus_content(tmp_path) -> None:
    """Prose at the root still counts. Only tool directories are dropped."""
    from governed_bi.corpus.hash import corpus_content_hash

    (tmp_path / "beer_factory").mkdir()
    (tmp_path / "beer_factory" / "t.yaml").write_text(TABLE_YAML, encoding="utf-8")
    before = corpus_content_hash(tmp_path)
    (tmp_path / "README.md").write_text("# BIRD-corpus\n", encoding="utf-8")
    assert corpus_content_hash(tmp_path) != before
    assert corpus_content_hash(tmp_path, schemas=["beer_factory"]) == corpus_content_hash(
        tmp_path, schemas=["beer_factory"]
    )
