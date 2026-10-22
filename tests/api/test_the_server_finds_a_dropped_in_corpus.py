"""Corpus discovery when no corpus env var is set. Two directories must not be chosen for the operator."""

from __future__ import annotations

import pytest


def test_a_dropped_in_corpus_is_found_but_ambiguity_is_refused(tmp_path, monkeypatch) -> None:
    """Picking one of two directories would make ``corpus_content_hash`` depend on directory order."""
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
