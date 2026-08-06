"""The BM25 tokenizer, which nothing tested.

``tests/retrieve/test_scoring_contract.py`` bounds BM25's scores and pins global IDF; it says
nothing about how text becomes terms. So ``_TOKEN = re.compile(r"\\S+")`` — whitespace-only,
no punctuation stripping — survived, and with it a vocabulary in which ``county``, ``county,``
and ``county.`` are three unrelated terms with three separate IDFs.

Measured on ``corpora/gold-semantic-layer-20260804`` before the fix: 70.3% of table-summary
tokens carried glued punctuation, only 3.1% of column physical names appeared as a clean token
in their own table's summary, the index vocabulary was 25 815 whitespace tokens against 12 397
real words, and the lexical channel scored nothing at all against all 57 schema summaries on
66.7% of held-out questions.
"""

from __future__ import annotations

from governed_bi.retrieve.lexical import BM25, _tokenize


def test_a_query_term_matches_the_same_term_wearing_a_comma() -> None:
    """The defect, at its smallest. This is the whole bug in one assertion."""
    document = "geographic (geografisch): city, county, region"
    assert _tokenize(document) == ["geographic", "geografisch", "city", "county", "region"]
    assert "county" in _tokenize("List all the cities in Sonoma County.")


def test_an_identifier_survives_tokenisation_whole() -> None:
    """Stripping punctuation must not also split the names the corpus is made of.

    Under obfuscation the identifier *is* the discriminating token — ``avg_house_value`` and
    ``CBSA_name`` are the only English in a routing document — so a tokenizer that broke them
    into ``avg``/``house``/``value`` would trade one failure for a worse one.
    """
    assert _tokenize("CBSA (CBSA): CBSA, CBSA_name, CBSA_type") == [
        "cbsa",
        "cbsa",
        "cbsa",
        "cbsa_name",
        "cbsa_type",
    ]
    assert _tokenize("avg_house_value, top-reviewed, a/b") == [
        "avg_house_value",
        "top-reviewed",
        "a/b",
    ]


def test_punctuation_no_longer_mints_its_own_high_idf_term() -> None:
    """``idf["name?"]`` was 7.02 against ``idf["name"]`` 2.87 on the real corpus.

    A trailing question mark therefore contributed the single largest IDF term in the query,
    and it matched only the handful of documents that happened to end the same way.
    """
    docs = [
        (f"d{i}", "customer name and email address for the account")
        for i in range(20)
    ]
    docs.append(("odd", "who is the officer name?"))
    index = BM25(docs)
    idf = index._idf
    assert "name?" not in idf, "punctuation is still a term of its own"
    assert "name" in idf
    # The question's tokens all reach the vocabulary, so a real match is possible.
    assert all(t in idf for t in _tokenize("what is the officer name?") if t != "what")


def test_a_natural_language_question_now_scores_a_curated_summary() -> None:
    """End to end over the two document forms that never met.

    The summary is the shape the seeder writes; the question is the shape a user asks. Before
    the fix this pair scored exactly 0.0 — measured across the full corpus, no schema summary
    scored above zero on 66.7% of held-out questions.
    """
    index = BM25(
        [
            ("restaurant.geografisch", "geographic (geografisch): city, county, region"),
            ("airline.Airports", "airports (Airports): code, description"),
        ]
    )
    scores = dict(index.search("List all the cities in Sonoma County."))
    assert scores["restaurant.geografisch"] > 0.0, (
        "a question about cities and counties still scores zero against a summary listing "
        "city, county and region"
    )
    assert scores["restaurant.geografisch"] > scores["airline.Airports"]


def test_tokenisation_is_symmetric_across_the_two_sides() -> None:
    """One function, both sides. An asymmetric tokenizer is a subtler version of the bug."""
    text = "movie_platform: 5 tables, lists_users, movies."
    assert _tokenize(text) == _tokenize(text.upper())
