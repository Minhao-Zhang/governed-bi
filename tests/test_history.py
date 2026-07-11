"""Tests for read-only corpus git history (D15), fully hermetic.

Builds a throwaway git repo under ``tmp_path`` (identity + gpgsign pinned via
``-c`` flags so a developer's global config can't leak in) with a few commits
touching two db subtrees, then exercises both the ``corpus.history`` module and
the API routes over a ``ServeStack`` pointed at that repo. No network, no live
model (the session fixture strips ``OPENAI_API_KEY``), and nothing is written to
the engine repo — every git write happens inside ``tmp_path``.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from governed_bi.corpus.history import (
    is_git_repo,
    read_commit,
    read_history,
    resolve_path,
)

if shutil.which("git") is None:  # the feature and the fixtures both need git
    pytest.skip("git binary not available", allow_module_level=True)


# --------------------------------------------------------------------------- #
# hermetic repo fixture
# --------------------------------------------------------------------------- #


def _run(cwd: Path, *args: str) -> None:
    """Run a git command in ``cwd`` for test setup, raising on failure."""
    subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True)


def _commit(root: Path, subject: str) -> None:
    """Stage everything and commit with a pinned identity (no signing)."""
    _run(root, "add", "-A")
    _run(
        root,
        "-c",
        "user.name=Test SME",
        "-c",
        "user.email=sme@example.com",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "-q",
        "-m",
        subject,
    )


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _make_repo(base: Path) -> Path:
    """A corpus-shaped git repo with 3 commits over two db subtrees.

    History, newest first: [seed pizza_shop orders, refine customers, seed
    customers]. beer_factory is touched by 2 commits, pizza_shop by 1.
    """
    root = base
    root.mkdir(parents=True, exist_ok=True)
    _run(root, "init", "-q")

    customers = root / "beer_factory" / "tables" / "customers.yaml"
    _write(customers, "asset_type: table\nid: tbl_beer_factory_customers\n")
    _commit(root, "seed beer_factory customers")

    _write(customers, "asset_type: table\nid: tbl_beer_factory_customers\ndescription: fixed grain\n")
    _commit(root, "SME: refine customers grain")

    _write(root / "pizza_shop" / "tables" / "orders.yaml", "asset_type: table\nid: tbl_pizza_shop_orders\n")
    _commit(root, "seed pizza_shop orders")
    return root


# --------------------------------------------------------------------------- #
# module: is_git_repo / read_history / read_commit / resolve_path
# --------------------------------------------------------------------------- #


def test_is_git_repo_true_and_false(tmp_path):
    repo = _make_repo(tmp_path / "repo")
    assert is_git_repo(repo) is True
    plain = tmp_path / "plain"
    plain.mkdir()
    assert is_git_repo(plain) is False


def test_read_history_all_commits_newest_first(tmp_path):
    repo = _make_repo(tmp_path / "repo")
    commits = read_history(repo)
    assert [c.subject for c in commits] == [
        "seed pizza_shop orders",
        "SME: refine customers grain",
        "seed beer_factory customers",
    ]
    head = commits[0]
    assert head.author == "Test SME"
    assert head.date and head.sha  # populated header fields
    assert any("orders.yaml" in p for p in head.changed_paths)


def test_read_history_path_scoped(tmp_path):
    repo = _make_repo(tmp_path / "repo")
    beer = read_history(repo, path="beer_factory")
    assert len(beer) == 2
    assert all(any("beer_factory" in p for p in c.changed_paths) for c in beer)

    pizza = read_history(repo, path="pizza_shop")
    assert len(pizza) == 1
    assert pizza[0].subject == "seed pizza_shop orders"


def test_read_commit_handles_non_ascii_diff(tmp_path):
    """Diffs carry UTF-8 (renamed identifiers, e.g. German/Chinese); git output
    must be decoded as UTF-8, not the platform locale. Regression: on Windows the
    default cp1252 decode raised UnicodeDecodeError and dropped the diff."""
    repo = tmp_path / "utf8repo"
    repo.mkdir()
    _run(repo, "init", "-q")
    _write(
        repo / "beer_factory" / "tables" / "kunden.yaml",
        "physical_name: kunden\ncolumns:\n- physical_name: kunde_id\n  note: Wurzelbier äöü 根啤酒\n",
    )
    _commit(repo, "seed kunden (Umlaut äöü + 中文)")

    commits = read_history(repo)
    assert commits and "中文" in commits[0].subject
    detail = read_commit(repo, commits[0].sha)
    assert detail is not None
    assert "kunde_id" in detail.diff
    assert "根啤酒" in detail.diff  # non-ASCII survives the round-trip


def test_read_history_limit_and_skip(tmp_path):
    repo = _make_repo(tmp_path / "repo")
    first = read_history(repo, limit=1)
    assert len(first) == 1
    assert first[0].subject == "seed pizza_shop orders"

    second = read_history(repo, limit=1, skip=1)
    assert len(second) == 1
    assert second[0].subject == "SME: refine customers grain"


def test_read_commit_returns_diff(tmp_path):
    repo = _make_repo(tmp_path / "repo")
    head = read_history(repo, limit=1)[0]
    detail = read_commit(repo, head.sha)
    assert detail is not None
    assert detail.sha == head.sha
    assert detail.subject == "seed pizza_shop orders"
    assert detail.author == "Test SME"
    assert "orders.yaml" in detail.diff  # a real unified diff of the commit


def test_read_commit_bogus_and_malformed_sha(tmp_path):
    repo = _make_repo(tmp_path / "repo")
    assert read_commit(repo, "deadbeef") is None  # well-formed hex, unknown object
    assert read_commit(repo, "zzzz") is None  # fails the hex regex
    assert read_commit(repo, "") is None  # too short for the regex


def test_resolve_path_scopes_and_guards(tmp_path):
    repo = _make_repo(tmp_path / "repo")
    assert resolve_path(repo, db="beer_factory", asset_id=None) == "beer_factory"
    assert resolve_path(repo, db=None, asset_id="customers") == "beer_factory/tables/customers.yaml"
    assert resolve_path(repo, db="beer_factory", asset_id="customers") == "beer_factory/tables/customers.yaml"
    assert resolve_path(repo, db=None, asset_id=None) is None
    assert resolve_path(repo, db=None, asset_id="does_not_exist") is None
    # path-escape guards: separators / parent refs never reach the glob
    assert resolve_path(repo, db="..", asset_id=None) is None
    assert resolve_path(repo, db=None, asset_id="../secret") is None
    assert resolve_path(repo, db=None, asset_id="tables/customers") is None


def test_non_git_dir_degrades(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "x.yaml").write_text("hi\n", encoding="utf-8")
    assert is_git_repo(plain) is False
    assert read_history(plain) == []
    assert read_commit(plain, "abc123") is None


# --------------------------------------------------------------------------- #
# API routes over a ServeStack pointed at the temp repo
# --------------------------------------------------------------------------- #

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from governed_bi.api import create_app  # noqa: E402
from governed_bi.api.stack import build_stack  # noqa: E402


def _git_client(repo: Path, db: str = "beer_factory") -> TestClient:
    # Reuse the real (offline) stack but repoint corpus_root at the temp git repo
    # and recompute can_history from it; history routes only touch corpus_root.
    stack = replace(build_stack(), corpus_root=repo, db=db, can_history=is_git_repo(repo))
    return TestClient(create_app(stack))


def test_capabilities_reports_can_history_true(tmp_path):
    client = _git_client(_make_repo(tmp_path / "repo"))
    assert client.get("/capabilities").json()["can_history"] is True


def test_history_route_returns_commits(tmp_path):
    client = _git_client(_make_repo(tmp_path / "repo"))
    body = client.get("/corpus/history").json()
    commits = body["commits"]
    assert len(commits) == 3
    assert commits[0]["subject"] == "seed pizza_shop orders"
    assert {"sha", "author", "date", "subject", "changed_paths"} <= commits[0].keys()


def test_history_route_scopes_by_db_and_asset(tmp_path):
    client = _git_client(_make_repo(tmp_path / "repo"))
    beer = client.get("/corpus/history", params={"db": "beer_factory"}).json()["commits"]
    assert len(beer) == 2
    asset = client.get("/corpus/history", params={"asset_id": "customers"}).json()["commits"]
    assert len(asset) == 2
    # a given-but-unknown asset_id yields empty history, not a 404
    unknown = client.get("/corpus/history", params={"asset_id": "nope"})
    assert unknown.status_code == 200
    assert unknown.json()["commits"] == []


def test_history_detail_route_returns_diff(tmp_path):
    client = _git_client(_make_repo(tmp_path / "repo"))
    sha = client.get("/corpus/history").json()["commits"][0]["sha"]
    detail = client.get(f"/corpus/history/{sha}").json()
    assert detail["sha"] == sha
    assert detail["subject"] == "seed pizza_shop orders"
    assert "orders.yaml" in detail["diff"]


def test_history_detail_bogus_sha_404(tmp_path):
    client = _git_client(_make_repo(tmp_path / "repo"))
    assert client.get("/corpus/history/deadbeef").status_code == 404  # unknown object
    assert client.get("/corpus/history/zzzz").status_code == 404  # malformed


def test_non_git_stack_reports_no_history(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    stack = replace(build_stack(), corpus_root=plain, can_history=is_git_repo(plain))
    client = TestClient(create_app(stack))
    assert client.get("/capabilities").json()["can_history"] is False
    assert client.get("/corpus/history").json()["commits"] == []
    # detail 404s when history is unavailable at all
    assert client.get("/corpus/history/abc123").status_code == 404
