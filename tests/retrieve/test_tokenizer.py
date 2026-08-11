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

import pytest

from governed_bi.retrieve.lexical import BM25, _tokenize


def test_a_query_term_matches_the_same_term_wearing_a_comma() -> None:
    """The defect, at its smallest. This is the whole bug in one assertion."""
    document = "geographic (geografisch): city, county, region"
    assert _tokenize(document) == ["geographic", "geografisch", "city", "county", "region"]
    assert "county" in _tokenize("List all the cities in Sonoma County.")


def test_an_identifier_survives_tokenisation_whole() -> None:
    """Stripping punctuation must not also split the names the corpus is made of.

    Under obfuscation the identifier *is* the discriminating token — ``avg_house_value`` and
    ``CBSA_name`` are the only English in a routing document — so a tokenizer that *replaced* them
    with ``avg``/``house``/``value`` would trade one failure for a worse one.

    **Asserted as presence rather than as an exact list, since audit I2.** A compound now yields
    the whole token *and* its parts, so the question "how many food types are there" can reach a
    ``food_type`` column at all — measured before that change, ``search("food type")`` against a
    document containing ``food_type`` returned 0.0. The property this test is named for is
    unchanged and is still asserted: the whole identifier is there. What is no longer asserted is
    that it is the *only* thing there, which was over-specified for the property.
    """
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
    """The compound split must not double an ordinary word's term frequency (audit I2).

    ``_JOINERS.split("city")`` is ``["city"]``, so emitting the parts unconditionally would put
    every non-compound token in twice — doubling its ``tf`` and its contribution to document
    length, which BM25 normalises against. Cheap to get wrong and invisible in a score.
    """
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


def test_repeating_a_query_term_buys_no_score() -> None:
    """Audit I3. The query's term frequency is not a weight.

    The score multiplied each term by its raw ``qf``, Okapi's ``k3 -> infinity`` limit, so a
    repeated word bought unbounded score: measured, ``search("cuisine")`` = 0.4498 against
    ``search("cuisine cuisine cuisine")`` = 0.7103 on the same document. The five facet rewriters
    *generate* these queries, so this decided routing on a model's phrasing — a rewriter that said
    a keyword twice outranked one that said it once on identical evidence.

    A first fix saturated with ``k3 = 8`` and the numbers convicted it: ``qf = 2`` still scored
    1.8x. The factor is dropped, as Lucene drops it.
    """
    index = BM25([("a", "restaurant cuisine and food_type for the city")])
    once = index.search("cuisine")[0][1]
    thrice = index.search("cuisine cuisine cuisine")[0][1]

    assert once > 0.0, "precondition: the term matches at all"
    assert thrice == once, (
        f"repeating a term changed the score ({once:.4f} -> {thrice:.4f}), so a rewriter's "
        "phrasing decides the ranking"
    )


def test_coverage_does_not_credit_a_corpus_for_holding_the_word_the() -> None:
    """Audit I4. Function words are not evidence that the corpus knows the question.

    ``coverage`` feeds ``weak_retrieval`` and is the only signal that can say "nothing here is
    relevant" — cosine cannot, because with an embedder every asset scores above zero. Counting
    every distinct token gave every English question a floor made of its function words:
    measured on the 13,304-asset BIRD corpus, ``the`` is in 76% of summaries and ``a`` in 45%,
    so a question the corpus could not answer scored 0.50 rather than near zero.

    The corpus below knows nothing about vestments, and none of the four words carrying the
    question's meaning is in it. **The assertion on `unanswerable` alone cannot fail** — it reads
    0.0 with the filter and without — so the property lives in the *control* below, which drops to
    2/7 when the filter is removed. Flagged in review, where an earlier version of this docstring
    also claimed "before the filter this scored 0.4"; it did not, it scored 0.0.
    """
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
    """The asymmetry is deliberate, so it is asserted rather than left to a comment.

    BM25 needs no list: a term in most documents has an IDF near zero and is discounted by
    construction. Removing function words from ``_tokenize`` would instead change document
    lengths, IDF, and every score in the index — and the sealed scoring contract pins those.
    """
    assert "the" in _tokenize("the customer"), "the tokenizer dropped a function word"
    index = BM25([("a", "the customer of the region"), ("b", "the vendor of the region")])
    assert dict(index.search("the"))["a"] > 0.0, (
        "a function word stopped matching, so the filter leaked out of `coverage` into scoring"
    )


def test_a_natural_language_phrase_reaches_a_snake_case_identifier() -> None:
    """Audit I2, as a score rather than as a token list.

    ``search("food type")`` against a document holding ``food_type`` returned **0.0** — the whole
    defect in one number. The exact identifier must still rank above the phrase, or the compound
    split has traded one recall failure for another.
    """
    index = BM25([("a", "the food_type column of the restaurant table")])
    phrase = index.search("food type")[0][1]
    exact = index.search("food_type")[0][1]

    assert phrase > 0.0, "a question spelled as words cannot reach a snake_case identifier"
    assert exact > phrase, (
        f"the exact identifier ({exact:.4f}) must outrank the phrase ({phrase:.4f}); otherwise the "
        "split has thrown away what makes an identifier query precise"
    )


def test_coverage_counts_a_compound_as_one_term() -> None:
    """Review finding 9. The split belongs to scoring, not to ``coverage``.

    Audit I2 made ``_tokenize`` emit a compound's parts beside it, which is what lets a question
    spelled as words reach ``food_type``. It also turned one query word into three coverage terms
    whose parts are ordinary English, so a corpus that happens to hold ``food`` and ``type``
    separately reported two thirds coverage for a compound it does not have.
    """
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
    """The behavioural half of finding 10, on the corpus shape that shows it.

    With ``may`` filtered, this question's only surviving term was ``orders``, which the corpus has
    — so a question it cannot answer reported **1.0**, the I4 defect in the other direction.
    Measured on this fixture: 1.0 before, 0.5 after.
    """
    corpus = BM25([("a", "orders revenue region customers")])
    assert corpus.coverage("orders in may") == pytest.approx(0.5), (
        "the month carrying the question's constraint is not being counted"
    )


def test_document_length_counts_words_not_index_terms() -> None:
    """Review finding 11. I2's parts must not lengthen the document that carries them.

    ``_dl`` counted the expanded list, so four snake_case identifiers measured 13 tokens against 5
    for a plain-English summary of the same size. ``avgdl`` moved with it and BM25's ``_B`` term
    then taxed identifier-dense summaries — the documents I2 exists to make reachable.
    """
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
