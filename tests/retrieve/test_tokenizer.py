"""Punctuation is not part of a term. Compounds stay whole and also split, on both sides."""

from __future__ import annotations

import pytest

from governed_bi.retrieve.lexical import BM25, _tokenize


def test_a_query_term_matches_the_same_term_wearing_a_comma() -> None:
    """The defect, at its smallest. This is the whole bug in one assertion."""
    document = "geographic (geografisch): city, county, region"
    assert _tokenize(document) == ["geographic", "geografisch", "city", "county", "region"]
    assert "county" in _tokenize("List all the cities in Sonoma County.")


def test_an_identifier_survives_tokenisation_whole() -> None:
    """A compound stays whole and also splits. Replacing it with only its parts would drop the discriminating token."""
    tokens = _tokenize("CBSA (CBSA): CBSA, CBSA_name, CBSA_type")
    assert tokens.count("cbsa") == 3 + 2, "three bare CBSA, plus one part from each compound"
    for whole in ("cbsa_name", "cbsa_type"):
        assert whole in tokens, f"{whole!r} was split away instead of kept beside its parts"

    compounds = _tokenize("avg_house_value, top-reviewed, a/b")
    for whole in ("avg_house_value", "top-reviewed", "a/b"):
        assert whole in compounds, f"{whole!r} did not survive whole"
    # The parts are additional, not a replacement.
    assert {"avg", "house", "value", "top", "reviewed", "a", "b"} <= set(compounds)


def test_a_plain_word_is_not_emitted_twice() -> None:
    """Splitting unconditionally would emit an ordinary word twice and double its term frequency."""
    assert _tokenize("city county region") == ["city", "county", "region"]
    assert _tokenize("a_b c") == ["a_b", "a", "b", "c"]


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
    """The question form and the curated summary must meet. Glued punctuation made them score zero."""
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


def test_repeating_a_query_term_buys_no_score() -> None:
    """Query term frequency is not a weight. A saturated query-side factor still let a double mention outrank a single
    one."""
    index = BM25([("a", "restaurant cuisine and food_type for the city")])
    once = index.search("cuisine")[0][1]
    thrice = index.search("cuisine cuisine cuisine")[0][1]

    assert once > 0.0, "precondition: the term matches at all"
    assert thrice == once, (
        f"repeating a term changed the score ({once:.4f} -> {thrice:.4f}), so a rewriter's "
        "phrasing decides the ranking"
    )


def test_coverage_does_not_credit_a_corpus_for_holding_the_word_the() -> None:
    """Function words are not evidence the corpus knows the question. The answerable control is what fails if they are
    counted."""
    bm = BM25([("a", "customers orders revenue by region")])
    unanswerable = bm.coverage("Which of the liturgical vestments were embroidered?")
    assert unanswerable is not None
    assert unanswerable == 0.0, (
        f"a question with no content word in the corpus scored {unanswerable:.2f}, so the score "
        "is measuring the corpus's stock of English rather than its subject"
    )
    # The control: the same shape of question, answerable, still scores.
    assert bm.coverage("Which of the customers were in the region?") == pytest.approx(1.0)


def test_a_query_of_nothing_but_function_words_is_unmeasurable_not_zero() -> None:
    """``None`` and never 0.0, for the same reason a blank query is ``None``.

    Zero would assert that the corpus failed to match something — and what it failed to match
    would be ``the`` and ``of``.
    """
    bm = BM25([("a", "customers orders revenue by region")])
    assert bm.coverage("what are the most of these?") is None
    assert bm.coverage("") is None


def test_the_stopword_list_is_not_applied_to_scoring() -> None:
    """Function words stay in scoring. IDF already discounts them, and stripping them would move every pinned score."""
    assert "the" in _tokenize("the customer"), "the tokenizer dropped a function word"
    index = BM25([("a", "the customer of the region"), ("b", "the vendor of the region")])
    assert dict(index.search("the"))["a"] > 0.0, (
        "a function word stopped matching, so the filter leaked out of `coverage` into scoring"
    )


def test_a_natural_language_phrase_reaches_a_snake_case_identifier() -> None:
    """A spaced phrase must score a snake_case identifier, and the exact identifier must still rank higher."""
    index = BM25([("a", "the food_type column of the restaurant table")])
    phrase = index.search("food type")[0][1]
    exact = index.search("food_type")[0][1]

    assert phrase > 0.0, "a question spelled as words cannot reach a snake_case identifier"
    assert exact > phrase, (
        f"the exact identifier ({exact:.4f}) must outrank the phrase ({phrase:.4f}); otherwise the "
        "split has thrown away what makes an identifier query precise"
    )


def test_coverage_counts_a_compound_as_one_term() -> None:
    """The split belongs to scoring. Coverage counts the compound as one term, or parts the corpus happens to hold look
    like coverage."""
    corpus = BM25([("a", "food and type are separate words here")])
    assert corpus.coverage("food_type") == 0.0, (
        "the corpus has the compound's parts and not the compound, so it knows none of this term"
    )
    # The control: scoring still splits, which is the whole point of I2.
    assert "food" in _tokenize("food_type")
    assert BM25([("a", "the food_type column")]).search("food type")[0][1] > 0.0


def test_no_rejected_stopword_is_in_the_list() -> None:
    """Review finding 10. ``may`` is a month, ``am`` is a time, ``no`` heads "invoice no".

    Structural, so the two sets cannot drift back together, and paired with the behavioural test
    below because a set-difference assertion alone says nothing about what ``coverage`` does.
    """
    from governed_bi.retrieve.lexical import _REJECTED_AS_TOO_CONTENTFUL, _STOPWORDS

    assert _REJECTED_AS_TOO_CONTENTFUL, "the rejected set is empty, so this asserts nothing"
    assert not (_STOPWORDS & _REJECTED_AS_TOO_CONTENTFUL), (
        f"content words are being filtered out of coverage: "
        f"{sorted(_STOPWORDS & _REJECTED_AS_TOO_CONTENTFUL)}"
    )


def test_a_question_about_a_month_the_corpus_lacks_scores_low() -> None:
    """Filtering a month makes an unanswerable question look fully covered, because the remaining word is in the corpus.
    """
    corpus = BM25([("a", "orders revenue region customers")])
    assert corpus.coverage("orders in may") == pytest.approx(0.5), (
        "the month carrying the question's constraint is not being counted"
    )


def test_document_length_counts_words_not_index_terms() -> None:
    """Document length counts words. A compound's parts must not lengthen the summary that carries them."""
    index = BM25(
        [
            ("id_heavy", "customer_id order_id product_id line_item_id"),
            ("plain", "customer order product line item"),
        ]
    )
    assert index._dl == [4, 5], f"length is counting index terms, not words: {index._dl}"
    assert index._dl[0] < index._dl[1], (
        "four identifiers are being treated as a longer document than five plain words"
    )
    # The parts are still indexed — length is the only thing that changed.
    assert index._tf[0]["customer"] == 1
