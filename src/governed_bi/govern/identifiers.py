"""Identifier handling: path validation (§9), folding (B5), and the two key shapes (§4).

Three separate v1 defects live in this file, and they are here together because
each one is a disagreement between *our* spelling of a name and *somebody else's*.

**B8 — ``\\A``/``\\Z``, never ``^``/``$``.** ``asset.schema`` becomes a directory
name while ``is_valid_id`` guarded only the asset id. Python's ``$`` also matches
before a trailing newline, so ``"beer_factory\\n"`` clears a ``^...$`` validator
labelled *security* and then names a directory. v2 makes the surface **wider**, not
narrower: ``SchemaAsset.name`` is a first-class field, and ADR 0005 §1.5
acknowledges the corpus is partly model-authored. **v2 has no HTTP corpus write**;
CLI / ``CorpusStore.write`` still derive directories from these strings.

**B5 — fold both sides, do not quote to compensate.** Postgres folds unquoted
identifiers, so ``customerid`` clears a ``CustomerID`` allowlist; v1's fix was to
quote the model's spelling, which then sent the engine a column that does not
exist. The fix is that every comparison happens between folded keys and the
*declared* spelling is what reaches the engine (§3 step 2).

**§4's keys — two shapes, not one.** Tables key on ``{schema}.{physical_name}``,
columns on ``{schema}.{table}.{column}``. ADR 0006's first draft claimed one
uniform two-part key "everywhere", which would make two tables in one schema that
both have an ``id`` column a corpus validation error — i.e. every corpus.

Caller-supplied sets are normalised through the **same** functions the statement's
own references go through, which is the only reason a two-part
``customers.CustomerID`` in an allowlist can be compared with a three-part
reference in a query at all.
"""

from __future__ import annotations

import re
from typing import Iterable, Mapping

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

#: ``\A``/``\Z`` and nothing else. Written as a compiled pattern rather than
#: inlined at a call site because there is exactly one definition of "safe as a
#: path component" and duplicating it is how the asset id got one and the schema
#: name got none.
SCHEMA_ID_PATTERN = re.compile(r"\A[A-Za-z0-9_][A-Za-z0-9_-]*\Z")

#: Postgres truncates identifiers at ``NAMEDATALEN - 1`` = 63 bytes, so a longer
#: string cannot name a real schema — but it can still name a very long directory.
#: A property of the engine, not a measurement of this system.
MAX_IDENTIFIER_BYTES = 63


def is_valid_schema_id(raw: object) -> bool:
    """Whether ``raw`` is safe to use as a path component and as a schema name.

    Total on ``object`` on purpose: a non-``str`` arriving here is model-authored
    YAML that parsed into an ``int`` or a ``list``, and ``re.match`` would raise
    ``TypeError`` on the security path instead of refusing.
    """
    if not isinstance(raw, str):
        return False
    if len(raw.encode("utf-8", errors="surrogatepass")) > MAX_IDENTIFIER_BYTES:
        return False
    return SCHEMA_ID_PATTERN.match(raw) is not None


def fold(name: str) -> str:
    """Fold an unquoted identifier the way the engine will.

    ``str.lower`` rather than ``str.casefold``: casefold maps ``ß`` to ``ss``,
    which would make two distinct identifiers compare equal here and *not* in the
    engine — a fold that is more aggressive than the engine's is a new
    mis-binding, which is the direction B5 already went wrong once.
    """
    return name.lower()


def table_key(schema: str | None, name: str) -> str:
    """``{schema}.{physical_name}``, folded. Unqualified when there is no schema."""
    return f"{fold(schema)}.{fold(name)}" if schema else fold(name)


def column_key(schema: str | None, table: str, column: str) -> str:
    """``{schema}.{table}.{column}``, folded."""
    return f"{table_key(schema, table)}.{fold(column)}"


def normalise_table_key(raw: str, default_schema: str | None) -> str:
    """A caller-supplied table key, in the same shape a reference resolves to.

    A bare name is qualified with ``default_schema`` when the datasource pins one.
    Without this, an allowlist written as ``{"customers"}`` and a statement written
    as ``FROM public.customers`` never compare equal, and "the allowlist is empty"
    and "the allowlist does not match" become the same observation.
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
    **raises**: a column key with no table cannot be compared against a bound
    reference, and silently accepting it would make a lake-wide bare-name allowlist
    representable again — which is exactly what made B4 exploitable.
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
    another. ADR 0006 §3's first draft called canonicalisation "cosmetic-but-
    recorded, never a control"; this is why that was false. Ambiguous folds refuse
    (``r_ambiguous_fold``); they are rare and the alternative is silent
    mis-binding.
    """
    spellings: dict[str, str] = {}
    ambiguous: set[str] = set()
    for name in declared:
        key = fold(name)
        if key in spellings and spellings[key] != name:
            ambiguous.add(key)
        spellings.setdefault(key, name)
    return spellings, frozenset(ambiguous)
