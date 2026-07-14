"""Server: the serve harness (``LangGraph`` + middleware).

The online governed agent that *consumes* the corpus to answer, **fail-closed
and auditable** (ADR 0002): a thin deterministic outer ``StateGraph`` wraps an
inner ``create_agent`` reasoning loop. Authority stays deterministic — what may
execute, what is trusted — even though the reasoning inside the loop is
agentic (design-spine #2, as reversed by ADR 0002: the question can be wide,
but the SQL that runs is still gated by deterministic guardrails).

Middleware: ``before_model`` injects context (working memory, RLS scope,
semantic-layer router); ``wrap_tool_call`` runs the guardrails and is where
fail-closed lives.

Modules map to the pipeline (``docs/server.md``):

- ``routing``: query understanding, term binding, intent route.
- ``sqlgen``: SQL generation (deterministic template + LLM seam).
- ``cache``: SQL semantic-cache fast path.
- ``agent``: the governed agentic core + outer deterministic rails (ADR 0002);
  entry point ``answer_question_agent``.
- ``governance``: shared stamping/refusal/cache-hit helpers the agent core calls.
- ``middleware``: before_model / wrap_tool_call hooks.
- ``answer``: answer assembly + reliability stamp.

Retrieval, join planning, guardrails, and gateway execution live in the
``retrieval``, ``graph``, and ``gateway`` packages (shared substrate).
"""

from __future__ import annotations

from .answer import (
    Answer,
    ReliabilityTier,
    ResultTable,
    SemanticAssurance,
    UncertaintySignals,
    assemble,
    reliability_tier,
    semantic_assurance,
)
from .cache import CacheEntry, SqlCache
from .context import PromptContext, assemble_context
from .narrate import AnswerNarrator, LlmAnswerNarrator
from .routing import Route, bind_terms, route_intent
from .sqlgen import GeneratedSql

__all__ = [
    "Answer",
    "AnswerNarrator",
    "CacheEntry",
    "GeneratedSql",
    "LlmAnswerNarrator",
    "PromptContext",
    "ReliabilityTier",
    "ResultTable",
    "SemanticAssurance",
    "SqlCache",
    "Route",
    "UncertaintySignals",
    "assemble",
    "assemble_context",
    "bind_terms",
    "reliability_tier",
    "semantic_assurance",
    "route_intent",
]
