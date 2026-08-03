"""Shared fixtures for govern tests after ``check()`` took ``AnalystCorpus``.

Acceptance contracts still call ``check(..., allowed_columns=...)`` in places.
This fixture translates that spelling into an :class:`AnalystCorpus` so the
criterion files stay readable while the production signature stays ADR-aligned.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def check():
    from governed_bi.corpus.analyst import analyst_corpus_from_keys
    from governed_bi.govern.check import check as real_check

    def _check(
        sql: str,
        *,
        licensed,
        corpus=None,
        allowed_columns=None,
        excluded_columns=(),
        suspect_columns=(),
        **kwargs,
    ):
        if corpus is None:
            allowed = () if allowed_columns is None else allowed_columns
            corpus = analyst_corpus_from_keys(
                allowed=allowed,
                excluded=excluded_columns,
                suspect=suspect_columns,
            )
        return real_check(sql, licensed=licensed, corpus=corpus, **kwargs)

    return _check


@pytest.fixture
def prepare():
    from governed_bi.corpus.analyst import analyst_corpus_from_keys
    from governed_bi.govern.pipeline import prepare as real_prepare

    def _prepare(
        sql: str,
        *,
        licensed,
        corpus=None,
        allowed_columns=None,
        excluded_columns=(),
        suspect_columns=(),
        **kwargs,
    ):
        if corpus is None:
            allowed = () if allowed_columns is None else allowed_columns
            corpus = analyst_corpus_from_keys(
                allowed=allowed,
                excluded=excluded_columns,
                suspect=suspect_columns,
            )
        return real_prepare(sql, licensed=licensed, corpus=corpus, **kwargs)

    return _prepare
