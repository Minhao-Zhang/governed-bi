r"""Ids and paths: where a corpus name becomes a filesystem path.

Validate path components where used as paths (``\A``/``\Z``). Column ids are
derived, never authored (ADR 0005 §1.2).
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
    """A name that must not reach the filesystem.

    A ``ValueError`` so a loader's ``except Exception`` catches it as a per-item problem,
    but raised rather than returned: on the write path this is a security control, not a
    degradation (``ports.CorpusStore``).
    """


#: Directory for assets whose type declares no ``schema`` field (``join``, ``metric``,
#: ``term`` — each one's namespace is a fact held by another asset). ADR 0005 does not say
#: where such a file lives, so :func:`~governed_bi.corpus.store.write` requires an explicit
#: namespace; this is what a caller passes when the asset really is system-wide.
SHARED_NAMESPACE = "_shared"

#: Directory names that are a tool's bookkeeping, never corpus content — see :func:`_is_tooling`.
_NON_CORPUS_DIRS = frozenset({".git", ".hg", ".svn", "__pycache__", ".ipynb_checkpoints"})

#: A directory component: a bare identifier. It names a corpus subtree **and** a
#: live SQL namespace, so separators, dots and ``..`` are all rejected.
_COMPONENT_RE = re.compile(r"\A[A-Za-z0-9_]+\Z")

#: An asset id. Dots are permitted (``beer_factory.customers.email``); separators, spaces
#: and control characters are not, because an id becomes a filename.
#:
#: The security property only, deliberately **not** a per-type shape check: ADR 0005 §1.2
#: gives a format for joins (``join_{schema}_{left}_{right}_{digest}``) while other ids are
#: dotted, so a regex enforcing either convention would reject the other.
_ID_RE = re.compile(r"\A[A-Za-z0-9_][A-Za-z0-9_.:-]*\Z")


def validate_path_component(value: object, *, what: str) -> str:
    """``value`` as a safe single path component, or raise :class:`UnsafeName`.

    ``what`` names the field, so the failure says which of an asset's string fields it was.
    """
    if not isinstance(value, str) or not _COMPONENT_RE.match(value):
        raise UnsafeName(
            f"{what}={value!r} is not a bare identifier ([A-Za-z0-9_]+). It names a "
            "corpus directory and a SQL namespace, so path separators, dots, '..' "
            "and trailing whitespace are rejected."
        )
    return value


def validate_asset_id(value: object) -> str:
    """``value`` as a safe asset id, or raise :class:`UnsafeName`."""
    if not isinstance(value, str) or not _ID_RE.match(value) or ".." in value:
        raise UnsafeName(
            f"id={value!r} is not a safe asset id. An id becomes a filename, so it "
            "must match [A-Za-z0-9_][A-Za-z0-9_.:-]* and contain no '..'."
        )
    return value


def slug(physical_name: str) -> str:
    """The id component for an engine identifier. **A key is not a name** (ADR 0008 D1).

    ``physical_name`` holds the engine's identifier verbatim and is the only string that may
    reach Postgres; this is the derived component that names a file and keys an index.
    Deriving the key from the raw name instead left ``airline."Air Carriers"`` with no
    ``TableAsset`` at all while 24 few-shots cited it, and widening the charset is not the
    fix because ``"Air Carriers".yaml`` is illegal on Windows.

    Returns ``physical_name`` unchanged when it is already a bare identifier (655 of 655
    tables and 5,942 of 5,942 columns in the corpus, so nothing existing moves). Otherwise
    unsafe characters become ``_`` and a six-hex digest of the *exact* name is appended:

        ``CBSA``          → ``CBSA``
        ``Air Carriers``  → ``Air_Carriers_66c534``
        ``orange_trophée``→ ``orange_troph_e_1fadf1``

    The digest is what makes it injective: ``a b`` and ``a_b`` sanitise alike and must not
    collide. Deliberately **not** lowercased — Postgres distinguishes quoted ``"CBSA"`` from
    ``"cbsa"``, and a corpus that cannot describe that cannot describe the lake.
    """
    if not isinstance(physical_name, str) or not physical_name:
        raise UnsafeName(f"physical_name={physical_name!r} is not a non-empty string")
    if _COMPONENT_RE.match(physical_name):
        return physical_name
    sanitised = "".join(ch if ch.isascii() and (ch.isalnum() or ch == "_") else "_" for ch in physical_name)
    digest = hashlib.sha256(physical_name.encode("utf-8")).hexdigest()[:6]
    return f"{sanitised}_{digest}"


def table_id(schema: str, physical_name: str) -> str:
    """Table id in ``schema`` (ADR 0005 §2.8.2, ADR 0008 D1).

    Single spelling shared by seed, join reconciliation, and licensed lookups.
    """
    return f"{schema}.{slug(physical_name)}"


def derive_column_id(table_id: str, physical_name: str) -> str:
    """The id of a column stored inline under ``table_id``.

    One function for the loader and the seed both: the retrieval index, ``resolve``'s
    closure and the budget all key on this string, so two spellings is two columns.
    """
    return f"{table_id}.{slug(physical_name)}"


def _qualified_operand(node: exp.Expression) -> str:
    """Lowercased SQL spelling of one side of an equality. Order-insensitive later."""
    return node.sql(dialect="postgres").casefold()


def on_digest(on: str) -> str:
    """Canonical digest of a join ON clause (ADR 0005 §1.2).

    Equality operands are unordered within a predicate; conjuncts are unordered
    within the clause; case and whitespace are ignored. The digest identifies the
    **relationship**, not the text — without it, two edges between the same table
    pair collapse and the last write wins.
    """
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
    """``join_{schema}_{left}_{right}_{digest[:8]}`` — one id per relationship."""
    left_name = left.rsplit(".", 1)[-1]
    right_name = right.rsplit(".", 1)[-1]
    return f"join_{schema}_{left_name}_{right_name}_{on_digest(on)[:8]}"


def namespace_of(root: Path, path: Path) -> str:
    """The schema subtree ``path`` sits in, relative to ``root``; ``""`` at the root.

    Used to apply the manifest, so a schema dropped from one attempt cannot leave its YAML
    behind and go on competing as a router candidate (ADR 0005 §2.2).
    """
    parts = path.relative_to(root).parts
    return parts[0] if len(parts) > 1 else ""


def corpus_files(
    root: Path,
    *,
    schemas: Sequence[str] | None = None,
    suffixes: Iterable[str] | None = None,
) -> list[Path]:
    """Every corpus file under ``root``, sorted, filtered by manifest and suffix.

    Sorted by relative path: the loader's output order and the content hash both depend on
    two runs over the same tree reading it identically.

    ``schemas=None`` means **the tree is the manifest** — right for a single-schema tree,
    wrong for a pooled root. Given a manifest, a file not under a named subtree (or under
    ``_shared``) is not read at all, including one loose at the root, so a schema dropped
    from an attempt cannot leave anything behind.
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
    """Is this a tool's bookkeeping rather than corpus content?

    Matters only once a corpus is version-controlled: with ``schemas=None`` the tree is the
    manifest, so without this ``corpus_content_hash`` digests ``.git/`` and the treatment
    identity changes on every commit, fetch and ``git gc``.

    Deliberately narrow — only VCS and interpreter bookkeeping. A ``README.md`` at the corpus
    root is *not* excluded: ``corpus_content_hash`` counts everything in the selected
    subtrees, and a caller wanting assets alone passes ``schemas``.

    **The last component is checked too, because ``.git`` is not always a directory.** In a
    ``git worktree`` it is a *file* holding a pointer, and that pointer embeds an absolute
    path — so digesting it gave the same commit a different identity in every worktree, and a
    different one again in a clone. Measured: three checkouts of one commit, three hashes.
    """
    parts = path.relative_to(root).parts
    return not _NON_CORPUS_DIRS.isdisjoint(parts)
