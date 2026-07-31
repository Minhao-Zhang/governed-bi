"""Corpus.table_by_name: qualified always; bare unique only; ambiguous bare → None."""

from __future__ import annotations

from pathlib import Path

import pytest

from governed_bi.analyst import agent as agent_mod
from governed_bi.analyst import middleware as mw
from governed_bi.analyst import tools as tools_mod
from governed_bi.corpus import Corpus
from governed_bi.corpus.schemas import Column, Governance, LogicalType, TableAsset

# Shape of the 27 ambiguous BIRD bare names: one physical name, many schemas.
_AMBIGUOUS_BARE = "kunden"


def _col(name: str) -> Column:
    return Column(
        physical_name=name,
        physical_type="INTEGER",
        logical_type=LogicalType.integer,
        nullable=True,
        is_unique=False,
    )


def _table(
    schema: str,
    physical: str,
    *,
    tid: str | None = None,
    excluded: bool = False,
) -> TableAsset:
    return TableAsset(
        id=tid or f"tbl_{schema}_{physical}",
        schema=schema,
        physical_name=physical,
        columns=[_col("id")],
        governance=Governance(excluded=excluded),
    )


def _ambiguous_corpus() -> Corpus:
    """Two schemas share the bare name ``kunden`` (pais/kunden shape)."""
    return Corpus(
        assets=[
            _table("beer_factory", _AMBIGUOUS_BARE),
            _table("sales", _AMBIGUOUS_BARE),
            _table("beer_factory", "transaction"),  # unique bare
        ]
    )


def test_qualified_name_resolves_case_insensitive():
    corpus = _ambiguous_corpus()
    a = corpus.table_by_name("Beer_Factory.Kunden")
    b = corpus.table_by_name("sales.kunden")
    assert a is not None and a.schema == "beer_factory"
    assert b is not None and b.schema == "sales"
    assert a.id != b.id


def test_unique_bare_name_resolves():
    corpus = _ambiguous_corpus()
    t = corpus.table_by_name("transaction")
    assert t is not None
    assert t.schema == "beer_factory"
    assert t.physical_name == "transaction"


def test_ambiguous_bare_name_returns_none_not_first_match():
    corpus = _ambiguous_corpus()
    assert corpus.table_by_name(_AMBIGUOUS_BARE) is None
    # First asset in list is beer_factory.kunden — must NOT win.
    assert corpus.assets[0].id == "tbl_beer_factory_kunden"


def test_table_by_name_does_not_apply_exclusion_filter():
    """Semantic #4: Corpus is a raw container — excluded tables still count."""
    corpus = Corpus(
        assets=[
            _table("beer_factory", "kunden", excluded=True),
            _table("sales", "kunden", excluded=False),
        ]
    )
    # Would be unique among Analyst-visible tables, but corpus-wide ambiguous.
    assert corpus.table_by_name("kunden") is None
    assert corpus.table_by_name("beer_factory.kunden") is not None
    assert corpus.table_by_name("beer_factory.kunden").governance.excluded is True


def test_tools_excluded_sibling_makes_bare_ambiguous():
    """Call-site exclusion runs *after* corpus-wide resolve (behavior change).

    Old first-match scan skipped excluded assets while matching, so one live +
    one excluded sibling resolved to the live table. Now ``table_by_name`` sees
    both → ``None``, then the call-site exclusion check never runs.
    """
    corpus = Corpus(
        assets=[
            _table("beer_factory", "kunden", excluded=True),
            _table("sales", "kunden", excluded=False),
        ]
    )
    assert tools_mod._table_by_id(corpus, "kunden") is None
    assert tools_mod._table_by_id(corpus, "sales.kunden") is not None
    assert mw._table_by_id(corpus, "kunden") is None


def test_tools_table_by_id_ambiguous_bare_is_none():
    corpus = _ambiguous_corpus()
    assert tools_mod._table_by_id(corpus, _AMBIGUOUS_BARE) is None
    assert tools_mod._table_by_id(corpus, "beer_factory.kunden") is not None
    assert tools_mod._table_by_id(corpus, "tbl_beer_factory_kunden") is not None


def test_middleware_table_by_id_ambiguous_bare_is_none():
    corpus = _ambiguous_corpus()
    assert mw._table_by_id(corpus, _AMBIGUOUS_BARE) is None
    assert mw._table_by_id(corpus, "sales.kunden") is not None


def test_agent_column_count_ambiguous_bare_is_zero():
    """Third call site: assemble rails ``_column_count_for`` (was inline genexp)."""
    corpus = _ambiguous_corpus()
    assert agent_mod._column_count_for(corpus, _AMBIGUOUS_BARE) == 0
    assert agent_mod._column_count_for(corpus, "beer_factory.kunden") == 1
    assert agent_mod._column_count_for(corpus, "tbl_beer_factory_kunden") == 1
    assert agent_mod._column_count_for(corpus, "transaction") == 1


def test_bird_corpus_has_ambiguous_bares_when_present():
    bird = Path(__file__).resolve().parents[2] / "BIRD-corpus"
    if not bird.is_dir():
        pytest.skip("BIRD-corpus sibling not present")
    from governed_bi.corpus import load_corpus

    corpus = load_corpus(bird)
    bare_counts: dict[str, int] = {}
    for a in corpus.tables():
        bare_counts[a.physical_name.lower()] = bare_counts.get(a.physical_name.lower(), 0) + 1
    ambiguous = sorted(n for n, c in bare_counts.items() if c > 1)
    assert len(ambiguous) >= 1, "expected at least one ambiguous bare name in BIRD-corpus"
    for name in ambiguous[:5]:
        assert corpus.table_by_name(name) is None, name
