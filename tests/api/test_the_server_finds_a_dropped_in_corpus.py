"""``uv run langgraph dev`` with no environment, and the one case it must not guess.

**Rescued from ``tests/serve/test_chat_transport.py``** (2026-08-18), which was deleted with the
``POST /chat`` transport it was named for. This specification was the one thing in that file with
nothing to do with the transport: it is about ``api/graph_app``'s environment adapter, which every
surface still goes through. Deleting it with its neighbours would have retired the only assertion
on corpus discovery — so it moves here, to a file named for what it asserts.
"""

from __future__ import annotations

import pytest


def test_a_dropped_in_corpus_is_found_but_ambiguity_is_refused(tmp_path, monkeypatch) -> None:
    """A curated corpus is dropped into ``corpora/`` and the server should find it, because
    typing three env vars before a dev command is how a wrong corpus gets served by accident.
    But *two* directories is a question only the operator can settle: picking one would make
    ``corpus_content_hash`` — the field every quotability gate reads — depend on directory
    ordering. So one is an answer and two is an error naming both.
    """
    from governed_bi.api import graph_app

    assert graph_app._dropped_in_corpus(tmp_path) is None, "no corpora/ at all is not an error"

    base = tmp_path / graph_app.CORPORA_DIR
    (base / "_build").mkdir(parents=True)       # underscore dirs are build output, not corpora
    assert graph_app._dropped_in_corpus(tmp_path) is None

    (base / "gold-20260804").mkdir()
    assert graph_app._dropped_in_corpus(tmp_path) == str(base / "gold-20260804")

    (base / "curated_sme_20260730").mkdir()
    with pytest.raises(RuntimeError, match="holds 2 corpora"):
        graph_app._dropped_in_corpus(tmp_path)
