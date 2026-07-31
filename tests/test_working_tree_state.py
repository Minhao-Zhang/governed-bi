"""N13: git branch / main hash / dirty working-tree state.

``main_git_sha`` is the field these tests exist for. Branch HEAD alone does not
locate a run's code: on the experiment server the internal-proxy code lives on a
branch that is never equal to ``main``, so a manifest recording only the branch
tip leaves no way to say which ``main`` commit the run was based on. Branch name
plus ``main``'s SHA together are the trace, which is why the pair is asserted
here in both directions — distinct off ``main``, equal on it.

Every null path returns a value instead of raising: this is read at manifest
time on a paid run, and a fresh clone of a fork, a detached HEAD, or a default
branch named something other than ``main`` must all cost a null field, not the
run.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from governed_bi.eval import metrics
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


def test_off_main_resolves_head_and_main_to_different_shas(tmp_path: Path):
    """The case the field exists for: HEAD is never equal to ``main``.

    ``git_sha`` alone cannot answer "based on which ``main``?" here — only the
    two together can, so they must resolve independently rather than one being
    read off the other.
    """
    repo = _init_repo(tmp_path)
    main_sha = corpus_release_hash(repo_root=repo)

    _git(repo, "checkout", "-b", "impl/proxy")
    (repo / "a.txt").write_text("branch work\n", encoding="utf-8")
    _git(repo, "add", "a.txt")
    _git(repo, "commit", "-m", "branch work")

    assert git_head_branch(repo_root=repo) == "impl/proxy"
    head_sha = corpus_release_hash(repo_root=repo)
    assert len(head_sha) >= 40
    assert git_main_hash(repo_root=repo) == main_sha
    assert head_sha != main_sha


def test_on_main_resolves_head_and_main_to_the_same_sha(tmp_path: Path):
    repo = _init_repo(tmp_path)
    (repo / "a.txt").write_text("second\n", encoding="utf-8")
    _git(repo, "add", "a.txt")
    _git(repo, "commit", "-m", "second")

    assert git_head_branch(repo_root=repo) == "main"
    assert git_main_hash(repo_root=repo) == corpus_release_hash(repo_root=repo)


def test_packed_main_ref_still_resolves(tmp_path: Path):
    """``refs/heads/main`` is loose only until something packs it.

    On any long-lived clone ``git gc`` moves it into ``packed-refs``; reading
    the loose file alone would silently start returning the null value.
    """
    repo = _init_repo(tmp_path)
    expected = corpus_release_hash(repo_root=repo)
    _git(repo, "pack-refs", "--all")
    assert not (repo / ".git" / "refs" / "heads" / "main").exists()
    assert git_main_hash(repo_root=repo) == expected


def test_missing_main_ref_is_null_not_an_exception(tmp_path: Path):
    """Fresh clone of a fork, or a default branch named something else.

    Returns ``"unknown"`` rather than ``None``: that is the sentinel
    ``corpus_release_hash`` already uses for the sibling ``git_sha`` field, and
    a run must not die at manifest time over absent git metadata.
    """
    repo = _init_repo(tmp_path)
    _git(repo, "branch", "-M", "trunk")
    assert git_head_branch(repo_root=repo) == "trunk"
    assert git_main_hash(repo_root=repo) == "unknown"
    # Corrupt/absent .git entirely — still a value, still no raise.
    assert git_main_hash(repo_root=tmp_path / "not-a-repo") == "unknown"


def test_built_manifest_carries_main_git_sha_as_operational(tmp_path: Path):
    """The field has to reach ``manifest.json``, and as an OPERATIONAL field.

    In ``MANIFEST_KNOBS`` it would join ``COMPARABILITY_KEYS`` and make two runs
    incomparable merely for sitting on different branches — which is the exact
    thing the branch/main pair was added to make legible.
    """
    operational = {m.name for m in metrics.MANIFEST_OPERATIONAL}
    knobs = {m.name for m in metrics.MANIFEST_KNOBS}
    assert "main_git_sha" in operational
    assert "main_git_sha" not in knobs

    manifest = metrics.build_manifest(
        mode="datalake",
        bird_dir="/data/bird",
        split="test",
        model_name="gpt-5.6-luna",
        prompt_variants={},
        created_at_utc="20260731T000000Z",
        route_top_k=3,
        route_llm_pick=False,
        schema_pick_max_columns=12,
        use_embedder=True,
        llm_temperature=None,
        question_pool_hash="pool0000",
        always_note_global_max=8,
        always_note_char_max=2000,
        pin_triggers_enabled=False,
        pin_require_certified=None,
        pin_max=None,
        arms=("baseline",),
        oracles=(),
        replicate_of=None,
        db_ids=None,
        limit=None,
        limit_dbs=None,
        question_scope_hash="abc123",
    )
    # Presence, not value: the value depends on the checkout the suite runs in
    # (a worktree has no local `main`), and an absent key is the failure mode
    # the ledger cannot detect.
    assert "main_git_sha" in manifest
    assert "git_branch" in manifest
    assert isinstance(manifest["main_git_sha"], str)
