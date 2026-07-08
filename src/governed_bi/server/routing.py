"""Server steps 2-3 — query understanding, term binding, intent routing.

Term binding resolves business language via ``term`` assets: synonyms and
``term_relationship`` map varied phrasings → the canonical asset (strong
routing, not an LLM guess). Intent routing is **hard-wired** to one of four
routes, each with its own retrieval + memory budget (``config.ROUTE_MEMORY_BUDGETS``).
"""

from __future__ import annotations

from enum import Enum


class Route(str, Enum):
    nl2sql = "nl2sql"
    kpi_lookup = "kpi_lookup"
    knowledge_qa = "knowledge_qa"
    deep_analysis = "deep_analysis"


def route_intent(question: str) -> Route:
    """Hard-wired intent classification into one of the four routes."""
    raise NotImplementedError("intent routing pending")
