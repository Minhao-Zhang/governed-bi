"""Corpus service: the Git+YAML typed-asset semantic layer (D9).

The corpus is the moat. Git is the single source of truth; every other store
(graph / vector / BM25 / Postgres) is a rebuildable projection. This package
holds the schema, ID conventions, CI validator, and loader — the concrete,
fully-specified pieces. The curator (``governed_bi.curator``) writes the corpus;
the server (``governed_bi.server``) consumes the ``for_server()`` view.

See ``docs/asset-schemas.md`` and D9 in ``docs/design-decisions.md``.
"""

from __future__ import annotations

from .loader import Corpus, Skill, load_corpus
from .schemas import (
    Asset,
    Column,
    FewShotAsset,
    JoinAsset,
    MetricAsset,
    NegativeExampleAsset,
    RuleAsset,
    SkillFrontmatter,
    TableAsset,
    TermAsset,
    parse_asset,
    parse_skill_frontmatter,
)
from .validate import Finding, is_green, validate_corpus

__all__ = [
    "Asset",
    "Column",
    "Corpus",
    "FewShotAsset",
    "Finding",
    "JoinAsset",
    "MetricAsset",
    "NegativeExampleAsset",
    "RuleAsset",
    "Skill",
    "SkillFrontmatter",
    "TableAsset",
    "TermAsset",
    "is_green",
    "load_corpus",
    "parse_asset",
    "parse_skill_frontmatter",
    "validate_corpus",
]
