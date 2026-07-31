"""N13: git branch / main hash / dirty working-tree state."""

from __future__ import annotations

import subprocess
from pathlib import Path

from governed_bi.provenance import (
    corpus_release_hash,
    git_head_branch,
    git_main_hash,
    working_tree_state,
)


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "test")
    (repo / "a.txt").write_text("one\n", encoding="utf-8")
    _git(repo, "add", "a.txt")
    _git(repo, "commit", "-m", "init")
    # Ensure a main ref exists for git_main_hash (default branch may be master).
    head = (repo / ".git" / "HEAD").read_text(encoding="utf-8").strip()
    if "refs/heads/main" not in head:
        _git(repo, "branch", "-M", "main")
    return repo


def test_clean_tree_reports_branch_and_no_diff(tmp_path: Path):
    repo = _init_repo(tmp_path)
    assert git_head_branch(repo_root=repo) == "main"
    sha = corpus_release_hash(repo_root=repo)
    assert len(sha) >= 40
    assert git_main_hash(repo_root=repo) == sha
    dirty, digest = working_tree_state(repo_root=repo)
    assert dirty is False
    assert digest is None


def test_dirty_tree_hashes_the_diff(tmp_path: Path):
    repo = _init_repo(tmp_path)
    (repo / "a.txt").write_text("two\n", encoding="utf-8")
    dirty, digest = working_tree_state(repo_root=repo)
    assert dirty is True
    assert digest is not None and len(digest) == 64
    # Stable across calls while the tree is unchanged.
    dirty2, digest2 = working_tree_state(repo_root=repo)
    assert (dirty2, digest2) == (dirty, digest)


def test_detached_head_has_no_branch_name(tmp_path: Path):
    repo = _init_repo(tmp_path)
    sha = corpus_release_hash(repo_root=repo)
    _git(repo, "checkout", "--detach", sha)
    assert git_head_branch(repo_root=repo) is None
    assert corpus_release_hash(repo_root=repo) == sha
