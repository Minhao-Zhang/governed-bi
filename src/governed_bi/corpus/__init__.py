"""Corpus service: the Git+YAML typed-asset semantic layer (D9).

The corpus is the moat. Git is the single source of truth; every other store
(graph / vector / BM25 / Postgres) is a rebuildable projection. This package
holds the schema, ID conventions, CI validator, and loader — the concrete,
fully-specified pieces. The curator (``governed_bi.curator``) writes the corpus;
the analyst (``governed_bi.analyst``) consumes the ``for_analyst()`` view.

See ``docs/asset-schemas.md`` and D9 in ``docs/design-decisions.md``.
"""

from __future__ import annotations

from .clarify import accept_answer
from .ids import is_valid_id
from .loader import Corpus, Skill, load_corpus
from .serialize import dump_asset, dump_skill, subdir_for_type, write_corpus
from .schemas import (
    Asset,
    Clarification,
    ClarificationStatus,
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
    "Clarification",
    "ClarificationStatus",
    "Column",
    "Corpus",
    "FewShotAsset",
    "Finding",
    "accept_answer",
    "dump_asset",
    "dump_skill",
    "JoinAsset",
    "MetricAsset",
    "NegativeExampleAsset",
    "RuleAsset",
    "Skill",
    "SkillFrontmatter",
    "TableAsset",
    "TermAsset",
    "is_green",
    "is_valid_id",
    "load_corpus",
    "parse_asset",
    "parse_skill_frontmatter",
    "subdir_for_type",
    "validate_corpus",
    "write_corpus",
]
