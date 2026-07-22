"""Tests for retrieval -> prompt context assembly (analyst.context).

Runs against the committed beer_factory corpus (no DB needed).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from governed_bi.corpus import load_corpus
from governed_bi.graph import build_graph, plan_joins
from governed_bi.retrieval import retrieve
from governed_bi.analyst.context import PromptContext, assemble_context
from governed_bi.analyst.governance import _licensed_table_ids

CORPUS_ROOT = Path(__file__).resolve().parents[1] / "corpus"

TRANSACTION = "tbl_beer_factory_transaction"
CUSTOMERS = "tbl_beer_factory_customers"


@pytest.fixture
def corpus():
    return load_corpus(CORPUS_ROOT, schema="beer_factory").for_analyst()


def _context(corpus, question):
    graph = build_graph(corpus)
    retrieval = retrieve(corpus, question)
    try:
        join_ids = plan_joins(graph, set(retrieval.table_ids)).join_ids
    except ValueError:
        join_ids = []
    licensed_ids = _licensed_table_ids(corpus, graph, retrieval, join_ids)
    return assemble_context(corpus, retrieval, licensed_table_ids=licensed_ids), retrieval


def test_always_notes_reach_context_by_licensed_scope():
    # A global note always applies; a table-scoped note applies only when its
    # table is licensed. This is the wiring that carries Phase B SME caveats into
    # the agent's seeded context (they were previously dropped by retrieval).
    from governed_bi.corpus import Corpus
    from governed_bi.corpus.schemas import (
        Column,
        LogicalType,
        Reliability,
        NoteAsset,
        TableAsset,
    )
    from governed_bi.retrieval import RetrievalResult

    def _tbl(tid: str) -> TableAsset:
        return TableAsset(
            id=tid,
            schema="s",
            physical_name=tid.split("_")[-1],
            columns=[
                Column(
                    physical_name="x",
                    physical_type="INTEGER",
                    logical_type=LogicalType.integer,
                    nullable=True,
                    is_unique=False,
                    reliability=Reliability(),
                )
            ],
        )

    licensed_tbl, other_tbl = _tbl("tbl_s_orders"), _tbl("tbl_s_other")
    corpus = Corpus(
        assets=[
            licensed_tbl,
            other_tbl,
            NoteAsset(id="note_global", kind="business_rule", scope=[], summary="always exclude test rows"),
            NoteAsset(id="note_scoped", kind="context", scope=["tbl_s_orders"], summary="amount is in cents"),
            NoteAsset(id="note_offscope", kind="context", scope=["tbl_s_other"], summary="should not appear"),
            NoteAsset(
                id="note_on_match",
                kind="routing",
                scope=["tbl_s_orders"],
                summary="on-match should not appear",
            ),
        ]
    )
    retrieval = RetrievalResult(question="q", table_ids=["tbl_s_orders"])
    ctx = assemble_context(corpus, retrieval, licensed_table_ids=frozenset({"tbl_s_orders"}))

    assert any("always exclude test rows" in r for r in ctx.rules)  # global
    assert any("amount is in cents" in r for r in ctx.rules)  # scoped to a licensed table
    assert not any("should not appear" in r for r in ctx.rules)  # scoped elsewhere
    block = ctx.render()
    assert "## Governance notes" in block
    assert "amount is in cents" in block
    assert "on-match should not appear" not in block


def test_allowed_table_names_match_licensed_physical_names(corpus):
    ctx, _ = _context(corpus, "total revenue")
    # retrieval surfaces transaction; its 1-hop neighborhood adds customers + rootbeer.
    # Names are schema-qualified (the engine is uniformly multi-schema).
    assert ctx.allowed_table_names() == {
        "beer_factory.transaction",
        "beer_factory.customers",
        "beer_factory.rootbeer",
    }


def test_retrieved_flag_distinguishes_neighbor_tables(corpus):
    ctx, _ = _context(corpus, "total revenue")
    by_name = {t.physical_name: t for t in ctx.tables}
    assert by_name["transaction"].retrieved is True
    assert by_name["customers"].retrieved is False  # reachable only via a join
    assert by_name["rootbeer"].retrieved is False


def test_physical_to_id_round_trips(corpus):
    ctx, _ = _context(corpus, "total revenue")
    assert ctx.physical_to_id()["beer_factory.transaction"] == TRANSACTION
    assert ctx.physical_to_id()["beer_factory.customers"] == CUSTOMERS


def test_columns_resolved_with_facts(corpus):
    ctx, _ = _context(corpus, "total revenue")
    txn = next(t for t in ctx.tables if t.physical_name == "transaction")
    names = {c.physical_name for c in txn.columns}
    assert "PurchasePrice" in names
    price = next(c for c in txn.columns if c.physical_name == "PurchasePrice")
    assert price.logical_type  # a resolved logical type string


def test_metric_resolved_over_physical_base_table(corpus):
    ctx, _ = _context(corpus, "total revenue")
    assert any(m.base_table == "transaction" and "PurchasePrice" in m.expression for m in ctx.metrics)


def test_excluded_column_never_appears(corpus):
    # The PII CreditCardNumber column is governance.excluded -> for_analyst() drops
    # it, so context must never surface it.
    ctx, _ = _context(corpus, "total revenue")
    all_cols = {c.physical_name for t in ctx.tables for c in t.columns}
    assert "CreditCardNumber" not in all_cols


def test_render_lists_only_licensed_tables_and_is_a_string(corpus):
    ctx, _ = _context(corpus, "total revenue")
    text = ctx.render()
    assert isinstance(text, str)
    assert "## Tables (use ONLY these physical identifiers)" in text
    assert "transaction" in text
    # rootbeerreview is 3 hops out -> not licensed -> must not be presented AS A
    # TABLE. Note prose may still mention it by name; guardrail scope comes from
    # allowed_table_names, not free text.
    assert "rootbeerreview" not in ctx.allowed_table_names()
    assert "### rootbeerreview" not in text


def test_render_includes_join_paths_when_present(corpus):
    ctx, _ = _context(corpus, "total revenue")
    # transaction <-> customers and transaction <-> rootbeer are internal to the
    # licensed set, so at least one join path is rendered.
    assert ctx.joins
    text = ctx.render()
    assert "## Joins" in text


def test_conversation_history_renders_into_context(corpus):
    graph = build_graph(corpus)
    retrieval = retrieve(corpus, "total revenue")
    try:
        join_ids = plan_joins(graph, set(retrieval.table_ids)).join_ids
    except ValueError:
        join_ids = []
    licensed_ids = _licensed_table_ids(corpus, graph, retrieval, join_ids)
    history = [("user", "What is the total revenue?"), ("assistant", "total_revenue = 18496.0")]
    ctx = assemble_context(corpus, retrieval, licensed_table_ids=licensed_ids, history=history)
    assert ctx.conversation == history
    text = ctx.render()
    assert "## Conversation so far" in text
    assert "user: What is the total revenue?" in text
    assert "assistant: total_revenue = 18496.0" in text


def test_no_history_means_no_conversation_section(corpus):
    ctx, _ = _context(corpus, "total revenue")
    assert ctx.conversation == []
    assert "## Conversation so far" not in ctx.render()


def test_empty_retrieval_yields_empty_but_valid_context(corpus):
    from governed_bi.retrieval import RetrievalResult

    empty = RetrievalResult(question="nothing matches xyzzy")
    ctx = assemble_context(corpus, empty, licensed_table_ids=frozenset())
    assert isinstance(ctx, PromptContext)
    assert ctx.tables == []
    assert ctx.allowed_table_names() == frozenset()
    assert "## Tables" in ctx.render()
