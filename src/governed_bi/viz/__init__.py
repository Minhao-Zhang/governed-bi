"""Viz: the audit + edit cockpit (D6 human gate, operationalized).

A simple interactive local app (not a static site) that lets a human audit the
AI-built corpus, correct it, then save to disk and open a PR. Editing the git
YAML/MD **is** editing the source of truth (D9), so this is a git-editing
front-end, not a derived store.

Editability = the tier model: Facts read-only, Inference editable, Audit
system-written (a human edit appends a ``source: human`` provenance entry),
Governance human-only (where ``governance.excluded`` is set). Editing flips
``draft -> certified``; the audit trail becomes three-party (proposer ->
adversary -> human).

Structure (so the UI is swappable):

- ``presenter`` builds UI-agnostic view models from the corpus and answers, with
  no UI dependency. This is the stable contract every frontend renders.
- ``app`` is the current Streamlit renderer (optional ``viz`` extra), the only
  UI-specific module. A more mature frontend replaces ``app`` alone.

The current cockpit is read-only (health, tables, assets, skills, ask); edit and
save-to-PR are a planned follow-up. See ``docs/viz.md``.
"""

from __future__ import annotations

from .presenter import (
    AnswerView,
    AssetRow,
    ColumnView,
    CorpusHealth,
    SkillView,
    TableView,
    answer_view,
    asset_rows,
    corpus_health,
    skill_views,
    table_views,
)

__all__ = [
    "AnswerView",
    "AssetRow",
    "ColumnView",
    "CorpusHealth",
    "SkillView",
    "TableView",
    "answer_view",
    "asset_rows",
    "corpus_health",
    "skill_views",
    "table_views",
]
