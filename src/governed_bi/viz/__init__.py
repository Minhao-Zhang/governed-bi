"""Viz: the read-only audit cockpit (D6 human gate, review side).

A simple local app to audit the AI-built corpus: corpus health, the table/tier
view, the asset listing, skills, and an "ask" panel that runs the server flow and
shows the reliability stamp. It reads the full corpus (Facts + Inference + Audit +
``governance.excluded`` assets), unlike the server's ``for_server`` view.

**Editing the corpus and opening PRs is out of scope here.** Because git is the
source of truth (D9), a correction is "edit a file + PR", served by generic
git/PR tooling plus CI (dev) or the enterprise app (prod). This repo owns the
write primitives an editor reuses (``corpus.schemas``, ``corpus.serialize``,
``corpus.validate``), not the interactive editor or the PR orchestration. The
editability tier contract (Facts read-only, Inference editable, Audit
system-written, Governance human-only; edit flips ``draft -> certified``) is what
such a downstream editor honors.

Structure (so the UI is swappable):

- ``presenter`` builds UI-agnostic view models from the corpus and answers, with
  no UI dependency. This is the stable contract every frontend renders.
- ``app`` is the current Streamlit renderer (optional ``viz`` extra), the only
  UI-specific module. A more mature frontend replaces ``app`` alone.

See ``docs/viz.md``.
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
