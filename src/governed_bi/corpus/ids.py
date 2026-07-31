"""Asset ID conventions (CI regex-checked).

Concretizes the ID table in ``docs/asset-schemas.md`` / D9. Every typed asset
carries an ``id`` matching a per-type regex; the loader derives column ids from
their parent table. A green regex check is one half of the curator's
machine-checkable "done-enough" signal (the other half is reference integrity,
see ``validate.py``).
"""

from __future__ import annotations

import re

# A name segment: lowercase alphanumerics, underscore-joined. DB and table names
# themselves contain underscores (e.g. ``beer_factory``), so the patterns
# validate the *prefix and shape*, not a segment-by-segment parse.
_NAME = r"[a-z0-9]+(?:_[a-z0-9]+)*"
_NUM = r"\d+"
_SLUG_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def slug(name: str, *, fallback: str = "x") -> str:
    """Make a regex-valid id segment from a real name (ids are lowercase a-z0-9_).

    Empty results (all punctuation) become ``fallback`` so segments stay non-empty
    under ``_NAME`` / ``is_valid_id``.
    """
    return _SLUG_NON_ALNUM.sub("_", name.lower()).strip("_") or fallback


# asset_type -> compiled ID pattern.
# \A...\Z, not ^...$: Python's $ also matches just before a trailing newline, so
# "tbl_foo\n" would otherwise validate as a well-formed id.
ID_PATTERNS: dict[str, re.Pattern[str]] = {
    "table": re.compile(rf"\Atbl_{_NAME}\Z"),
    "column": re.compile(rf"\Acol_{_NAME}\Z"),
    "join": re.compile(rf"\Ajoin_{_NAME}\Z"),
    "few_shot": re.compile(rf"\Afs_{_NAME}_{_NUM}\Z"),
    "term": re.compile(rf"\Aterm_{_NAME}\Z"),
    "metric": re.compile(rf"\Ametric_{_NAME}\Z"),
    "note": re.compile(rf"\Anote_{_NAME}\Z"),
    "negative_example": re.compile(rf"\Aneg_{_NAME}_{_NUM}\Z"),
}

# asset_type -> literal ID prefix, for constructing/eyeballing ids.
ID_PREFIX: dict[str, str] = {
    "table": "tbl_",
    "column": "col_",
    "join": "join_",
    "few_shot": "fs_",
    "term": "term_",
    "metric": "metric_",
    "note": "note_",
    "negative_example": "neg_",
}


def is_valid_id(asset_type: str, asset_id: str) -> bool:
    """True if ``asset_id`` matches the convention for ``asset_type``."""
    pattern = ID_PATTERNS.get(asset_type)
    if pattern is None:
        return False
    return bool(pattern.match(asset_id))


def derive_column_id(table_id: str, physical_name: str) -> str:
    """Loader-internal column id, e.g. ``col_beer_factory_customers_CustomerID``.

    Columns are inline in their table asset and do not carry their own ``id`` in
    YAML (D9: "id derived by loader"). This derivation is deterministic and
    unique within a DB; it is what column-level references resolve against.
    """
    return f"col_{table_id.removeprefix(ID_PREFIX['table'])}_{physical_name}"
