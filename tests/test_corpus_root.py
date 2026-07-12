"""``resolve_corpus_root``: repo-root-relative resolution for the D13 corpus repo.

A relative corpus path must resolve against the repo root, not the process CWD,
so a sibling checkout is reachable as ``../BIRD-corpus`` from any working
directory. The configured value lives in ``[paths].corpus_root`` (TOML).
"""

import os
from pathlib import Path

from governed_bi.config import _repo_root, load_settings, resolve_corpus_root


def test_default_is_repo_root_corpus():
    root = resolve_corpus_root()
    assert root.is_absolute()
    assert root == Path(os.path.normpath(_repo_root() / "corpus"))


def test_relative_sibling_resolves_against_repo_root():
    root = resolve_corpus_root("../BIRD-corpus")
    assert root.is_absolute()
    assert root.name == "BIRD-corpus"
    assert root.parent == _repo_root().parent  # sibling of the repo, not the CWD


def test_relative_is_cwd_independent(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # run from an unrelated directory
    assert resolve_corpus_root("../BIRD-corpus").parent == _repo_root().parent


def test_absolute_passthrough(tmp_path):
    target = tmp_path / "some-corpus"
    assert resolve_corpus_root(str(target)) == Path(os.path.normpath(target))


def test_settings_corpus_root_is_resolved():
    settings = load_settings(apply_local=False)
    root = resolve_corpus_root(settings.corpus_root)
    assert root.name == "corpus"
    assert root.is_absolute()
