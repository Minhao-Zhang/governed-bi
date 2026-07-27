"""Refuse-gate topic matching (D5) against the *shipped* negative example.

The gate is the curated high-recall net: refusal is driven by a curated signal, not
a coverage heuristic (D5), so a direct paraphrase of a curated ``pattern`` must
refuse even when it shares only one of the pattern's words. Every case here keys on
the real ``corpus/beer_factory/negatives/`` asset rather than a hand-built fixture,
because a fixture written to match the implementation is exactly how the previous
version of this gate shipped a recall regression with a green suite: the only
existing test used a verbatim ``example_questions`` entry, which the *other* branch
catches, so the pattern branch was never executed.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from langchain_core.messages import AIMessage

from governed_bi.analyst.agent import answer_question_agent
from governed_bi.analyst.answer import ReliabilityTier
from governed_bi.analyst.governance import _match_negative_example
from governed_bi.config import Environment, Settings
from governed_bi.corpus import load_corpus
from governed_bi.gateway import Gateway, Identity, SqliteConnector
from governed_bi.llm.fake import FakeToolModel

CORPUS_ROOT = Path(__file__).resolve().parents[1] / "corpus"
NEG_ID = "neg_beer_factory_001"


@pytest.fixture
def corpus():
    return load_corpus(CORPUS_ROOT, schema="beer_factory").for_analyst()


@pytest.fixture
def negative(corpus):
    asset = corpus.by_id(NEG_ID)
    # Guard the premise: if the shipped asset is ever re-curated away from the
    # staffing topic, the cases below stop meaning anything and must be rewritten
    # rather than silently pass.
    assert asset is not None and "headcount" in asset.pattern
    return asset


@pytest.mark.parametrize(
    "question",
    [
        "What is the total headcount?",
        "What is the average staffing level per shift?",
        # Singular of a plural pattern word - the commonest paraphrase shape.
        "Who is the highest paid employee at the factory?",
    ],
)
def test_paraphrase_of_curated_pattern_refuses(corpus, negative, question):
    """A paraphrase must hit the pattern even sharing one word out of several.

    These are not example questions, so only the pattern branch can catch them.
    """
    assert _match_negative_example(corpus, question) is negative


def test_verbatim_example_questions_still_refuse(corpus, negative):
    """The example-question branch is untouched; keep it that way."""
    for example in negative.example_questions:
        assert _match_negative_example(corpus, example) is negative


@pytest.mark.parametrize(
    "question",
    [
        # Shares the pattern's ONLY filler word ("questions"): analytics filler
        # alone carries no topical signal and must never refuse.
        "How many open questions are there per brand review?",
        "List all root beer brands by average star rating",
        "What is the total revenue for all customers?",
    ],
)
def test_answerable_question_sharing_only_filler_is_not_refused(corpus, negative, question):
    assert _match_negative_example(corpus, question) is None


def test_paraphrase_stops_at_the_gate_on_the_serve_path(corpus):
    """End-to-end: the paraphrase refuses *before* the model is ever invoked.

    Binds the matcher rule to the governed outcome - the eval harness scores a
    refusal and a crash identically, so a recall change here moves measured
    accuracy without looking like a bug.
    """
    conn = SqliteConnector(":memory:")
    try:
        ans = answer_question_agent(
            "What is the total headcount?",
            Identity(user="dev", all_access=True),
            corpus=corpus,
            gateway=Gateway(conn),
            settings=Settings.for_env(Environment.dev),
            session_id="refuse-gate-paraphrase",
            model=FakeToolModel(responses=[AIMessage(content="should not run")]),
        )
    finally:
        conn.close()
    assert ans.tier is ReliabilityTier.refused
    assert ans.provenance["refused_by"] == "refuse_gate"
    assert ans.provenance["negative_example"] == NEG_ID
    assert ans.sql is None
