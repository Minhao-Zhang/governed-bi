"""Load a ``corpus/<schema>/`` tree and enforce the consumption contract.

Git is the single source of truth (D9); this loader reads YAML typed assets
into memory. The **consumption contract** (docs/asset-schemas
"who reads which tier") is enforced here:

- ``Corpus.for_analyst()`` strips the Audit tier and drops ``governance.excluded``
  assets — this is what SQL-gen and the retrieval index are allowed to see.
- The Viz/audit surface uses the full ``Corpus`` (Facts + Inference + Audit).

The ``_generated/`` directory (search index, embeddings, compiled graph) is a
derived, rebuildable projection and is never read as source.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .schemas import Asset, TableAsset, parse_asset

logger = logging.getLogger(__name__)

# libyaml-backed when the wheel provides it (it does on every platform we run on),
# which parses ~7x faster than the pure-Python scanner. Corpus loading is a real
# cost on a scale run: a 69-schema build re-reads these trees per arm, and YAML
# parsing measured ~23% of an offline run's wall clock. The C loader shares
# PyYAML's *Python-side* resolver machinery, so the boolean fix below applies
# identically to both — but the fallback is kept because a source build of PyYAML
# without libyaml is a legitimate environment, and silently losing `on:` there
# would corrupt every join asset.
_BaseYamlLoader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)


class _CorpusYamlLoader(_BaseYamlLoader):  # type: ignore[misc,valid-type]
    """SafeLoader with YAML-1.2 boolean semantics.

    PyYAML follows YAML 1.1, which parses ``on``/``off``/``yes``/``no`` as
    booleans. The ``join`` asset has a field literally named ``on:``, so under
    the default loader that key becomes the bool ``True``. Restricting booleans
    to ``true``/``false`` lets curators author ``on:`` (and any ``yes``/``no``
    values) as plain strings, matching the schema spec.
    """


# Rebuild the resolver table on the subclass (leaving the global loaders
# untouched): drop every bool resolver, then re-add one for true/false only.
_CorpusYamlLoader.yaml_implicit_resolvers = {
    ch: [(tag, rx) for (tag, rx) in resolvers if tag != "tag:yaml.org,2002:bool"]
    for ch, resolvers in _BaseYamlLoader.yaml_implicit_resolvers.items()
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
    "notes": "note",
    "negatives": "negative_example",
}


@dataclass
class Corpus:
    """An in-memory corpus for one schema (or several, if loaded together)."""

    assets: list[Asset] = field(default_factory=list)

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
        return Corpus(assets=visible)


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
    unreadable: list[str] = []
    for schema_dir in schema_dirs:
        for sub, _asset_type in _DIR_ASSET_TYPE.items():
            for yaml_path in sorted((schema_dir / sub).glob("*.yaml")):
                try:
                    text = yaml_path.read_text(encoding="utf-8")
                    corpus.assets.append(parse_asset(_load_yaml(text)))
                except Exception as err:
                    # Per FILE, not per load. Only ``UnicodeDecodeError`` used to be
                    # handled here, and even that only to improve the message before
                    # re-raising — while ``_load_yaml`` (YAMLError) and ``parse_asset``
                    # (ValidationError) were bare. The comment that guard carried
                    # already stated the blast radius exactly: in the pooled runner
                    # this "fires after the whole build phase and would otherwise
                    # discard every db's work with no clue why". It does: the pooled
                    # driver loads every schema × every arm through one call, and its
                    # ``main`` wrapper is ``try``/``finally`` with no ``except``, so one
                    # truncated YAML anywhere threw away a fully-paid build of 69
                    # schemas.
                    #
                    # One bad file now costs that file. Loud, and named: skipping in
                    # silence would turn a corpus that lost half its assets into one
                    # that merely looks small, and "the treatment was thinner than we
                    # think" is the failure this project has published a result on top
                    # of before.
                    unreadable.append(f"{yaml_path}: {type(err).__name__}: {err}")
                    logger.warning("corpus file skipped — %s", unreadable[-1])
    if unreadable:
        print(
            f"*** WARNING: {len(unreadable)} corpus file(s) could not be loaded and were "
            f"SKIPPED — the corpus served is missing them ***"
        )
        for detail in unreadable[:10]:
            print(f"  - {detail}")
        if len(unreadable) > 10:
            print(f"  ... (+{len(unreadable) - 10} more)")
    return corpus
