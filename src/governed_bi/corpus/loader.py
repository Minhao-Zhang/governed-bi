"""Load a ``corpus/<schema>/`` tree and enforce the consumption contract.

Git is the single source of truth (D9); this loader reads the YAML typed assets
and Markdown skills into memory. The **consumption contract** (docs/asset-schemas
"who reads which tier") is enforced here:

- ``Corpus.for_analyst()`` strips the Audit tier and drops ``governance.excluded``
  assets — this is what SQL-gen and the retrieval index are allowed to see.
- The Viz/audit surface uses the full ``Corpus`` (Facts + Inference + Audit).

The ``_generated/`` directory (search index, embeddings, compiled graph) is a
derived, rebuildable projection and is never read as source.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .schemas import (
    Asset,
    SkillFrontmatter,
    TableAsset,
    parse_asset,
    parse_skill_frontmatter,
)


class _CorpusYamlLoader(yaml.SafeLoader):
    """SafeLoader with YAML-1.2 boolean semantics.

    PyYAML follows YAML 1.1, which parses ``on``/``off``/``yes``/``no`` as
    booleans. The ``join`` asset has a field literally named ``on:``, so under
    the default loader that key becomes the bool ``True``. Restricting booleans
    to ``true``/``false`` lets curators author ``on:`` (and any ``yes``/``no``
    values) as plain strings, matching the schema spec.
    """


# Rebuild the resolver table on the subclass (leaving the global SafeLoader
# untouched): drop every bool resolver, then re-add one for true/false only.
_CorpusYamlLoader.yaml_implicit_resolvers = {
    ch: [(tag, rx) for (tag, rx) in resolvers if tag != "tag:yaml.org,2002:bool"]
    for ch, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
_CorpusYamlLoader.add_implicit_resolver(
    "tag:yaml.org,2002:bool",
    re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"),
    list("tTfF"),
)


def _load_yaml(text: str) -> Any:
    return yaml.load(text, Loader=_CorpusYamlLoader)

# Directory name -> expected asset_type, for a friendlier error if a file lands
# in the wrong folder. The discriminator in the file is authoritative.
_DIR_ASSET_TYPE = {
    "tables": "table",
    "joins": "join",
    "few-shots": "few_shot",
    "terms": "term",
    "metrics": "metric",
    "rules": "rule",
    "negatives": "negative_example",
}


@dataclass
class Skill:
    """A Markdown skill: parsed frontmatter + raw body."""

    frontmatter: SkillFrontmatter
    body: str
    path: Path | None = None


@dataclass
class Corpus:
    """An in-memory corpus for one schema (or several, if loaded together)."""

    assets: list[Asset] = field(default_factory=list)
    skills: list[Skill] = field(default_factory=list)

    def by_id(self, asset_id: str) -> Asset | None:
        return next((a for a in self.assets if a.id == asset_id), None)

    def tables(self) -> list[TableAsset]:
        return [a for a in self.assets if isinstance(a, TableAsset)]

    def for_analyst(self) -> "Corpus":
        """Return the Analyst-visible view: Audit stripped, ``excluded`` removed.

        Enforces the loader contract so the Analyst context is Facts + Inference
        only (never Audit) and never sees a human-excluded asset.
        """
        visible: list[Asset] = []
        for a in self.assets:
            if getattr(a, "governance", None) and a.governance.excluded:
                continue
            copy = a.model_copy(deep=True)
            if hasattr(copy, "audit"):
                copy.audit = None
            if isinstance(copy, TableAsset):
                copy.columns = [
                    c.model_copy(update={"audit": None})
                    for c in copy.columns
                    if not c.governance.excluded
                ]
            visible.append(copy)
        return Corpus(assets=visible, skills=self.skills)


def _split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Split a ``---`` YAML frontmatter block from a Markdown body."""
    if not text.startswith("---"):
        raise ValueError("skill file has no YAML frontmatter")
    parts = text.split("---", 2)
    if len(parts) < 3:
        raise ValueError("skill frontmatter is not terminated by a second '---'")
    front = _load_yaml(parts[1]) or {}
    body = parts[2].lstrip("\n")
    return front, body


def load_corpus(root: Path, schema: str | None = None) -> Corpus:
    """Load the corpus under ``root`` (a ``corpus/`` dir). If ``schema`` is given,
    load only ``root/<schema>``; otherwise load every schema subdirectory."""
    root = Path(root)
    schema_dirs = (
        [root / schema]
        if schema
        else [p for p in root.iterdir() if p.is_dir() and p.name != "_generated"]
    )

    corpus = Corpus()
    for schema_dir in schema_dirs:
        for sub, _asset_type in _DIR_ASSET_TYPE.items():
            for yaml_path in sorted((schema_dir / sub).glob("*.yaml")):
                data = _load_yaml(yaml_path.read_text(encoding="utf-8"))
                corpus.assets.append(parse_asset(data))
        for md_path in sorted((schema_dir / "skills").glob("*.md")):
            front, body = _split_frontmatter(md_path.read_text(encoding="utf-8"))
            corpus.skills.append(
                Skill(frontmatter=parse_skill_frontmatter(front), body=body, path=md_path)
            )
    return corpus
