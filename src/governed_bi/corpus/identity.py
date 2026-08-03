"""Ids and paths: the two places a corpus name becomes a filesystem path.

**Why this is its own module rather than a few helpers inside the store.** v1's
``asset.schema`` escaped the corpus root: the write directory is derived from that
field while the only validator in the area guarded the asset *id*. Two names, one
of them unguarded, and the guard was in a module that had no reason to be looking
at directories. So the rule this module states is **validate a path component
where it is used as a path component**, and both functions are here so a reader
looking for "what may become a directory name" finds one answer.

The subtlety that made v1's version wrong even where it existed: **``\\A...\\Z``, not
``^...$``.** Python's ``$`` also matches just before a trailing newline, so
``"beer_factory\\n"`` passes a ``^[A-Za-z0-9_]+$`` validator that names a
directory. :func:`validate_path_component` is tested against exactly that string.

Column ids are **derived, never authored** (ADR 0005 §1.2): columns are stored
inline under their table, so a column id in YAML would be a second spelling of a
fact the file's position already carries -- and two spellings of one fact is this
project's most expensive shape.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable, Sequence

__all__ = [
    "UnsafeName",
    "SHARED_NAMESPACE",
    "validate_path_component",
    "validate_asset_id",
    "derive_column_id",
    "namespace_of",
    "corpus_files",
]


class UnsafeName(ValueError):
    """A name that must not reach the filesystem.

    A ``ValueError``, so a loader's ``except Exception`` catches it as a per-item
    problem -- but raised rather than returned, because on the *write* path this is
    a security control and not a degradation (``ports.CorpusStore``).
    """


#: Directory for assets whose type declares no ``schema`` field.
#:
#: ``join``, ``metric`` and ``term`` have none: a join's namespace is its left
#: endpoint's, a metric's is its base table's, a term's is its binding target's --
#: all facts held elsewhere. ADR 0005 does not say where such a file lives, so
#: :func:`~governed_bi.corpus.store.write` refuses to guess and requires an
#: explicit namespace; this constant is the name a caller passes when the asset is
#: genuinely system-wide.
SHARED_NAMESPACE = "_shared"

#: A directory component: a bare identifier. It names a corpus subtree **and** a
#: live SQL namespace, so separators, dots and ``..`` are all rejected.
_COMPONENT_RE = re.compile(r"\A[A-Za-z0-9_]+\Z")

#: An asset id. Dots are permitted because the ids in use are dotted
#: (``beer_factory.customers.email``); separators, spaces and control characters
#: are not, because an id becomes a filename.
#:
#: Deliberately **not** a per-type shape check. ADR 0005 gives a format for exactly
#: one type (``join_{schema}_{left}_{right}_{digest}``, §1.2) while the ids in the
#: acceptance contract are dotted, and a regex enforcing either convention would
#: reject the other. This is the security property only.
_ID_RE = re.compile(r"\A[A-Za-z0-9_][A-Za-z0-9_.:-]*\Z")


def validate_path_component(value: object, *, what: str) -> str:
    """``value`` as a safe single path component, or raise :class:`UnsafeName`.

    ``what`` names the field, so the failure says which of an asset's several
    string fields was unsafe rather than making a reader guess.
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


def derive_column_id(table_id: str, physical_name: str) -> str:
    """The id of a column stored inline under ``table_id``.

    One function, called by the loader and by the seed, because a column id
    computed independently in two places is two answers to what identifies a
    column -- and the retrieval index, ``resolve``'s closure and the budget all
    key on it.
    """
    return f"{table_id}.{physical_name}"


def namespace_of(root: Path, path: Path) -> str:
    """The schema subtree ``path`` sits in, relative to ``root``.

    ``""`` for a file directly under ``root``. Used to apply the manifest, so that
    a schema dropped from one attempt cannot leave its YAML behind and go on
    competing as a router candidate for every other schema's questions (ADR 0005
    §2.2).
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

    Sorted by relative path so that two runs over the same tree read it in the same
    order -- the loader's output order and the content hash both depend on it.

    ``schemas=None`` means **the tree is the manifest**. That is correct for a
    single-schema tree and for a test; it is the wrong call for a pooled corpus
    root, which is why the parameter exists and why
    :func:`~governed_bi.corpus.store.load` reports a manifest entry with no
    directory as a problem.

    Given a manifest, a file **not** under one of the named subtrees (or under
    ``_shared``) is not read at all -- including a file sitting loose at the root.
    That is the point: not in the manifest means not loaded, so a schema dropped
    from one attempt cannot leave anything behind.
    """
    if not root.is_dir():
        return []
    allowed = None if schemas is None else {*schemas, SHARED_NAMESPACE}
    wanted = None if suffixes is None else {s.lower() for s in suffixes}
    out = [
        path
        for path in root.rglob("*")
        if path.is_file()
        and (wanted is None or path.suffix.lower() in wanted)
        and (allowed is None or namespace_of(root, path) in allowed)
    ]
    return sorted(out, key=lambda p: p.relative_to(root).as_posix())
