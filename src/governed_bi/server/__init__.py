"""Server: the serve harness (``LangGraph`` + middleware).

The online governed agent that *consumes* the corpus to answer, **fail-closed
and auditable**. A deterministic LangGraph DAG with conditional routing, never
autonomous ReAct (design-spine #2: the question can be wide, the SQL must be
narrow).

Middleware: ``before_model`` injects context (working memory, RLS scope,
semantic-layer router); ``wrap_tool_call`` runs the guardrails and is where
fail-closed lives.

Modules map to the flow (``docs/server.md``):

- ``routing``: query understanding, term binding, intent route.
- ``sqlgen``: SQL generation (deterministic template + LLM seam).
- ``cache``: SQL semantic-cache fast path.
- ``flow``: the deterministic DAG wiring the stages together.
- ``middleware``: before_model / wrap_tool_call hooks.
- ``answer``: answer assembly + reliability stamp.

Retrieval, join planning, guardrails, and gateway execution live in the
``retrieval``, ``graph``, and ``gateway`` packages (shared substrate).
"""

from __future__ import annotations

from .answer import Answer, ReliabilityTier, UncertaintySignals, assemble, reliability_tier
from .cache import CacheEntry, SqlCache
from .context import PromptContext, assemble_context
from .flow import answer_question
from .routing import Route, bind_terms, route_intent
from .sqlgen import (
    GeneratedSql,
    LlmSqlGenerator,
    RepairFeedback,
    SqlGenerator,
    TemplateSqlGenerator,
)

__all__ = [
    "Answer",
    "CacheEntry",
    "GeneratedSql",
    "LlmSqlGenerator",
    "PromptContext",
    "ReliabilityTier",
    "SqlCache",
    "RepairFeedback",
    "Route",
    "SqlGenerator",
    "TemplateSqlGenerator",
    "UncertaintySignals",
    "answer_question",
    "assemble",
    "assemble_context",
    "bind_terms",
    "reliability_tier",
    "route_intent",
]
