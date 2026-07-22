"""Tests for the RVGD retrieval slice: BM25 index + Ground expansion.

Runs against the committed ``corpus/beer_factory`` semantic layer (its
``for_analyst()`` view), so no live database is needed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from governed_bi.corpus import Corpus, load_corpus
from governed_bi.corpus.ids import derive_column_id
from governed_bi.corpus.schemas import (
    Column,
    FewShotAsset,
    LogicalType,
    NoteAsset,
    TableAsset,
    TermAsset,
    TermBinding,
)
from governed_bi.retrieval import BM25Index, RetrievalResult, retrieve, tokenize

CORPUS_ROOT = Path(__file__).resolve().parents[1] / "corpus"

TRANSACTION = "tbl_beer_factory_transaction"
BRAND = "tbl_beer_factory_rootbeerbrand"
REVIEW = "tbl_beer_factory_rootbeerreview"


@pytest.fixture
def corpus():
    return load_corpus(CORPUS_ROOT, schema="beer_factory").for_analyst()


# --------------------------------------------------------------------------- #
# Tokenizer + BM25 unit behavior
# --------------------------------------------------------------------------- #


def test_tokenize_lowercases_and_splits_on_non_alphanumeric():
    assert tokenize("Total Revenue, by Brand!") == ["total", "revenue", "by", "brand"]
    assert tokenize("") == []
    assert tokenize("   \t\n ") == []


def test_tokenize_splits_camelcase_and_stems_plurals():
    # camelCase / PascalCase physical names split into their words...
    assert tokenize("SUM(PurchasePrice)") == ["sum", "purchase", "price"]
    assert tokenize("CustomerID") == ["customer", "id"]
    assert tokenize("StarRating") == ["star", "rating"]
    # ...and simple plurals collapse so a query term matches the singular name.
    assert tokenize("transactions") == ["transaction"]
    assert tokenize("companies") == ["company"]
    # -ss words and short tokens are left alone.
    assert tokenize("address") == ["address"]
    assert tokenize("is") == ["is"]


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
    # and dropped by for_analyst(), so it must not appear as a retrieved column.
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
        assert result.note_ids == []
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


def _plain_table(tid: str, physical: str, *, desc: str = "") -> TableAsset:
    return TableAsset(
        id=tid,
        schema="shop",
        physical_name=physical,
        description=desc,
        columns=[
            Column(
                physical_name="id",
                physical_type="int",
                logical_type=LogicalType.integer,
                nullable=False,
                is_unique=True,
            )
        ],
    )


def test_per_type_budget_keeps_tables_when_few_shots_flood():
    # A pile of few-shots whose questions all match the query would fill a single
    # pooled top-k and starve the table (whose gold SQL the few-shots do not name,
    # so grounding cannot rescue it). Per-type budgets keep the table its slot.
    table = _plain_table("tbl_shop_widgets", "widgets", desc="widget catalog")
    few_shots = [
        FewShotAsset(
            id=f"fs_{i:02d}",
            schema="shop",
            question="widget widget widget report count",
            sql="SELECT 1",  # references no real table -> no grounding rescue
        )
        for i in range(12)
    ]
    res = retrieve(Corpus(assets=[table, *few_shots]), "widget report count")
    assert "tbl_shop_widgets" in res.table_ids  # survived the few-shot flood
    assert len(res.few_shot_ids) <= 3  # few-shot budget capped


def test_few_shot_grounds_its_referenced_tables():
    # The target table does NOT lexically match the query, so BM25 never surfaces
    # it directly; only the matching few-shot's gold SQL points at it. Grounding
    # the few-shot's tables must pull it into scope.
    table = _plain_table("tbl_shop_orders", "orders", desc="zzz unrelated words")
    fs = FewShotAsset(
        id="fs_orders",
        schema="shop",
        question="how many purchases were made",
        sql="SELECT COUNT(*) FROM orders",
    )
    res = retrieve(Corpus(assets=[table, fs]), "how many purchases were made")
    assert "fs_orders" in res.few_shot_ids
    assert "tbl_shop_orders" in res.table_ids  # grounded from the few-shot's SQL


@pytest.mark.xfail(
    reason="field weights are flat (_SEMANTIC_BOOST=1) for now; preferring the "
    "curated description over a matching raw/decoy name is a production-tuning "
    "target (raise _SEMANTIC_BOOST). Kept as an executable spec of the goal.",
    strict=False,
)
def test_curated_semantics_outrank_a_decoy_raw_name():
    # Governed-BI thesis: the curated description is the trusted match surface; a
    # raw / decoy physical name must NOT outrank it. The real table has an opaque
    # (obfuscated) name but a description that matches the question; the decoy has
    # an attractive camelCase name that tokenizes straight onto the query but a
    # description that does not. Under flat weights the decoy's raw name actually
    # WINS (this is the regression the boost must overcome), so this is xfail until
    # the boost is tuned up on the obfuscated eval.
    real = _plain_table("tbl_real", "t_042", desc="monthly sales revenue by region")
    decoy = _plain_table("tbl_decoy", "TotalRevenue", desc="internal audit log, do not use")
    res = retrieve(Corpus(assets=[real, decoy]), "total revenue")
    assert res.scores["tbl_real"] > res.scores["tbl_decoy"]


def test_confidence_breaks_ties_toward_more_trusted_asset():
    # Two terms with identical text score identically; the higher-confidence one
    # must order first (mild prior — ties only).
    low = TermAsset(id="term_low", name="vip", synonyms=[], confidence=0.5)
    high = TermAsset(id="term_high", name="vip", synonyms=[], confidence=0.95)
    res = retrieve(Corpus(assets=[low, high]), "vip")
    assert res.scores["term_low"] == res.scores["term_high"]  # a genuine tie
    assert res.term_ids[0] == "term_high"  # confidence, not id, wins the tie


def test_note_indexes_summary_only_and_has_own_budget():
    note = NoteAsset(
        id="note_revenue",
        kind="routing",
        summary="Use the governed revenue metric.",
        body="body-only-secret-token",
    )
    corpus = Corpus(assets=[note])
    assert retrieve(corpus, "governed revenue").note_ids == ["note_revenue"]
    assert retrieve(corpus, "body-only-secret-token").note_ids == []
    assert retrieve(corpus, "governed revenue", note_k=0).note_ids == []


def test_vector_weight_scales_semantic_channel():
    from governed_bi.retrieval.embedding import fuse_rankings

    lexical = [("a", 9.0), ("b", 1.0)]
    semantic = [("b", 9.0), ("a", 1.0)]
    # Equal weight: a and b tie on rank-sum, so id breaks the tie (a first).
    assert [i for i, _ in fuse_rankings(lexical, semantic)] == ["a", "b"]
    # Down-weight semantic: lexical's top (a) wins outright.
    down = fuse_rankings(lexical, semantic, weights=[1.0, 0.1])
    assert down[0][0] == "a" and down[0][1] > down[1][1]
