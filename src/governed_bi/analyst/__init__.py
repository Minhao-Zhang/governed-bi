"""Analyst: the serve harness (``LangGraph`` + middleware).

The online governed agent that *consumes* the corpus to answer, **fail-closed
and auditable** (ADR 0002): a thin deterministic outer ``StateGraph`` wraps an
inner ``create_agent`` reasoning loop. Authority stays deterministic — what may
execute, what is trusted — even though the reasoning inside the loop is
agentic (design-spine #2, as reversed by ADR 0002: the question can be wide,
but the SQL that runs is still gated by deterministic guardrails).

Middleware: ``before_model`` injects context (working memory, RLS scope,
semantic-layer router); ``wrap_tool_call`` runs the guardrails and is where
fail-closed lives.

Modules map to the pipeline (``docs/analyst.md``):

- ``sqlgen``: the flow-free value objects the agent core still needs.
- ``agent``: the governed agentic core + outer deterministic rails (ADR 0002);
  entry point ``answer_question_agent``.
- ``governance``: shared stamping/refusal helpers the agent core calls.
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
from .context import PromptContext, assemble_context
from .narrate import AnswerNarrator, LlmAnswerNarrator
from .sqlgen import GeneratedSql

__all__ = [
    "Answer",
    "AnswerNarrator",
    "GeneratedSql",
    "LlmAnswerNarrator",
    "PromptContext",
    "ReliabilityTier",
    "ResultTable",
    "SemanticAssurance",
    "UncertaintySignals",
    "assemble",
    "assemble_context",
    "reliability_tier",
    "semantic_assurance",
]
