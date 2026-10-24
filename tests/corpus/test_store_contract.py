"""Store contract. See ADR 0005 and ADR 0022.

Summary length is bounded because the index is one scoring space. Body is optional and unbounded. ``load`` returns
problems and does not raise for a bad item.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from governed_bi.corpus import ossie, store
from governed_bi.corpus.hash import corpus_content_hash
from governed_bi.corpus.schema import Asset, ColumnAsset, SchemaAsset, TableAsset


def _assets(schema: str = "beer_factory", **table: object) -> list[Asset]:
    fields = {
        "id": f"{schema}.customers",
        "schema": schema,
        "physical_name": "customers",
        "summary": "customers - one row per registered buyer",
        **table,
    }
    return [
        SchemaAsset(id=schema, name=schema, summary=f"{schema}: a schema of customers and their orders"),
        TableAsset(**fields),  # type: ignore[arg-type]
    ]


def _write(root: Path, assets: list[Asset], schema: str = "beer_factory") -> Path:
    path = root / schema / f"{schema}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(ossie.model_text(schema, assets), encoding="utf-8")
    return path


# ── per-item error isolation, in both directions ─────────────────────────────


def test_one_unparseable_file_does_not_discard_the_others(tmp_path: Path) -> None:
    _write(tmp_path, _assets())
    (tmp_path / "orders").mkdir()
    (tmp_path / "orders" / "orders.yaml").write_text("version: 0.2.0.dev0\nname: ord", encoding="utf-8")

    assets, problems = store.load(tmp_path)
    assert len(assets) == 2, "the parseable model must survive its neighbour"
    assert len(problems) == 1


def test_load_never_raises_for_a_bad_item(tmp_path: Path) -> None:
    for name, text in (("a", "{{{ not yaml"), ("b", "version: 0.2.0.dev0\nname: b\ndatasets: 3\n")):
        (tmp_path / name).mkdir()
        (tmp_path / name / f"{name}.yaml").write_text(text, encoding="utf-8")
    assets, problems = store.load(tmp_path)
    assert assets == []
    assert len(problems) == 2


def test_a_problem_names_the_file_and_the_reason(tmp_path: Path) -> None:
    (tmp_path / "truncated").mkdir()
    (tmp_path / "truncated" / "truncated.yaml").write_text("version: 0.2.0.dev0\nname: x\nsum", encoding="utf-8")
    _, problems = store.load(tmp_path)
    assert "truncated/truncated.yaml" in " ".join(str(p) for p in problems)


def test_a_file_outside_the_model_layout_is_reported(tmp_path: Path) -> None:
    _write(tmp_path, _assets())
    (tmp_path / "beer_factory" / "extra.yaml").write_text("asset_type: table\n", encoding="utf-8")
    assets, problems = store.load(tmp_path)
    assert len(assets) == 2
    assert len(problems) == 1 and "extra.yaml" in str(problems[0])


def test_an_empty_directory_is_not_an_error_but_is_reported(tmp_path: Path) -> None:
    assets, problems = store.load(tmp_path)
    assert assets == []
    assert not problems, "an empty directory is not a per-item problem"


# ── I1 and I2 ────────────────────────────────────────────────────────────────


def test_a_summary_over_250_chars_is_rejected(tmp_path: Path) -> None:
    path = _write(tmp_path, _assets())
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    document["datasets"][0]["description"] = "x" * 251
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    assets, problems = store.load(tmp_path)
    assert len(problems) == 1 and len(assets) == 1, "the table is the one problem; the schema still loads"


def test_an_asset_with_no_body_is_valid(tmp_path: Path) -> None:
    _write(tmp_path, _assets())
    assets, problems = store.load(tmp_path)
    assert len(assets) == 2 and not problems


def test_an_unbounded_body_is_valid(tmp_path: Path) -> None:
    _write(tmp_path, _assets(body="".join(f"line {i}\n" for i in range(500))))
    assets, problems = store.load(tmp_path)
    assert len(assets) == 2, problems


def test_a_column_summary_may_drop_the_table_qualifier(tmp_path: Path) -> None:
    """The table comes from the tag rule, so a shorter summary may omit the qualifier."""
    column = ColumnAsset(
        id="beer_factory.customers.email",
        schema="beer_factory",
        parent_table="beer_factory.customers",
        physical_name="email",
        summary="email - contact address, unique per customer",
    )
    _write(tmp_path, [*_assets(columns=(column.id,)), column])
    assets, problems = store.load(tmp_path)
    assert len(assets) == 3, problems


# ── write ────────────────────────────────────────────────────────────────────


def test_write_then_load_returns_equal_assets(tmp_path: Path) -> None:
    written = _assets(body="what a customer is")
    store.write(tmp_path, "beer_factory", written)
    loaded, problems = store.load(tmp_path, schemas=["beer_factory"])
    assert not problems
    assert sorted(loaded, key=lambda a: a.id) == sorted(written, key=lambda a: a.id)


def test_write_refuses_an_invalid_asset(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="cap"):
        store.write(tmp_path, "beer_factory", _assets(summary="customers " + "x" * 250))


def test_write_refuses_a_schema_name_that_escapes_the_root(tmp_path: Path) -> None:
    from governed_bi.corpus.identity import UnsafeName

    with pytest.raises(UnsafeName):
        store.write(tmp_path, "../evil", _assets())


# ── the content hash ─────────────────────────────────────────────────────────


def test_the_content_hash_has_no_unknown_sentinel(tmp_path: Path) -> None:
    """A sentinel would compare equal for two missing corpora."""
    _write(tmp_path, _assets())
    assert "unknown" not in corpus_content_hash(tmp_path).lower()


def test_the_content_hash_changes_when_content_changes(tmp_path: Path) -> None:
    path = _write(tmp_path, _assets())
    before = corpus_content_hash(tmp_path)
    path.write_text(path.read_text(encoding="utf-8").replace("registered buyer", "registered purchaser"), "utf-8")
    assert corpus_content_hash(tmp_path) != before


def test_the_content_hash_is_stable_across_calls(tmp_path: Path) -> None:
    """A digest that changed on every call would also pass the change test."""
    _write(tmp_path, _assets())
    assert corpus_content_hash(tmp_path) == corpus_content_hash(tmp_path)
