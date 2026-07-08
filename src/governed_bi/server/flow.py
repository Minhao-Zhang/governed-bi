"""The deterministic LangGraph DAG (Server flow; Architecture §6).

Wires the stages into a hard-wired, auditable graph with conditional routing:

    ask → supervisor → query understanding → intent route → SQL cache check →
    RVGD retrieval → Steiner-tree join plan → SQL gen → five-layer guardrails →
    execute (as-user) → answer + provenance

The refuse-gate (D5) runs concurrently with the hard guardrails, not as a stage.
Free exploration (discovery path) is a fenced pocket that only emits promotion
candidates — never autonomous ReAct over the whole graph.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..config import Settings
    from ..corpus import Corpus
    from ..gateway import Gateway, Identity
    from .answer import Answer


def answer_question(
    question: str,
    identity: "Identity",
    *,
    corpus: "Corpus",
    gateway: "Gateway",
    settings: "Settings",
    session_id: str,
) -> "Answer":
    """Run one question through the full serve DAG. Fail-closed on any guardrail
    or refuse-gate hit. ``corpus`` should be the ``for_server()`` view."""
    raise NotImplementedError("serve DAG pending; built on LangGraph + middleware")
