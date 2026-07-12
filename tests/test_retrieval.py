"""Tests for the RVGD retrieval slice: BM25 index + Ground expansion.

Runs against the committed ``corpus/beer_factory`` semantic layer (its
``for_server()`` view), so no live database is needed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from governed_bi.corpus import Corpus, load_corpus
from governed_bi.corpus.ids import derive_column_id
from governed_bi.corpus.schemas import Column, LogicalType, TableAsset, TermAsset, TermBinding
from governed_bi.retrieval import BM25Index, RetrievalResult, retrieve, tokenize

CORPUS_ROOT = Path(__file__).resolve().parents[1] / "corpus"

TRANSACTION = "tbl_beer_factory_transaction"
BRAND = "tbl_beer_factory_rootbeerbrand"
REVIEW = "tbl_beer_factory_rootbeerreview"


@pytest.fixture
def corpus():
    return load_corpus(CORPUS_ROOT, schema="beer_factory").for_server()


# --------------------------------------------------------------------------- #
# Tokenizer + BM25 unit behavior
# --------------------------------------------------------------------------- #


def test_tokenize_lowercases_and_splits_on_non_alphanumeric():
    assert tokenize("Total Revenue, by Brand!") == ["total", "revenue", "by", "brand"]
    assert tokenize("SUM(PurchasePrice)") == ["sum", "purchaseprice"]
    assert tokenize("") == []
    assert tokenize("   \t\n ") == []


def test_bm25_ranks_matching_document_first():
    index = BM25Index.from_documents(
        {
            "a": "revenue sales total",
            "b": "brand label make",
            "c": "customer review rating",
        }
    )
    ranked = index.rank("total revenue")
    assert ranked, "expected at least one match"
    assert ranked[0][0] == "a"
    # only the doc that shares a term scores > 0
    assert all(score > 0.0 for _, score in ranked)


def test_bm25_empty_query_returns_nothing():
    index = BM25Index.from_documents({"a": "revenue", "b": "brand"})
    assert index.rank("") == []
    assert index.rank("   ") == []


# --------------------------------------------------------------------------- #
# retrieve(): grounding + typed partition
# --------------------------------------------------------------------------- #


def test_revenue_by_brand_surfaces_metric_and_grounds_tables(corpus):
    result = retrieve(corpus, "total revenue by brand")

    # the revenue term/metric are surfaced (at least one)
    assert "metric_revenue" in result.metric_ids or "term_revenue" in result.term_ids
    # grounding pulls in the metric's base table (transaction) and the brand table
    assert TRANSACTION in result.table_ids
    assert BRAND in result.table_ids


def test_customer_reviews_and_ratings_surfaces_review_assets(corpus):
    result = retrieve(corpus, "customer reviews and ratings")
    assert REVIEW in result.table_ids or "metric_avg_rating" in result.metric_ids


def test_scores_non_empty_and_deterministically_ordered(corpus):
    result = retrieve(corpus, "total revenue by brand")
    assert result.scores, "a matching question must produce non-empty scores"
    assert all(score > 0.0 for score in result.scores.values())

    # scores dict is in descending score / ascending id order...
    items = list(result.scores.items())
    assert items == sorted(items, key=lambda kv: (-kv[1], kv[0]))

    # ...and retrieval is fully deterministic across calls.
    again = retrieve(corpus, "total revenue by brand")
    assert result == again
    assert isinstance(result, RetrievalResult)


def test_selected_tables_contribute_their_columns(corpus):
    result = retrieve(corpus, "total revenue by brand")
    assert BRAND in result.table_ids
    # every column of a selected table is present, via the loader's derivation
    brand = corpus.by_id(BRAND)
    for col in brand.columns:
        assert derive_column_id(BRAND, col.physical_name) in result.column_ids
    # a specific, human-recognizable column id is present
    assert "col_beer_factory_rootbeerbrand_BrandName" in result.column_ids


def test_excluded_column_never_reaches_column_ids(corpus):
    # transaction is grounded in for this query; its PII column is governance.excluded
    # and dropped by for_server(), so it must not appear as a retrieved column.
    result = retrieve(corpus, "total revenue by brand")
    assert TRANSACTION in result.table_ids
    assert "col_beer_factory_transaction_CreditCardNumber" not in result.column_ids


def test_empty_and_whitespace_question_return_empty_result(corpus):
    for question in ("", "   \t "):
        result = retrieve(corpus, question)
        assert isinstance(result, RetrievalResult)
        assert result.question == question
        assert result.table_ids == []
        assert result.column_ids == []
        assert result.term_ids == []
        assert result.metric_ids == []
        assert result.few_shot_ids == []
        assert result.scores == {}


def test_no_match_question_returns_empty_result(corpus):
    # a question with no lexical overlap with any indexed asset
    result = retrieve(corpus, "xyzzy qwerty zzzznope")
    assert result.scores == {}
    assert result.table_ids == []
    assert result.metric_ids == []


def test_top_k_controls_breadth(corpus):
    # a smaller top_k seeds fewer assets; grounding is a monotonic closure, so a
    # narrow result's scored ids are a subset of a wide one's.
    question = "brand rating review revenue customer"
    narrow = retrieve(corpus, question, top_k=1)
    wide = retrieve(corpus, question, top_k=20)
    assert isinstance(narrow, RetrievalResult)
    assert set(narrow.scores) <= set(wide.scores)
    assert len(wide.scores) >= len(narrow.scores) >= 1


def test_term_bound_to_column_grounds_owning_table():
    # A term bound to a column must surface both the column and its owning table.
    col_id = derive_column_id("tbl_shop_orders", "LifecycleStatus")
    table = TableAsset(
        id="tbl_shop_orders",
        schema="shop",
        physical_name="orders",
        columns=[
            Column(
                physical_name="LifecycleStatus",
                physical_type="text",
                logical_type=LogicalType.string,
                nullable=True,
                is_unique=False,
                description="whether the customer churned",
            )
        ],
    )
    term = TermAsset(
        id="term_churned",
        name="churned",
        synonyms=["churn"],
        binding=TermBinding(asset_type="column", asset_id=col_id),
    )
    res = retrieve(Corpus(assets=[table, term]), "churned customers")
    assert "term_churned" in res.term_ids
    assert "tbl_shop_orders" in res.table_ids
    assert col_id in res.column_ids
