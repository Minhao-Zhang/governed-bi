"""The corpus on disk to typed assets: one Ossie model per schema, at ``<root>/<schema>/<schema>.yaml``.

A bad item is one problem; ``load`` does not raise for it. The format is :mod:`.ossie` (ADR 0022).
YAML 1.1 ``on`` stays a string.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Sequence

import yaml

from .identity import corpus_files, validate_asset_id, validate_path_component
from .ossie import model_text, read_model
from .parse import from_mapping
from .schema import Asset, class_for
from .validate import Problem, problems_with

__all__ = ["SUFFIX", "load", "load_file", "write"]

SUFFIX = ".yaml"

#: ``.yml`` is listed so a near-miss suffix is reported. Other files are not corpus files.
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


def load(root: Path | str, *, schemas: Sequence[str] | None = None) -> tuple[list[Asset], list[Problem]]:
    """Assets plus one problem per bad item. ``schemas=None`` means the tree is the manifest."""
    base = Path(root)
    problems: list[Problem] = []
    if not base.is_dir():
        return [], [Problem(str(base), "the corpus root does not exist or is not a directory")]

    if schemas is not None:
        for name in schemas:
            if not (base / name / f"{name}{SUFFIX}").is_file():
                problems.append(
                    Problem(
                        str(base / name),
                        f"the manifest names schema {name!r} and there is no {name}/{name}{SUFFIX}, "
                        "so every asset of that schema is missing from this load",
                    )
                )

    assets: list[Asset] = []
    for path in corpus_files(base, schemas=schemas, suffixes=_LOOKED_AT):
        where = path.relative_to(base).as_posix()
        parts = path.relative_to(base).parts
        if path.suffix.lower() != SUFFIX or len(parts) != 2 or parts[1] != f"{parts[0]}{SUFFIX}":
            problems.append(
                Problem(
                    where,
                    f"was not loaded: the corpus holds one model per schema, at <schema>/<schema>{SUFFIX}. "
                    "Reported rather than skipped, so a file the loader ignores is not a schema the corpus lost",
                )
            )
            continue
        found, trouble = load_file(path, where=where)
        assets.extend(found)
        problems.extend(trouble)
    return assets, problems


def load_file(path: Path, *, where: str | None = None) -> tuple[list[Asset], list[Problem]]:
    """One model file. Never raises. ``where`` is the problem label, so the text does not depend on the machine."""
    label = where or str(path)
    try:
        document = yaml.load(path.read_text(encoding="utf-8-sig"), Loader=_LOADER)  # noqa: S506 - SafeLoader
    except Exception as err:  # noqa: BLE001 — every read failure becomes a per-file Problem
        return [], [Problem(label, f"could not be read as YAML: {type(err).__name__}: {err}")]
    if document is None:
        return [], [Problem(label, "is empty. An empty model file is a truncated write, not a schema")]
    if isinstance(document, Mapping) and document.get("name") != path.parent.name:
        return [], [Problem(label, f"names model {document.get('name')!r}; the directory says {path.parent.name!r}")]
    try:
        items = read_model(document, where=label)
    except Exception as err:  # noqa: BLE001 — a malformed model is a reported reason, not a crash
        return [], [Problem(label, f"{type(err).__name__}: {err}")]

    assets: list[Asset] = []
    problems: list[Problem] = []
    for raw in items:
        position = f"{label}: {raw.get('asset_type')} {raw.get('id')!r}"
        found, reasons = _one(raw)
        assets.extend(found)
        if reasons:
            problems.append(Problem(position, "; ".join(reasons)))
    return assets, problems


def _one(raw: Mapping[str, Any]) -> tuple[list[Asset], list[str]]:
    """Construct, then validate. A sanitizer would change indexed text without moving the file hash. See ADR 0005."""
    try:
        class_for(raw["asset_type"])
        asset = from_mapping(raw)
    except Exception as err:  # noqa: BLE001 — a malformed asset is a reported reason, not a crash
        return [], [f"{type(err).__name__}: {err}"]
    broken = problems_with(asset)
    if broken:
        return [], broken
    try:
        validate_asset_id(asset.id)
    except Exception as err:  # noqa: BLE001 — a bad id is a reported reason, not a crash
        return [], [str(err)]
    return [asset], []


def write(root: Path | str, name: str, assets: Sequence[Asset]) -> Path:
    """Write one schema's model file. Raises on an invalid asset or one the model cannot hold."""
    reasons = [r for asset in assets for r in problems_with(asset)]
    if reasons:
        raise ValueError("; ".join(reasons))
    validate_path_component(name, what="schema")
    for asset in assets:
        validate_asset_id(asset.id)
    target = Path(root) / name / f"{name}{SUFFIX}"
    target.parent.mkdir(parents=True, exist_ok=True)
    # LF, not the platform newline: the content hash reads bytes.
    target.write_text(model_text(name, assets), encoding="utf-8", newline="\n")
    return target
