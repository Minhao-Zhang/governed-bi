"""Path validation, folding, and two key shapes. See ADR 0006 and ADR 0008. Anchors are ``\\A``/``\\Z``. Keys are
slugged asset ids, so a caller set and a statement reference share one normaliser."""

from __future__ import annotations

import re
from typing import Iterable, Mapping

from ..corpus.identity import slug

__all__ = [
    "SCHEMA_ID_PATTERN",
    "MAX_IDENTIFIER_BYTES",
    "is_valid_schema_id",
    "fold",
    "table_key",
    "column_key",
    "normalise_table_key",
    "normalise_column_key",
    "fold_map",
]

#: ``\A``/``\Z`` only. A second copy is how one identifier was checked and another was not.
SCHEMA_ID_PATTERN = re.compile(r"\A[A-Za-z0-9_][A-Za-z0-9_-]*\Z")

#: Postgres truncates identifiers at 63 bytes. A longer string can still name a directory.
MAX_IDENTIFIER_BYTES = 63


def is_valid_schema_id(raw: object) -> bool:
    """Total on ``object``: a non-string is model YAML, and ``re.match`` would raise on the security path."""
    if not isinstance(raw, str):
        return False
    if len(raw.encode("utf-8", errors="surrogatepass")) > MAX_IDENTIFIER_BYTES:
        return False
    return SCHEMA_ID_PATTERN.match(raw) is not None


def fold(name: str) -> str:
    """``str.lower``, not ``str.casefold``: casefold maps ``ß`` to ``ss``, so two identifiers would compare equal."""
    return name.lower()


def table_key(schema: str | None, name: str) -> str:
    """Slugged because the compared set is keyed on asset ids. Idempotent. See ADR 0008."""
    return f"{fold(schema)}.{fold(slug(name))}" if schema else fold(slug(name))


def column_key(schema: str | None, table: str, column: str) -> str:
    """Same slug rule as :func:`table_key`."""
    return f"{table_key(schema, table)}.{fold(slug(column))}"


def normalise_table_key(raw: str, default_schema: str | None) -> str:
    """A bare name takes ``default_schema`` when one is pinned, or it never equals a qualified reference."""
    parts = [p for p in raw.split(".") if p]
    if not parts:
        raise ValueError("a table key cannot be empty")
    if len(parts) == 1:
        return table_key(default_schema, parts[0])
    if len(parts) == 2:
        return table_key(parts[0], parts[1])
    raise ValueError(f"{raw!r} has {len(parts)} parts; a table key is schema.table")


def normalise_column_key(raw: str, default_schema: str | None) -> str:
    """One part raises: a bare column name matches in every table, which is B4."""
    parts = [p for p in raw.split(".") if p]
    if len(parts) == 2:
        return column_key(default_schema, parts[0], parts[1])
    if len(parts) == 3:
        return column_key(parts[0], parts[1], parts[2])
    raise ValueError(
        f"{raw!r} is not a column key. Use table.column or schema.table.column; a bare "
        "column name is not a key, it is a name that matches in every table."
    )


def fold_map(declared: Iterable[str]) -> tuple[Mapping[str, str], frozenset[str]]:
    """Spellings that differ only by case: the engine may fold to the decoy. Ambiguous folds refuse."""
    spellings: dict[str, str] = {}
    ambiguous: set[str] = set()
    for name in declared:
        key = fold(name)
        if key in spellings and spellings[key] != name:
            ambiguous.add(key)
        spellings.setdefault(key, name)
    return spellings, frozenset(ambiguous)
