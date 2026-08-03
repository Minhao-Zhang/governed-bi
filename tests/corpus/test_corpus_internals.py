"""Parcel D's internals: the guards the acceptance contract does not reach.

``tests/corpus/test_store_contract.py`` is the criterion and is not edited. This
file covers what it leaves untested, under the authoring rules in
``docs/lessons-from-v1.md`` §7:

* **Assert on the effect, not on the presence of a constant.** The loader's ``on:``
  handling is tested by loading a join and reading back its ON clause, not by looking
  for the resolver tweak in the source.
* **Never assert a module against its own constant.** Nothing here compares a value
  to the table it came from.
* **Test the negative case.** Every import-time guard is driven with a broken
  register so it actually fires; a guard that only leaves a trace when it fires
  cannot afterwards be told from one that was never wired up. Each negative has its
  positive control beside it, or it would also pass for a guard that raises always.
"""

from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from contracts import needs  # noqa: E402

pytestmark = needs("D")

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
    """``beer_factory\\n`` is the case that matters and the reason the pattern is
    ``\\A...\\Z``: Python's ``$`` also matches just before a trailing newline, so this
    string passes a ``^[A-Za-z0-9_]+$`` validator that names a directory. v1's write
    path derived the directory from ``asset.schema`` while the only validator in the
    area guarded the asset id.
    """
    from governed_bi.corpus.identity import UnsafeName, validate_path_component

    with pytest.raises(UnsafeName):
        validate_path_component(bad, what="schema")


def test_a_plain_identifier_is_accepted() -> None:
    from governed_bi.corpus.identity import validate_path_component

    assert validate_path_component("beer_factory", what="schema") == "beer_factory"


@pytest.mark.parametrize("bad", ["cust/omers", "customers\n", "..", "cust omers", ""])
def test_physical_name_rejects_path_unsafe_spellings(bad: str) -> None:
    """Same character class as path components (#37 leftover). Refuse, never edit."""
    from governed_bi.corpus.schema import TableAsset
    from governed_bi.corpus.validate import problems_with

    asset = TableAsset(
        id="beer_factory.customers",
        schema="beer_factory",
        physical_name=bad,
        summary=f"{bad} - one row per buyer" if bad.strip() else "customers - one row",
    )
    reasons = problems_with(asset)
    assert any("physical_name" in r for r in reasons), reasons


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
    """The control, asserted against PyYAML rather than assumed.

    Without it, the test below would pass just as well against a loader that fixed
    nothing, and this whole trap would be untested. ``JoinAsset.on`` is the field at
    stake and ADR 0005 §1.2 makes the ON clause part of a join's identity.
    """
    assert yaml.safe_load("on: kunden.a = geo.b") == {True: "kunden.a = geo.b"}


def test_a_join_keeps_its_on_clause_through_the_loader(tmp_path) -> None:
    """Under YAML 1.1 the key arrives as ``True``, the field looks absent, and the ON
    clause vanishes with no error anywhere."""
    from governed_bi.corpus import store

    (tmp_path / "j.yaml").write_text(JOIN_YAML, encoding="utf-8")
    assets, problems = store.load(tmp_path)
    assert not problems
    assert assets[0].on == "kunden.betrieb_id = geoposition.standort_id"


# ── inline columns: identity is derived, not read ─────────────────────────────


def test_inline_columns_become_their_own_assets_with_derived_ids(tmp_path) -> None:
    """v1 concatenated a table and all its columns into one index document, so
    columns were never ranked at all. One entry per asset is what fixes that, and it
    requires the loader to expand the inline form."""
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
    assert by_id["beer_factory.customers.email"].parent_table == "customers"


def test_an_inline_column_that_states_a_conflicting_parent_is_a_problem(tmp_path) -> None:
    """Not an override. A column's identity comes from its position, and a file
    carrying it twice has two answers to one fact -- which is how the retrieval
    index, ``resolve``'s closure and the per-type budget end up keyed on different
    strings for one column with nothing raising."""
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
    """v1's shared corpus root was a cross-run contamination channel: a schema dropped
    from one attempt left its YAML behind and competed as a router candidate for every
    other schema's questions, silently changing the routing problem's difficulty
    between two runs of the same set."""
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
    """The other half. Zero assets for a named schema must not read as "that schema is
    small"."""
    from governed_bi.corpus import store

    (tmp_path / "beer_factory").mkdir()
    (tmp_path / "beer_factory" / "t.yaml").write_text(TABLE_YAML, encoding="utf-8")

    assets, problems = store.load(tmp_path, schemas=["beer_factory", "absent_schema"])
    assert len(assets) == 1
    assert len(problems) == 1 and "absent_schema" in str(problems[0])


def test_a_near_miss_suffix_is_reported_rather_than_skipped(tmp_path) -> None:
    """A file the loader ignores in silence is an asset the corpus lost."""
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
        ({"confidence": 1.5}, "not a number in [0, 1]"),
    ],
    ids=["empty", "over_cap", "identifier_absent", "untagged", "confidence"],
)
def test_each_rule_fires(overrides: dict, expected: str) -> None:
    """One asset, one rule broken at a time, so a validator that failed everything
    for one reason could not satisfy the whole set."""
    from governed_bi.corpus.validate import problems_with

    reasons = problems_with(_table(**overrides))
    assert any(expected in reason for reason in reasons), reasons


def test_a_body_is_never_a_reason() -> None:
    """I2, as an explicit non-rule. The seed produces assets with no body at all, and
    requiring one would falsify ADR 0005's "measurable with no model" claim -- which
    is the reason the seed exists. The other direction is equally load-bearing:
    ``body`` is unbounded, so length must not be a reason either."""
    from governed_bi.corpus.validate import problems_with

    assert problems_with(_table(body=None)) == []
    assert problems_with(_table(body="line. " * 20_000)) == []


def test_every_reason_names_the_asset() -> None:
    """A bare reason string still has to be actionable: the seed reports these
    without a file path, and a problem a reader cannot act on is a silent skip with
    extra steps."""
    from governed_bi.corpus.validate import problems_with

    reasons = problems_with(_table(summary=""))
    assert reasons and all("beer_factory.customers" in reason for reason in reasons)


def test_the_tag_rule_predicate_guard_fires_on_a_rule_with_no_predicate(monkeypatch) -> None:
    """A tag rule with no predicate is a check that never runs -- the
    ``budgets.get(cls, 0)`` shape, where the rule exists, something iterates the enum,
    and the missing row is silent."""
    from governed_bi.corpus import validate
    from governed_bi.register.assets import TagRule

    monkeypatch.delitem(validate.TAG_RULE_FIELDS, TagRule.own_schema)  # type: ignore[arg-type]
    with pytest.raises(AssertionError, match="own_schema"):
        validate._assert_every_tag_rule_has_a_predicate()


def test_the_common_field_guard_fires_on_a_class_that_forgets_governance(monkeypatch) -> None:
    """The six common fields are written out eight times because dataclass
    inheritance orders them wrongly. Repetition nothing checks is how two v1 tables
    disagreed for a year."""
    from governed_bi.corpus import schema as module
    from governed_bi.register.assets import AssetType

    @dataclasses.dataclass(frozen=True)
    class Forgetful:
        asset_type = AssetType.term
        id: str = ""
        summary: str = ""
        body: str | None = None
        confidence: float | None = None
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
    """Validation covers *every* writer, not only the loader -- a tool call that built
    an over-length summary must not be able to put it in the index."""
    from governed_bi.corpus import store

    with pytest.raises(ValueError, match="cap"):
        store.write(tmp_path, _table(summary="customers " + "x" * 250))


def test_write_refuses_a_namespace_that_escapes_the_root(tmp_path) -> None:
    from governed_bi.corpus import store
    from governed_bi.corpus.identity import UnsafeName

    with pytest.raises(UnsafeName):
        store.write(tmp_path, _table(), namespace="../evil")


def test_write_refuses_to_guess_a_namespace_for_a_type_that_has_none(tmp_path) -> None:
    """A join's namespace is its left endpoint's, a metric's its base table's -- facts
    held by another asset. ADR 0005 does not say where such a file lives, so this
    refuses rather than inventing a default that then has to be reconciled with the
    tag rule."""
    from governed_bi.corpus import store
    from governed_bi.corpus.identity import SHARED_NAMESPACE
    from governed_bi.corpus.parse import from_mapping

    join = from_mapping(yaml.safe_load(JOIN_YAML.replace("on:", "'on':")))
    with pytest.raises(ValueError, match="namespace"):
        store.write(tmp_path, join)
    assert store.write(tmp_path, join, namespace=SHARED_NAMESPACE).exists()


def test_a_metric_is_not_written_into_a_directory_named_after_the_metric(tmp_path) -> None:
    """``MetricAsset.name`` is a business name. Deriving the directory from "the first
    attribute that looks like a name" would put ``revenue`` in a schema directory
    called ``revenue`` -- a plausible-looking wrong answer."""
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
    """An arm's treatment identity must cover exactly the schemas that arm served.
    A leftover subtree moving the digest would make two runs over the same treatment
    look differently treated, which passes the delivery gate for the wrong reason."""
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
    """v1's ``corpus_content_hash == "unknown"`` compared equal to itself, so two runs
    with no recorded treatment passed comparability. Absence is reported out of band
    precisely because a value can be compared."""
    from governed_bi.corpus.hash import corpus_content_hash

    with pytest.raises(FileNotFoundError):
        corpus_content_hash(tmp_path / "no_such_corpus")
