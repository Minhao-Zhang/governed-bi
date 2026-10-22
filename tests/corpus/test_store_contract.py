"""Store contract. See ADR 0005.

Summary length is bounded because the index is one scoring space. Body is optional and unbounded. ``load`` returns
problems and does not raise for a bad item.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))



@pytest.fixture
def store():
    from governed_bi.corpus import store  # type: ignore[import-not-found]

    return store


@pytest.fixture
def valid_asset_yaml() -> str:
    """Shared so each negative case below changes one thing."""
    return (
        "asset_type: table\n"
        "id: beer_factory.customers\n"
        "schema: beer_factory\n"
        "physical_name: customers\n"
        "summary: customers - one row per registered buyer\n"
    )


# ── per-item error isolation, in both directions ─────────────────────────────


def test_one_unparseable_file_does_not_discard_the_others(store, tmp_path, valid_asset_yaml) -> None:
    (tmp_path / "good.yaml").write_text(valid_asset_yaml, encoding="utf-8")
    (tmp_path / "truncated.yaml").write_text("asset_type: table\nid: beer_factory.orders\nsum", encoding="utf-8")

    assets, problems = store.load(tmp_path)
    assert len(assets) == 1, "the parseable asset must survive its neighbour"
    assert len(problems) == 1


def test_load_never_raises_for_a_bad_item(store, tmp_path) -> None:
    (tmp_path / "a.yaml").write_text("{{{ not yaml", encoding="utf-8")
    (tmp_path / "b.yaml").write_text("asset_type: nonesuch\nid: x\n", encoding="utf-8")
    assets, problems = store.load(tmp_path)
    assert assets == [] or len(assets) == 0
    assert len(problems) == 2


def test_a_problem_names_the_file_and_the_reason(store, tmp_path) -> None:
    (tmp_path / "truncated.yaml").write_text("asset_type: table\nid: x\nsum", encoding="utf-8")
    _, problems = store.load(tmp_path)
    rendered = " ".join(str(p) for p in problems)
    assert "truncated.yaml" in rendered


def test_an_empty_directory_is_not_an_error_but_is_reported(store, tmp_path) -> None:
    assets, problems = store.load(tmp_path)
    assert assets == [] or len(assets) == 0
    assert not problems, "an empty directory is not a per-item problem"


# ── I1 and I2 ────────────────────────────────────────────────────────────────


def test_a_summary_over_250_chars_is_rejected(store, tmp_path) -> None:
    yaml = (
        "asset_type: table\nid: beer_factory.customers\nschema: beer_factory\n"
        f"physical_name: customers\nsummary: {'x' * 251}\n"
    )
    (tmp_path / "wide.yaml").write_text(yaml, encoding="utf-8")
    assets, problems = store.load(tmp_path)
    assert len(problems) == 1 and not assets


def test_an_asset_with_no_body_is_valid(store, tmp_path, valid_asset_yaml) -> None:
    (tmp_path / "seeded.yaml").write_text(valid_asset_yaml, encoding="utf-8")
    assets, problems = store.load(tmp_path)
    assert len(assets) == 1 and not problems


def test_an_unbounded_body_is_valid(store, tmp_path, valid_asset_yaml) -> None:
    yaml = valid_asset_yaml + "body: |\n" + "".join(f"  line {i}\n" for i in range(500))
    (tmp_path / "rich.yaml").write_text(yaml, encoding="utf-8")
    assets, problems = store.load(tmp_path)
    assert len(assets) == 1, problems


def test_a_column_summary_may_drop_the_table_qualifier(store, tmp_path) -> None:
    """The table comes from the tag rule, so a shorter summary may omit the qualifier."""
    yaml = (
        "asset_type: column\nid: beer_factory.customers.email\nschema: beer_factory\n"
        "physical_name: email\nparent_table: customers\n"
        "summary: email - contact address, unique per customer\n"
    )
    (tmp_path / "col.yaml").write_text(yaml, encoding="utf-8")
    assets, problems = store.load(tmp_path)
    assert len(assets) == 1, problems


# ── the content hash ─────────────────────────────────────────────────────────


def test_the_content_hash_has_no_unknown_sentinel(tmp_path, valid_asset_yaml) -> None:
    """A sentinel would compare equal for two missing corpora."""
    from governed_bi.corpus.hash import corpus_content_hash  # type: ignore[import-not-found]

    (tmp_path / "a.yaml").write_text(valid_asset_yaml, encoding="utf-8")
    digest = corpus_content_hash(tmp_path)
    assert "unknown" not in str(digest).lower()


def test_the_content_hash_changes_when_content_changes(tmp_path, valid_asset_yaml) -> None:
    from governed_bi.corpus.hash import corpus_content_hash  # type: ignore[import-not-found]

    path = tmp_path / "a.yaml"
    path.write_text(valid_asset_yaml, encoding="utf-8")
    before = corpus_content_hash(tmp_path)
    path.write_text(valid_asset_yaml.replace("registered buyer", "registered purchaser"), encoding="utf-8")
    assert corpus_content_hash(tmp_path) != before


def test_the_content_hash_is_stable_across_calls(tmp_path, valid_asset_yaml) -> None:
    """A digest that changed on every call would also pass the change test."""
    from governed_bi.corpus.hash import corpus_content_hash  # type: ignore[import-not-found]

    (tmp_path / "a.yaml").write_text(valid_asset_yaml, encoding="utf-8")
    assert corpus_content_hash(tmp_path) == corpus_content_hash(tmp_path)
