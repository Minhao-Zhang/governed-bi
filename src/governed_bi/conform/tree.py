"""Reading a corpus tree as ``(asset_type, mapping, file)`` triples.

**Raw YAML rather than ``corpus.store.load``**, and that is the whole reason this reader exists
beside the loader: conformance must give a useful answer on a half-written tree, where the loader
would raise. V14 is the rule that asks the loader instead, and it is one rule of twenty-two.

A table's inline columns are unpacked into their own triples here, because that is what the loader
does on the way to the index and it is the *assets* the rules are about.
"""


from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from governed_bi.corpus.identity import derive_column_id


def _derived_column_id(table: dict[str, Any], column: dict[str, Any]) -> dict[str, str]:
    """``{"id": ...}`` for an inline column, or ``{}`` when there is nothing to derive from.

    An inline column carries no ``id`` in YAML -- ``corpus/identity.py::derive_column_id`` computes
    it -- so every rule reading ``a.get("id")`` skipped every column. **Measured on
    ``../BIRD-corpus``: 5,947 of 13,304 assets, 45%.** V23's whole job is catching the
    ``ValueError: duplicate index id`` that ``build_index`` raises after the commit, and a derived
    column id collides identically; it had never examined one.

    Placed *before* ``**col`` in the merge, so an explicit ``id`` in the file still wins. Empty when
    the column has no ``physical_name``, which is V14's finding and not something to paper over with
    a guessed id.
    """
    table_id = _text_or_empty(table.get("id"))
    physical = _text_or_empty(column.get("physical_name"))
    if not table_id or not physical:
        return {}
    return {"id": derive_column_id(table_id, physical)}


def _text_or_empty(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def load_assets(path: Path) -> list[tuple[str, dict[str, Any], Path]]:
    """``(asset_type, mapping, file)`` for one YAML file, columns unpacked from their table."""
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as err:
        return [("<unparseable>", {"_error": str(err)}, path)]
    if not isinstance(doc, dict):
        return [("<unparseable>", {"_error": "top level is not a mapping"}, path)]
    out = [(str(doc.get("asset_type") or "<missing>"), doc, path)]
    if doc.get("asset_type") == "table":
        for col in doc.get("columns") or []:
            if isinstance(col, dict):
                # An inline column carries no ``schema`` of its own -- the loader derives it
                # from the table (``corpus/store.py``). Copying it here is what lets V11 key
                # on ``(db, physical_name)``; without it that rule silently matched nothing
                # and reported a clean corpus that names the column each decoy resembles.
                out.append(
                    (
                        "column",
                        {"schema": doc.get("schema"), **_derived_column_id(doc, col), **col},
                        path,
                    )
                )
    return out


def walk(root: Path) -> list[tuple[str, dict[str, Any], Path]]:
    found: list[tuple[str, dict[str, Any], Path]] = []
    for p in sorted(root.rglob("*.yaml")):
        if ".git" in p.parts:
            continue
        found.extend(load_assets(p))
    return found
