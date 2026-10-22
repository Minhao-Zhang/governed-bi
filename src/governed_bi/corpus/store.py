"""YAML on disk to typed assets. A bad item is one problem; ``load`` does not raise for it.

``.yml`` is reported, not skipped. YAML 1.1 ``on`` stays a string.
"""


from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Sequence

import yaml

from .identity import (
    corpus_files,
    derive_column_id,
    validate_asset_id,
    validate_path_component,
)
from .parse import from_mapping, to_mapping
from .schema import Asset, SchemaAsset, TableAsset, class_for
from .validate import Problem, problems_with

__all__ = ["SUFFIX", "load", "load_file", "write"]

SUFFIX = ".yaml"

#: ``.yml`` is listed so a near-miss suffix is reported. Other files are not asset files.
_LOOKED_AT: tuple[str, ...] = (SUFFIX, ".yml")


def _loader_class() -> type:
    """YAML loader with the 1.1 bool aliases removed, so a join key ``on`` stays a string."""
    try:
        base: type = yaml.CSafeLoader  # type: ignore[attr-defined]
    except AttributeError:  # pragma: no cover - depends on the libyaml build
        base = yaml.SafeLoader

    loader = type("_CorpusLoader", (base,), {})
    bool_tag = "tag:yaml.org,2002:bool"
    loader.yaml_implicit_resolvers = {  # type: ignore[attr-defined]
        first: [(tag, rx) for tag, rx in mappings if tag != bool_tag]
        for first, mappings in base.yaml_implicit_resolvers.items()  # type: ignore[attr-defined]
    }
    loader.add_implicit_resolver(  # type: ignore[attr-defined]
        bool_tag,
        re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"),
        list("tTfF"),
    )
    return loader


_LOADER = _loader_class()


def load(
    root: Path | str, *, schemas: Sequence[str] | None = None
) -> tuple[list[Asset], list[Problem]]:
    """Assets plus one problem per bad item. ``schemas=None`` means the tree is the manifest."""
    base = Path(root)
    problems: list[Problem] = []
    if not base.is_dir():
        return [], [Problem(str(base), "the corpus root does not exist or is not a directory")]

    if schemas is not None:
        for name in schemas:
            if not (base / name).is_dir():
                problems.append(
                    Problem(
                        str(base / name),
                        f"the manifest names schema {name!r} and there is no directory for "
                        "it, so every asset of that schema is missing from this load",
                    )
                )

    assets: list[Asset] = []
    for path in corpus_files(base, schemas=schemas, suffixes=_LOOKED_AT):
        if path.suffix.lower() != SUFFIX:
            problems.append(
                Problem(
                    path.relative_to(base).as_posix(),
                    f"has suffix {path.suffix!r} and was not loaded; asset files are "
                    f"{SUFFIX}. Reported rather than skipped: a file the loader ignores "
                    "in silence is an asset the corpus lost",
                )
            )
            continue
        found, trouble = load_file(path, where=path.relative_to(base).as_posix())
        assets.extend(found)
        problems.extend(trouble)
    return assets, problems


def load_file(path: Path, *, where: str | None = None) -> tuple[list[Asset], list[Problem]]:
    """One file. Never raises. ``where`` is the problem label, so the text does not depend on the machine."""
    label = where or str(path)
    try:
        text = path.read_text(encoding="utf-8-sig")
        document = yaml.load(text, Loader=_LOADER)  # noqa: S506 - _LOADER derives from SafeLoader
    except Exception as err:  # noqa: BLE001 — every read failure becomes a per-file Problem
        return [], [Problem(label, f"could not be read as YAML: {type(err).__name__}: {err}")]

    if document is None:
        return [], [Problem(label, "is empty. An empty asset file is a truncated write, not an asset")]

    raw_items = document if isinstance(document, list) else [document]
    assets: list[Asset] = []
    problems: list[Problem] = []
    for index, raw in enumerate(raw_items):
        position = label if len(raw_items) == 1 else f"{label}[{index}]"
        found, reasons = _one(raw, where=position)
        assets.extend(found)
        if reasons:
            problems.append(Problem(position, "; ".join(reasons)))
    return assets, problems


def _one(raw: Any, *, where: str) -> tuple[list[Asset], list[str]]:
    """Construct, then validate. A sanitizer would change indexed text without moving the file hash. See ADR 0005."""
    if not isinstance(raw, Mapping):
        return [], [f"expected a mapping, got {type(raw).__name__}"]
    if "asset_type" not in raw:
        return [], ["no asset_type: the discriminator decides which of the eight this is"]

    try:
        class_for(raw["asset_type"])  # loud on an unknown type before anything else runs
        parent, columns, reasons = _split_inline_columns(dict(raw))
        if reasons:
            return [], reasons
        built = [from_mapping(item) for item in [parent, *columns]]
    except Exception as err:  # noqa: BLE001 — a malformed asset is a reported reason, not a crash
        return [], [f"{type(err).__name__}: {err}"]

    broken = [reason for asset in built for reason in problems_with(asset)]
    if broken:
        return [], broken
    for asset in built:
        try:
            validate_asset_id(asset.id)
        except Exception as err:  # noqa: BLE001 — a bad id is a reported reason, not a crash
            return [], [str(err)]
    return built, []


def _split_inline_columns(
    raw: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
    """Inline columns become their own assets. Identity is derived; a disagreeing field is an error. See ADR 0005."""
    if raw.get("asset_type") != "table" or "columns" not in raw:
        return raw, [], []
    inline = raw.get("columns")
    if not isinstance(inline, list) or not all(isinstance(c, Mapping) for c in inline):
        return raw, [], []  # already a list of ids; let the coercion judge it

    table_id, schema = raw.get("id"), raw.get("schema")
    physical = raw.get("physical_name")
    if not isinstance(table_id, str) or not isinstance(physical, str):
        return raw, [], ["a table with inline columns needs its own id and physical_name first"]

    columns: list[dict[str, Any]] = []
    reasons: list[str] = []
    for entry in inline:
        column = dict(entry)
        name = column.get("physical_name")
        if not isinstance(name, str) or not name:
            reasons.append("an inline column has no physical_name, so no id can be derived for it")
            continue
        derived = {
            "asset_type": "column",
            "id": derive_column_id(table_id, name),
            "schema": schema,
            # Parent is the table id, not the physical name: a reference names one asset (ADR 0008).
            "parent_table": table_id,
        }
        clashes = sorted(k for k, v in derived.items() if k in column and column[k] != v)
        if clashes:
            reasons.append(
                f"inline column {name!r} states {clashes} that disagree with the table it "
                "is stored under; a column's identity is derived from its position, and "
                "carrying it twice is two answers to one fact"
            )
            continue
        columns.append({**column, **derived})

    parent = {**raw, "columns": [c["id"] for c in columns]}
    return parent, columns, reasons


def write(root: Path | str, asset: Asset, *, namespace: str | None = None) -> Path:
    """Raise on an invalid asset. Types with no ``schema`` need ``namespace``; the directory is not guessed."""
    reasons = problems_with(asset)
    if reasons:
        raise ValueError("; ".join(reasons))

    directory = namespace if namespace is not None else _namespace(asset)
    if directory is None:
        raise ValueError(
            f"{type(asset).__name__} declares no `schema` field, so write() needs an "
            "explicit namespace= rather than a guessed directory"
        )
    validate_path_component(directory, what="namespace")
    validate_asset_id(asset.id)

    target = Path(root) / directory / f"{asset.id}{SUFFIX}"
    target.parent.mkdir(parents=True, exist_ok=True)
    # LF, not the platform newline: the content hash reads bytes.
    target.write_text(
        yaml.safe_dump(to_mapping(asset), sort_keys=False, allow_unicode=True),
        encoding="utf-8",
        newline="\n",
    )
    return target


def _namespace(asset: Asset) -> str | None:
    """Subtree for this asset, or ``None`` when the type has no schema field.

    A metric or term ``name`` is a business name, so it is not the directory.
    """
    if isinstance(asset, SchemaAsset):
        return asset.name or None
    value = getattr(asset, "schema", None)
    return value if isinstance(value, str) and value else None


def _assert_column_ids_are_derived_the_same_way() -> None:
    """The loader and the seed must keep one column-id spelling."""
    probe = derive_column_id("beer_factory.customers", "email")
    if not probe.startswith("beer_factory.customers") or probe.rsplit(".", 1)[-1] != "email":
        raise AssertionError(  # pragma: no cover - import-time guard
            f"derive_column_id no longer extends the table id with the bare column "
            f"name ({probe!r}); the loader's inline-column expansion assumes it does"
        )
    if "columns" not in {f.name for f in TableAsset.__dataclass_fields__.values()}:
        raise AssertionError(  # pragma: no cover - import-time guard
            "TableAsset has no `columns` field, so inline-column expansion has nowhere "
            "to record the ids it derived and every loaded column would be orphaned"
        )


_assert_column_ids_are_derived_the_same_way()
