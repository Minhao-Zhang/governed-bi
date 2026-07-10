"""Viz: UI-agnostic view models for the read-only audit surface (review side, D6).

``presenter`` turns the corpus and server answers into plain, frozen view models
(corpus health, the table/tier view, the asset listing, skills, the relationship
graph, and an answer's two-axis reliability stamp) with **no UI dependency**. It
reads the full corpus (Facts + Inference + Audit + ``governance.excluded``
assets), unlike the server's ``for_server`` view.

These view models are the stable contract the HTTP API (``governed_bi.api``)
serializes as JSON and a separate frontend renders; this repo ships no bundled UI,
so swapping frontends touches only the renderer, never this module.

Editing the corpus and opening PRs is out of scope for these view models: git is
the source of truth (D9), and the write primitives an editor reuses live in
``corpus.schemas`` / ``corpus.serialize`` / ``corpus.validate``. See
``docs/viz.md`` and ``docs/ui-frontend-design.md``.
"""

from __future__ import annotations

from .presenter import (
    AnswerView,
    AssetRow,
    ColumnView,
    CorpusHealth,
    ResultTableView,
    SchemaGraphEdge,
    SchemaGraphNode,
    SchemaGraphView,
    SkillView,
    TableView,
    answer_view,
    asset_rows,
    corpus_health,
    schema_graph,
    skill_views,
    table_views,
)

__all__ = [
    "AnswerView",
    "AssetRow",
    "ColumnView",
    "CorpusHealth",
    "ResultTableView",
    "SchemaGraphEdge",
    "SchemaGraphNode",
    "SchemaGraphView",
    "SkillView",
    "TableView",
    "answer_view",
    "asset_rows",
    "corpus_health",
    "schema_graph",
    "skill_views",
    "table_views",
]
