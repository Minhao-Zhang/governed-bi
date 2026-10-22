"""Ids and paths. A path component is checked where it becomes a path.

Column ids are derived, never authored. See ADR 0005.
"""


from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Iterable, Sequence

import sqlglot
from sqlglot import expressions as exp
from sqlglot.errors import SqlglotError

__all__ = [
    "UnsafeName",
    "SHARED_NAMESPACE",
    "validate_path_component",
    "validate_asset_id",
    "slug",
    "table_id",
    "derive_column_id",
    "on_digest",
    "join_id",
    "namespace_of",
    "corpus_files",
]


class UnsafeName(ValueError):
    """A name that must not reach the filesystem. A ``ValueError``, so a loader records it per item."""


#: Directory for join, metric, and term assets, which have no schema field.
#: ``write`` will not guess one; pass this when the asset is system-wide.
SHARED_NAMESPACE = "_shared"

#: Tool directories, never corpus content. See ``_is_tooling``.
_NON_CORPUS_DIRS = frozenset(
    {".git", ".hg", ".svn", "__pycache__", ".ipynb_checkpoints", ".github", ".conformance"}
)

#: One path component: a bare identifier, also a SQL namespace.
_COMPONENT_RE = re.compile(r"\A[A-Za-z0-9_]+\Z")

#: An asset id that can be a filename. Dots are allowed; ``..`` is rejected in code.
#: Not a per-type shape: join ids and dotted ids would reject each other. See ADR 0005.
_ID_RE = re.compile(r"\A[A-Za-z0-9_][A-Za-z0-9_.:-]*\Z")


def validate_path_component(value: object, *, what: str) -> str:
    if not isinstance(value, str) or not _COMPONENT_RE.match(value):
        raise UnsafeName(
            f"{what}={value!r} is not a bare identifier ([A-Za-z0-9_]+). It names a "
            "corpus directory and a SQL namespace, so path separators, dots, '..' "
            "and trailing whitespace are rejected."
        )
    return value


def validate_asset_id(value: object) -> str:
    if not isinstance(value, str) or not _ID_RE.match(value) or ".." in value:
        raise UnsafeName(
            f"id={value!r} is not a safe asset id. An id becomes a filename, so it "
            "must match [A-Za-z0-9_][A-Za-z0-9_.:-]* and contain no '..'."
        )
    return value


def slug(physical_name: str) -> str:
    """Id component for an engine identifier. A key is not the name. See ADR 0008.

    A bare identifier is kept. Anything else is sanitised plus a six-hex digest of the exact name, so ``a b`` and
    ``a_b`` do not collide. Not lowercased: quoted identifiers differ by case.
    """
    if not isinstance(physical_name, str) or not physical_name:
        raise UnsafeName(f"physical_name={physical_name!r} is not a non-empty string")
    if _COMPONENT_RE.match(physical_name):
        return physical_name
    sanitised = "".join(ch if ch.isascii() and (ch.isalnum() or ch == "_") else "_" for ch in physical_name)
    digest = hashlib.sha256(physical_name.encode("utf-8")).hexdigest()[:6]
    return f"{sanitised}_{digest}"


def table_id(schema: str, physical_name: str) -> str:
    """``schema`` plus :func:`slug`. One spelling for the seed, joins, and lookups. See ADR 0005."""
    return f"{schema}.{slug(physical_name)}"


def derive_column_id(table_id: str, physical_name: str) -> str:
    """Column id under ``table_id``. One spelling for the loader and the seed."""
    return f"{table_id}.{slug(physical_name)}"


def _qualified_operand(node: exp.Expression) -> str:
    return node.sql(dialect="postgres").casefold()


def on_digest(on: str) -> str:
    """Digest of the relationship, not the ON text. Operand order, conjunct order, case, and whitespace do not count.
    See ADR 0005."""
    if not isinstance(on, str) or not on.strip():
        raise ValueError("on_digest requires a non-empty ON clause")
    try:
        tree = sqlglot.parse_one(
            f"SELECT 1 FROM _left AS _l JOIN _right AS _r ON {on}",
            dialect="postgres",
        )
    except SqlglotError as err:
        raise ValueError(f"on_digest could not parse ON clause: {err}") from err
    join = tree.find(exp.Join)
    if join is None or join.args.get("on") is None:
        raise ValueError("on_digest could not find an ON expression")
    predicates = frozenset(
        frozenset({_qualified_operand(eq.left), _qualified_operand(eq.right)})
        for eq in join.args["on"].find_all(exp.EQ)
    )
    if not predicates:
        raise ValueError("on_digest found no equality predicates in the ON clause")
    canonical = repr(sorted(tuple(sorted(pred)) for pred in predicates))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def join_id(schema: str, left: str, right: str, on: str) -> str:
    left_name = left.rsplit(".", 1)[-1]
    right_name = right.rsplit(".", 1)[-1]
    return f"join_{schema}_{left_name}_{right_name}_{on_digest(on)[:8]}"


def namespace_of(root: Path, path: Path) -> str:
    """First directory under ``root``, or empty at the root. See ADR 0005."""
    parts = path.relative_to(root).parts
    return parts[0] if len(parts) > 1 else ""


def corpus_files(
    root: Path,
    *,
    schemas: Sequence[str] | None = None,
    suffixes: Iterable[str] | None = None,
) -> list[Path]:
    """Corpus files under ``root``, sorted by relative path.

    ``schemas=None`` treats the whole tree as the manifest. A named manifest also keeps ``_shared``.
    """
    if not root.is_dir():
        return []
    allowed = None if schemas is None else {*schemas, SHARED_NAMESPACE}
    wanted = None if suffixes is None else {s.lower() for s in suffixes}
    out = [
        path
        for path in root.rglob("*")
        if path.is_file()
        and not _is_tooling(root, path)
        and (wanted is None or path.suffix.lower() in wanted)
        and (allowed is None or namespace_of(root, path) in allowed)
    ]
    return sorted(out, key=lambda p: p.relative_to(root).as_posix())


def _is_tooling(root: Path, path: Path) -> bool:
    """True when any path component is a tool directory.

    Filenames are kept, so prose beside the assets still counts. ``.git`` is matched as a component because a worktree
    stores it as a file, and that file embeds an absolute path.
    """
    parts = path.relative_to(root).parts
    return not _NON_CORPUS_DIRS.isdisjoint(parts)
