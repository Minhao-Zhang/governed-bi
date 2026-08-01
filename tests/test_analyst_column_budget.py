"""Per-table column budget for the analyst prompt (``analyst/context.py``).

The load-bearing test in here is the first one: with the shipped default the
rendered block must be **byte-identical** to the uncapped rendering. Everything
recorded before the knob existed was measured without it, so a default that
changed a single character would confound every comparison the knob was added to
make.

The rest pin the parts a green suite would otherwise not notice: that a SUSPECT
column survives a budget it ranks nowhere near, that a column another block of
the same prompt already names survives too, and that the fill is relevance and
not the head of the declaration order.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from governed_bi.analyst.context import (
    DEFAULT_MAX_TABLE_COLUMNS,
    assemble_context,
)
from governed_bi.analyst.governance import _licensed_table_ids
from governed_bi.corpus import Corpus, load_corpus
from governed_bi.corpus.schemas import (
    Column,
    JoinAsset,
    LogicalType,
    Reliability,
    ReliabilityStatus,
    TableAsset,
)
from governed_bi.graph import build_graph, plan_joins
from governed_bi.retrieval import RetrievalResult, retrieve

CORPUS_ROOT = Path(__file__).resolve().parents[1] / "corpus"

WIDE = "tbl_s_wide"


# --------------------------------------------------------------------------- #
# Fixtures: a synthetic wide table with a decoy, plus the committed corpus
# --------------------------------------------------------------------------- #


def _col(name: str, *, suspect: bool = False, role=None, description: str | None = None):
    return Column(
        physical_name=name,
        physical_type="INTEGER",
        logical_type=LogicalType.integer,
        nullable=True,
        is_unique=False,
        role=role,
        description=description,
        reliability=(
            Reliability(status=ReliabilityStatus.suspect, note="obfuscation decoy")
            if suspect
            else Reliability()
        ),
    )


def _wide_corpus() -> Corpus:
    """One 30-column table. ``zz_target`` is LAST in declaration order and is the
    only column whose name matches the test question, so relevance and position
    disagree — which is the only way to tell the two selection rules apart."""
    cols = [_col(f"filler_{i:02d}") for i in range(28)]
    cols.insert(0, _col("wide_id", role=None))
    cols.append(_col("decoy_col", suspect=True))
    cols.append(_col("zz_target", description="the widget count"))
    return Corpus(
        assets=[
            TableAsset(id=WIDE, schema="s", physical_name="wide", columns=cols),
        ]
    )


def _ctx(corpus: Corpus, question: str, **kw):
    retrieval = RetrievalResult(question=question, table_ids=[WIDE])
    return assemble_context(corpus, retrieval, licensed_table_ids=frozenset({WIDE}), **kw)


@pytest.fixture
def beer():
    return load_corpus(CORPUS_ROOT, schema="beer_factory").for_analyst()


def _beer_ctx(corpus, question, **kw):
    graph = build_graph(corpus)
    retrieval = retrieve(corpus, question)
    try:
        join_ids = plan_joins(graph, set(retrieval.table_ids)).join_ids
    except ValueError:
        join_ids = []
    licensed = _licensed_table_ids(corpus, graph, retrieval, join_ids)
    return assemble_context(corpus, retrieval, licensed_table_ids=licensed, **kw)


# --------------------------------------------------------------------------- #
# 1. The default is a no-op (the check the experiment depends on)
# --------------------------------------------------------------------------- #


def test_default_is_no_cap():
    """A non-zero default would silently rewrite every prompt in every recorded run."""
    assert DEFAULT_MAX_TABLE_COLUMNS == 0


def test_default_renders_every_column_and_no_marker(beer):
    q = "How many transactions did each customer make?"
    ctx = _beer_ctx(beer, q)
    for tv in ctx.tables:
        table = beer.by_id(tv.id)
        assert len(tv.columns) == len(table.columns)
        assert tv.n_columns_omitted == 0
    assert ctx.n_columns_omitted == 0
    assert "columns omitted" not in ctx.render()


@pytest.mark.parametrize("off", [None, 0])
def test_off_values_render_identically_to_the_implicit_default(beer, off):
    q = "How many transactions did each customer make?"
    baseline = _beer_ctx(beer, q).render()
    assert _beer_ctx(beer, q, max_table_columns=off).render() == baseline
    assert _beer_ctx(beer, q, compact_caveats=False).render() == baseline


def test_wide_table_uncapped_by_default():
    corpus = _wide_corpus()
    ctx = _ctx(corpus, "count the widgets")
    assert len(ctx.tables[0].columns) == 31
    assert ctx.tables[0].n_columns_omitted == 0


# --------------------------------------------------------------------------- #
# 2. The budget binds, and reports what it withheld
# --------------------------------------------------------------------------- #


def test_budget_caps_the_table_and_records_the_omission():
    corpus = _wide_corpus()
    ctx = _ctx(corpus, "count the widgets", max_table_columns=10)
    tv = ctx.tables[0]
    assert len(tv.columns) == 10
    assert tv.n_columns_omitted == 21
    assert ctx.n_columns_omitted == 21


def test_truncation_marker_names_the_counts_and_the_recovery_path():
    corpus = _wide_corpus()
    block = _ctx(corpus, "count the widgets", max_table_columns=10).render()
    assert "… (21 of 31 columns omitted as low-relevance" in block
    # The budget must be recoverable, not lossy: inspect_schema returns the table whole.
    assert f"inspect_schema('{WIDE}')" in block


def test_budget_wider_than_the_table_is_a_no_op():
    corpus = _wide_corpus()
    assert _ctx(corpus, "q", max_table_columns=999).render() == _ctx(corpus, "q").render()


def test_rendered_columns_keep_declaration_order():
    corpus = _wide_corpus()
    ctx = _ctx(corpus, "count the widgets", max_table_columns=10)
    names = [c.physical_name for c in ctx.tables[0].columns]
    declared = [c.physical_name for c in corpus.by_id(WIDE).columns]
    assert names == [n for n in declared if n in set(names)]


# --------------------------------------------------------------------------- #
# 3. Governance: a warning is never budgeted away
# --------------------------------------------------------------------------- #


def test_suspect_column_survives_the_tightest_budget():
    """``decoy_col`` shares no vocabulary with the question and sits second-to-last
    in declaration order — it loses on every discretionary rule there is. Dropping
    it would remove the DO-NOT-USE line while leaving the column reachable, i.e.
    turn the governance signal into an invitation."""
    corpus = _wide_corpus()
    ctx = _ctx(corpus, "count the widgets", max_table_columns=1)
    names = [c.physical_name for c in ctx.tables[0].columns]
    assert "decoy_col" in names
    block = ctx.render()
    assert "[SUSPECT - DO NOT USE: obfuscation decoy]" in block


def test_mandatory_tier_may_exceed_the_budget_rather_than_drop_a_warning():
    cols = [_col(f"d{i}", suspect=True) for i in range(5)] + [_col(f"p{i}") for i in range(5)]
    corpus = Corpus(assets=[TableAsset(id=WIDE, schema="s", physical_name="wide", columns=cols)])
    ctx = _ctx(corpus, "unrelated question", max_table_columns=2)
    names = [c.physical_name for c in ctx.tables[0].columns]
    assert sorted(n for n in names if n.startswith("d")) == ["d0", "d1", "d2", "d3", "d4"]
    assert len(names) == 5  # budget of 2 overrun by the mandatory tier, not honoured


def test_caveat_list_is_budget_invariant(beer):
    q = "Which customers are in a given zip code?"
    assert _beer_ctx(beer, q, max_table_columns=2).caveats == _beer_ctx(beer, q).caveats


# --------------------------------------------------------------------------- #
# 4. Coherence: never hide an identifier this same prompt names
# --------------------------------------------------------------------------- #


def test_column_named_by_a_rendered_join_survives():
    corpus = _wide_corpus()
    other = TableAsset(
        id="tbl_s_other", schema="s", physical_name="other", columns=[_col("wide_ref")]
    )
    corpus.assets.append(other)
    corpus.assets.append(
        JoinAsset(
            id="join_s_wide_other",
            left_table=WIDE,
            right_table="tbl_s_other",
            on="wide.filler_27 = other.wide_ref",
        )
    )
    retrieval = RetrievalResult(question="unrelated question", table_ids=[WIDE, "tbl_s_other"])
    ctx = assemble_context(
        corpus,
        retrieval,
        licensed_table_ids=frozenset({WIDE, "tbl_s_other"}),
        max_table_columns=2,
    )
    wide = next(t for t in ctx.tables if t.id == WIDE)
    # filler_27 scores 0 and is 29th in declaration order; only the join reference
    # keeps it, and without it the ## Joins block would name a hidden column.
    assert "filler_27" in [c.physical_name for c in wide.columns]


def test_quoted_identifiers_in_a_join_are_protected():
    from governed_bi.analyst.context import _sql_identifiers

    assert _sql_identifiers('COUNT("Air Carriers"."Code")') == {
        "COUNT",
        "Air Carriers",
        "Code",
    }


# --------------------------------------------------------------------------- #
# 5. Selection is relevance, not the head of the list
# --------------------------------------------------------------------------- #


def test_relevance_beats_declaration_order():
    corpus = _wide_corpus()
    ctx = _ctx(corpus, "how many widgets are there?", max_table_columns=3)
    names = [c.physical_name for c in ctx.tables[0].columns]
    # zz_target is LAST in declaration order; its curated description is the only
    # place the question's vocabulary appears.
    assert "zz_target" in names
    # ... and the head of the declaration order did not simply survive by position.
    assert "filler_00" not in names


def test_name_match_outranks_description_match():
    # The description-match column is placed FIRST so declaration order favours it;
    # only the weight difference can pick the name match.
    cols = [
        _col("other", description="the widget"),  # description hit -> weight 1.0
        _col("widget_count"),  # name hit -> weight 2.0
        _col("unrelated"),
    ]
    corpus = Corpus(assets=[TableAsset(id=WIDE, schema="s", physical_name="wide", columns=cols)])
    ctx = _ctx(corpus, "widget", max_table_columns=1)
    assert [c.physical_name for c in ctx.tables[0].columns] == ["widget_count"]


def test_key_role_outranks_an_unscored_non_key():
    from governed_bi.corpus.schemas import ColumnRole

    cols = [_col("plain_a"), _col("plain_b"), _col("the_key", role=ColumnRole.key)]
    corpus = Corpus(assets=[TableAsset(id=WIDE, schema="s", physical_name="wide", columns=cols)])
    ctx = _ctx(corpus, "nothing matches here", max_table_columns=1)
    assert [c.physical_name for c in ctx.tables[0].columns] == ["the_key"]


def test_selection_is_deterministic():
    corpus = _wide_corpus()
    a = _ctx(corpus, "count the widgets", max_table_columns=7).render()
    b = _ctx(_wide_corpus(), "count the widgets", max_table_columns=7).render()
    assert a == b


# --------------------------------------------------------------------------- #
# 6. Compact reliability caveats (the duplicate-rendering win)
# --------------------------------------------------------------------------- #


def test_compact_caveats_keeps_the_directive_and_every_identifier(beer):
    q = "Which customers are in a given zip code?"
    full = _beer_ctx(beer, q)
    assert full.caveats, "fixture must actually have a suspect column"
    compact = _beer_ctx(beer, q, compact_caveats=True).render()
    assert "## Reliability caveats (DO NOT USE these columns)" in compact
    for ident in full.caveat_columns:
        assert ident in compact
    # The inline warning — the one adjacent to the column — is untouched.
    assert "[SUSPECT - DO NOT USE" in compact


def test_compact_caveats_drops_only_the_duplicated_note_prose(beer):
    q = "Which customers are in a given zip code?"
    full = _beer_ctx(beer, q)
    compact_text = _beer_ctx(beer, q, compact_caveats=True).render()
    assert len(compact_text) < len(full.render())
    # ``caveats`` (and therefore ``n_caveats_injected`` in provenance) is unchanged;
    # only the rendering differs.
    assert _beer_ctx(beer, q, compact_caveats=True).caveats == full.caveats


def test_prompt_context_defaults_to_the_duplicated_rendering():
    """Two independent defaults gate this: the ``assemble_context`` parameter and the
    ``PromptContext`` field. A direct construction (viz, a test, a future caller that
    builds the context itself) goes through the second one, so it is pinned here —
    mutating it alone otherwise leaves the whole suite green."""
    from governed_bi.analyst.context import PromptContext

    ctx = PromptContext(
        question="q",
        caveats=["t.c: bad numbers"],
        caveat_columns=["t.c"],
    )
    assert ctx.compact_caveats is False
    assert "t.c: bad numbers" in ctx.render()


def test_caveat_columns_parallel_caveats(beer):
    ctx = _beer_ctx(beer, "Which customers are in a given zip code?")
    assert len(ctx.caveat_columns) == len(ctx.caveats)
    for ident, line in zip(ctx.caveat_columns, ctx.caveats):
        assert line.startswith(f"{ident}: ")


# --------------------------------------------------------------------------- #
# 7. Config surface
# --------------------------------------------------------------------------- #


def test_settings_defaults_are_off():
    from governed_bi.config import Environment, Settings

    s = Settings.for_env(Environment.dev)
    assert s.analyst_max_table_columns == 0
    assert s.analyst_compact_suspect_caveats is False


def test_analyst_toml_table_is_reachable(tmp_path):
    """A knob only a CLI can set is a knob no deployment can run (the ``[routing]``
    lesson). ``[analyst]`` must load from TOML like ``[routing]`` does."""
    from governed_bi.config import load_settings

    path = tmp_path / "governed_bi.toml"
    path.write_text(
        "[runtime]\nenvironment = 'dev'\n\n"
        "[analyst]\nmax_table_columns = 20\ncompact_suspect_caveats = true\n",
        encoding="utf-8",
    )
    s = load_settings(path)
    assert s.analyst_max_table_columns == 20
    assert s.analyst_compact_suspect_caveats is True


def test_analyst_toml_absent_leaves_the_defaults(tmp_path):
    from governed_bi.config import load_settings

    path = tmp_path / "governed_bi.toml"
    path.write_text("[runtime]\nenvironment = 'dev'\n", encoding="utf-8")
    s = load_settings(path)
    assert s.analyst_max_table_columns == 0
    assert s.analyst_compact_suspect_caveats is False
