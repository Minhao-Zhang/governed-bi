"""Read-only git history over the mounted corpus checkout (D15).

The corpus is its own git repository (D13) and every growth step is a commit (D14
checkpoints), so the semantic layer's history already exists as commits. This
module surfaces it for the read-only audit route (``GET /corpus/history`` +
``GET /corpus/history/{sha}``) by shelling out to ``git log`` / ``git show``.

Read-only throughout, and minimal-deps by design (subprocess ``git``, not a
Python git library, per D15's alternatives). Every call **degrades** rather than
raises: a missing ``git`` binary, a corpus root that is not a checkout, or an
unknown sha yields ``False`` / ``[]`` / ``None``, so the route can report an
empty history with ``can_history=false`` instead of erroring. Subprocess is
always invoked with an argument list (never ``shell=True``), the sha is regex-
validated, and path scoping is guarded against escapes.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

# Field/record separators embedded in the git pretty-format so subjects/authors
# containing spaces or newlines cannot be confused for delimiters. RS (\x1e)
# starts each commit record; US (\x1f) separates the header fields.
_RS = "\x1e"
_US = "\x1f"

# A bounded timeout so a wedged/huge repo can never hang a request; git reads are
# fast, and on expiry we degrade to the unavailable answer like any other failure.
_GIT_TIMEOUT = 15

# A git object name: hex, 4 (min abbrev) to 64 (sha-256) chars. Validated before
# it reaches the subprocess so a hostile value can neither inject an option nor
# name an arbitrary ref.
_SHA_RE = re.compile(r"[0-9a-fA-F]{4,64}")


def _run_git(root: Path | str, args: list[str]) -> "subprocess.CompletedProcess[str] | None":
    """Run ``git <args>`` in ``root``; return the completed process, or ``None``
    if git is missing / the cwd is gone / the call times out. Never raises."""
    try:
        return subprocess.run(
            ["git", *args],
            cwd=str(root),
            capture_output=True,
            # git output (messages, diffs, filenames) is UTF-8; decode it as such
            # rather than the platform locale (cp1252 on Windows would choke on a
            # German/Chinese renamed identifier in a diff). errors="replace" keeps
            # a stray byte from ever raising.
            encoding="utf-8",
            errors="replace",
            timeout=_GIT_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        # FileNotFoundError (no git / no cwd), TimeoutExpired, and any other
        # subprocess error all mean "history unavailable" -> degrade.
        return None


def is_git_repo(root: Path | str) -> bool:
    """True when ``root`` sits inside a git working tree (``git rev-parse``)."""
    result = _run_git(root, ["rev-parse", "--git-dir"])
    return result is not None and result.returncode == 0


@dataclass(frozen=True)
class Commit:
    """One commit from the corpus log (metadata + the paths it touched)."""

    sha: str
    author: str
    date: str
    subject: str
    changed_paths: list[str]


@dataclass(frozen=True)
class CommitDetail:
    """One commit's metadata plus its full unified diff."""

    sha: str
    author: str
    date: str
    subject: str
    diff: str


def read_history(
    root: Path | str,
    *,
    path: str | None = None,
    limit: int = 50,
    skip: int = 0,
) -> list[Commit]:
    """Return the corpus git log as :class:`Commit`\\ s, newest first.

    ``path`` scopes to one asset or db subtree via ``git log -- <path>`` (a per-db
    log is the D14 growth timeline; a per-asset log shows one asset's evolution).
    ``limit`` / ``skip`` page the log. Returns ``[]`` when ``root`` is not a git
    repo (or has no matching commits) rather than raising.
    """
    args = [
        "log",
        f"--max-count={limit}",
        f"--skip={skip}",
        "--date=iso-strict",
        f"--pretty=format:{_RS}%H{_US}%an{_US}%aI{_US}%s",
        "--name-only",
    ]
    if path:
        args += ["--", path]

    result = _run_git(root, args)
    if result is None or result.returncode != 0:
        return []

    commits: list[Commit] = []
    # Each commit record starts with RS; the leading split chunk (before the first
    # RS) is empty and skipped. Within a record the first line is the US-delimited
    # header; the remaining non-empty lines are the --name-only paths (the blank
    # line git inserts between header and file list drops out as empty).
    for chunk in result.stdout.split(_RS):
        if not chunk:
            continue
        lines = chunk.splitlines()
        header = lines[0].split(_US)
        if len(header) < 4:
            continue  # malformed record -> skip defensively
        sha, author, date, subject = header[0], header[1], header[2], header[3]
        changed_paths = [line for line in lines[1:] if line]
        commits.append(
            Commit(sha=sha, author=author, date=date, subject=subject, changed_paths=changed_paths)
        )
    return commits


def read_commit(root: Path | str, sha: str) -> CommitDetail | None:
    """Return one commit's metadata + unified diff, or ``None``.

    ``None`` when ``sha`` is not a valid object name (regex-guarded before it
    reaches git), when the commit is unknown, or when ``root`` is not a git repo.
    """
    if not _SHA_RE.fullmatch(sha):
        return None
    result = _run_git(
        root,
        ["show", "--no-color", "--date=iso-strict", f"--format=%H{_US}%an{_US}%aI{_US}%s", sha],
    )
    if result is None or result.returncode != 0:
        return None
    # The first line is the US-delimited header; everything after the first
    # newline is the diff, verbatim.
    head, _, diff = result.stdout.partition("\n")
    fields = head.split(_US)
    if len(fields) < 4:
        return None
    return CommitDetail(
        sha=fields[0], author=fields[1], date=fields[2], subject=fields[3], diff=diff
    )


def resolve_path(
    root: Path | str,
    *,
    db: str | None,
    asset_id: str | None,
) -> str | None:
    """Resolve the ``git log`` pathspec for a history scope, or ``None``.

    ``asset_id`` globs ``root`` (within ``<db>/`` when given) for the asset's YAML
    file and returns its POSIX path relative to ``root``; ``db`` alone returns the
    subtree name; neither returns ``None`` (an unscoped, full-repo log). Returns
    ``None`` when a given ``asset_id`` has no file. Path-escape guarded: ``db`` /
    ``asset_id`` carrying a separator or ``..`` are rejected, and the resolved file
    must live under ``root``.
    """
    root = Path(root)

    # Reject anything that could climb out of / redirect the glob: a path
    # separator or a parent-dir reference in either scoping component.
    for part in (db, asset_id):
        if part is not None and ("/" in part or "\\" in part or ".." in part):
            return None

    if asset_id:
        pattern = f"{db}/**/{asset_id}.yaml" if db else f"**/{asset_id}.yaml"
        for match in sorted(root.glob(pattern)):
            try:
                if not match.resolve().is_relative_to(root.resolve()):
                    continue  # symlink/escape guard: must resolve under root
            except OSError:
                continue
            return match.relative_to(root).as_posix()
        return None  # asset_id given but no file matched

    if db:
        return db
    return None
