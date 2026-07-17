"""Offline retrieval recall@k harness (eval/retrieval_eval.py).

Runs against the committed beer_factory corpus + the hand-authored
``BEER_FACTORY_EVAL`` gold set, so no live DB / model / BIRD checkout is needed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from governed_bi.corpus import load_corpus
from governed_bi.eval.dataset import BEER_FACTORY_EVAL
from governed_bi.eval.retrieval_eval import evaluate_retrieval, gold_table_ids

CORPUS_ROOT = Path(__file__).resolve().parents[1] / "corpus"
TRANSACTION = "tbl_beer_factory_transaction"
CUSTOMERS = "tbl_beer_factory_customers"


@pytest.fixture
def corpus():
    return load_corpus(CORPUS_ROOT, schema="beer_factory").for_analyst()


def test_gold_table_ids_extracts_referenced_tables(corpus):
    ids = gold_table_ids(corpus, 'SELECT SUM(PurchasePrice) FROM "transaction"')
    assert ids == frozenset({TRANSACTION})
    # a join names both tables; a CTE / unknown name is ignored
    ids2 = gold_table_ids(
        corpus,
        'WITH x AS (SELECT 1) SELECT * FROM customers c JOIN "transaction" t '
        "ON t.CustomerID = c.CustomerID",
    )
    assert ids2 == frozenset({CUSTOMERS, TRANSACTION})


def test_gold_table_ids_empty_on_unparseable_or_unknown(corpus):
    assert gold_table_ids(corpus, "this is not sql (((") == frozenset()
    assert gold_table_ids(corpus, "SELECT 1") == frozenset()  # no table


def test_recall_on_committed_gold_set(corpus):
    report = evaluate_retrieval(corpus, BEER_FACTORY_EVAL, top_k=8)
    assert report.n == len(BEER_FACTORY_EVAL)
    assert report.skipped == 0
    # Every gold table is reachable within the licensed scope (bounds achievable EX).
    assert report.hit_rate_licensed == 1.0
    # Retrieval surfaces every gold table directly on this set (tokenizer splits
    # camelCase + stems plurals, so "transactions" matches "transaction").
    assert report.hit_rate_retrieved == 1.0


def test_report_format_is_stringable(corpus):
    report = evaluate_retrieval(corpus, BEER_FACTORY_EVAL, top_k=8)
    text = report.format()
    assert "retrieval recall" in text and "licensed=" in text
