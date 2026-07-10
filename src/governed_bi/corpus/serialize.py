"""Serialize parsed corpus assets back to the Git+YAML tree (D9).

The loader reads ``corpus/<db>/`` into typed models; this writes them out again,
closing the profile/curate -> disk -> load round trip. The output is valid YAML
that ``load_corpus`` reads back to equivalent assets. Tier comments (``# Facts``)
are not reproduced; they are cosmetic.

Where the output goes is the caller's choice: the curated, human-audited corpus
is committed under ``corpus/<db>/``; machine-generated output (profiled Facts,
curator drafts) goes under ``data/generated/<db>/``, which is rebuildable and
therefore gitignored.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

import yaml

from .loader import _DIR_ASSET_TYPE, Skill
from .schemas import Asset

# asset_type -> subdirectory (inverse of the loader's dir -> type map, kept in sync).
_SUBDIR_BY_TYPE: dict[str, str] = {v: k for k, v in _DIR_ASSET_TYPE.items()}


def subdir_for_type(asset_type: str) -> str:
    """The corpus subdirectory an ``asset_type`` is written to (e.g. ``metrics``).

    The single source of truth for an asset's on-disk location, so callers can
    compute the canonical ``root/<db>/<subdir>/<id>.yaml`` path without a
    filesystem search. Raises ``KeyError`` for an unknown type.
    """
    return _SUBDIR_BY_TYPE[asset_type]


def _yamlify(obj: Any) -> Any:
    """Coerce to YAML-safe primitives. ``Any``-typed values such as a column's
    ``sample_values`` may hold Decimal / datetime / bytes read from a DB; those
    are stringified while real scalars (str/int/float/bool/None) are kept."""
    if isinstance(obj, dict):
        return {k: _yamlify(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_yamlify(v) for v in obj]
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    return str(obj)


def dump_asset(asset: Asset) -> str:
    """Serialize one asset to YAML text (round-trips through ``parse_asset``)."""
    data = _yamlify(asset.model_dump(mode="json", exclude_none=True))
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)


def dump_skill(skill: Skill) -> str:
    """Serialize a skill to Markdown-with-frontmatter (round-trips through the loader)."""
    front = _yamlify(skill.frontmatter.model_dump(mode="json", exclude_none=True))
    frontmatter = yaml.safe_dump(front, sort_keys=False, allow_unicode=True)
    body = skill.body if skill.body.endswith("\n") else skill.body + "\n"
    return f"---\n{frontmatter}---\n\n{body}"


def write_corpus(
    root: Path | str,
    db: str,
    assets: Iterable[Asset],
    skills: Iterable[Skill] = (),
) -> list[Path]:
    """Write ``assets`` and ``skills`` into ``root/<db>/`` and return the paths.

    ``db`` selects the subtree, since join / term / metric / rule / negative
    assets carry no ``db`` field (it is implied by their location). Creates the
    per-type subdirectories as needed.
    """
    db_dir = Path(root) / db
    written: list[Path] = []

    for asset in assets:
        out_dir = db_dir / _SUBDIR_BY_TYPE[asset.asset_type]
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{asset.id}.yaml"
        path.write_text(dump_asset(asset), encoding="utf-8")
        written.append(path)

    for skill in skills:
        out_dir = db_dir / "skills"
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{skill.frontmatter.skill_id}.md"
        path.write_text(dump_skill(skill), encoding="utf-8")
        written.append(path)

    return written
