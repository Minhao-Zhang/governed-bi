"""Analyst steps 2-3: query understanding, term binding, intent routing.

Term binding resolves business language via ``term`` assets: synonyms map varied
phrasings to the canonical asset (strong routing, not an LLM guess). Intent
routing is **hard-wired** to one of four routes, each with its own retrieval and
memory budget (``config.ROUTE_MEMORY_BUDGETS``).

Both are deterministic here. In an enterprise deployment the router may be
model-assisted, but the route set and the term-binding contract stay the same.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import TYPE_CHECKING

from ..corpus.schemas import TermAsset

if TYPE_CHECKING:
    from ..corpus import Corpus


class Route(str, Enum):
    nl2sql = "nl2sql"
    kpi_lookup = "kpi_lookup"
    knowledge_qa = "knowledge_qa"
    deep_analysis = "deep_analysis"


# Route cue phrases, checked in priority order (first hit wins). Deliberately
# small and legible: the point is a hard-wired route, not a learned classifier.
_KNOWLEDGE_CUES = ("what is", "what does", "explain", "definition", "define", "meaning of")
_DEEP_CUES = (
    "trend", "over time", "compare", "comparison", "correlat",
    "breakdown", "why", "analyz", "analys", "distribution",
)
_KPI_CUES = ("how many", "how much", "total", "count", "average", "avg", "sum of", "number of")


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def route_intent(question: str) -> Route:
    """Hard-wired intent classification into one of the four routes.

    Priority: knowledge questions, then deep-analysis cues, then single-number
    KPI cues, else the general ``nl2sql`` route.
    """
    q = question.lower()
    if any(cue in q for cue in _KNOWLEDGE_CUES):
        return Route.knowledge_qa
    if any(cue in q for cue in _DEEP_CUES):
        return Route.deep_analysis
    if any(cue in q for cue in _KPI_CUES):
        return Route.kpi_lookup
    return Route.nl2sql


def bind_terms(corpus: "Corpus", question: str) -> list[str]:
    """Ids of ``term`` assets whose name or a synonym appears in the question.

    Matched on whole token runs (a synonym's tokens must appear contiguously in
    the question), so ``brand`` binds ``term_brand`` but does not spuriously fire
    on ``brandish``. Order-stable in corpus order.
    """
    q_joined = " " + " ".join(_tokens(question)) + " "
    bound: list[str] = []
    for asset in corpus.assets:
        if not isinstance(asset, TermAsset):
            continue
        for phrase in (asset.name, *asset.synonyms):
            needle = " " + " ".join(_tokens(phrase)) + " "
            if needle.strip() and needle in q_joined:
                bound.append(asset.id)
                break
    return bound
