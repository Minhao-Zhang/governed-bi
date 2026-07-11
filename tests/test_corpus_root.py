"""``resolve_corpus_root``: repo-root-relative resolution for the D13 corpus repo.

A relative ``GOVERNED_BI_CORPUS`` must resolve against the repo root, not the
process CWD, so the separate corpus repo is reachable as ``../BIRD-corpus`` from
any working directory.
"""

from governed_bi.config import _repo_root, resolve_corpus_root


def test_default_is_repo_root_corpus(monkeypatch):
    monkeypatch.delenv("GOVERNED_BI_CORPUS", raising=False)
    root = resolve_corpus_root()
    assert root.is_absolute()
    assert root == (_repo_root() / "corpus").resolve()


def test_relative_sibling_resolves_against_repo_root(monkeypatch):
    monkeypatch.delenv("GOVERNED_BI_CORPUS", raising=False)
    root = resolve_corpus_root("../BIRD-corpus")
    assert root.is_absolute()
    assert root.name == "BIRD-corpus"
    assert root.parent == _repo_root().parent  # sibling of the repo, not the CWD


def test_relative_is_cwd_independent(monkeypatch, tmp_path):
    monkeypatch.delenv("GOVERNED_BI_CORPUS", raising=False)
    monkeypatch.chdir(tmp_path)  # run from an unrelated directory
    assert resolve_corpus_root("../BIRD-corpus").parent == _repo_root().parent


def test_absolute_passthrough(tmp_path):
    target = tmp_path / "some-corpus"
    assert resolve_corpus_root(str(target)) == target.resolve()


def test_env_var_used_when_no_arg(monkeypatch):
    monkeypatch.setenv("GOVERNED_BI_CORPUS", "../BIRD-corpus")
    assert resolve_corpus_root().name == "BIRD-corpus"


def test_explicit_arg_beats_env(monkeypatch):
    monkeypatch.setenv("GOVERNED_BI_CORPUS", "corpus")
    assert resolve_corpus_root("../BIRD-corpus").name == "BIRD-corpus"
