"""Identifier handling: path validation (§9), folding (B5), two key shapes (§4).

* Path components: ``\\A``/``\\Z``, never ``^``/``$``.
* Fold both sides of every comparison; declared spelling reaches the engine.
* Tables: ``{schema}.{physical_name}``; columns: ``{schema}.{table}.{column}``.
  Caller sets and statement refs share the same normalisers.
"""

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

#: ``\A``/``\Z`` and nothing else. One definition of "safe as a path component":
#: duplicating it is how the asset id got one and the schema name got none.
SCHEMA_ID_PATTERN = re.compile(r"\A[A-Za-z0-9_][A-Za-z0-9_-]*\Z")

#: Postgres truncates identifiers at ``NAMEDATALEN - 1`` = 63 bytes, so a longer
#: string cannot name a real schema — but it can still name a very long directory.
#: A property of the engine, not a measurement of this system.
MAX_IDENTIFIER_BYTES = 63


def is_valid_schema_id(raw: object) -> bool:
    """Whether ``raw`` is safe to use as a path component and as a schema name.

    Total on ``object``: a non-``str`` here is model-authored YAML that parsed as an
    ``int`` or ``list``, and ``re.match`` would raise on the security path rather
    than refuse.
    """
    if not isinstance(raw, str):
        return False
    if len(raw.encode("utf-8", errors="surrogatepass")) > MAX_IDENTIFIER_BYTES:
        return False
    return SCHEMA_ID_PATTERN.match(raw) is not None


def fold(name: str) -> str:
    """Fold an unquoted identifier the way the engine will.

    ``str.lower``, not ``str.casefold``: casefold maps ``ß`` to ``ss``, so two
    distinct identifiers would compare equal here and not in the engine. A fold more
    aggressive than the engine's is a new mis-binding, which is B5's direction.
    """
    return name.lower()


def table_key(schema: str | None, name: str) -> str:
    """``{schema}.{slug(physical_name)}``, folded. Unqualified when there is no schema.

    Slugged because the allowlist it is compared against is keyed on asset ids, which
    carry the slug (ADR 0008 D1): a statement writes ``FROM airline."Air Carriers"``
    while ``licensed`` holds ``airline.Air_Carriers_66c534``. Idempotent on an
    already-slugged name, so :func:`normalise_table_key` can push a caller's *key*
    through the same function as a statement's *reference* — the only reason a
    two-part allowlist entry and a three-part reference compare at all.
    """
    return f"{fold(schema)}.{fold(slug(name))}" if schema else fold(slug(name))


def column_key(schema: str | None, table: str, column: str) -> str:
    """``{schema}.{slug(table)}.{slug(column)}``, folded."""
    return f"{table_key(schema, table)}.{fold(slug(column))}"


def normalise_table_key(raw: str, default_schema: str | None) -> str:
    """A caller-supplied table key, in the same shape a reference resolves to.

    A bare name is qualified with ``default_schema`` when the datasource pins one.
    Otherwise ``{"customers"}`` and ``FROM public.customers`` never compare equal, and
    "the allowlist is empty" and "it does not match" become one observation.
    """
    parts = [p for p in raw.split(".") if p]
    if not parts:
        raise ValueError("a table key cannot be empty")
    if len(parts) == 1:
        return table_key(default_schema, parts[0])
    if len(parts) == 2:
        return table_key(parts[0], parts[1])
    raise ValueError(f"{raw!r} has {len(parts)} parts; a table key is schema.table")


def normalise_column_key(raw: str, default_schema: str | None) -> str:
    """A caller-supplied column key, in the same shape a reference resolves to.

    Two parts is ``table.column``; three is ``schema.table.column``. One part
    **raises**: it cannot be compared against a bound reference, and accepting it
    would make a lake-wide bare-name allowlist representable — B4's hole.
    """
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
    """``(folded → declared spelling, folded names that are ambiguous)``.

    An ambiguous fold is two declared identifiers differing only by case. Left
    un-rewritten, the engine folds the reference to one of them — possibly the
    **decoy** — so the column layer approves one binding and the engine reads
    another. That is why canonicalisation is a control and not cosmetic (against ADR
    0006 §3's first draft). Ambiguous folds refuse: ``r_ambiguous_fold``.
    """
    spellings: dict[str, str] = {}
    ambiguous: set[str] = set()
    for name in declared:
        key = fold(name)
        if key in spellings and spellings[key] != name:
            ambiguous.add(key)
        spellings.setdefault(key, name)
    return spellings, frozenset(ambiguous)
