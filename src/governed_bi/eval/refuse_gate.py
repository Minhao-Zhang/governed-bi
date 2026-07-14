"""Refuse-gate eval (Architecture section 8; D5).

BIRD questions are all answerable, so they do **not** test the refuse-gate. This
needs a held-out **unanswerable** set: cross-DB + removed-coverage cases
(auto-generated) plus a small hand-built out-of-scope set (see
``dataset.BEER_FACTORY_UNANSWERABLE``). Scored on:

- **refusal accuracy**: refuses the unanswerable (recall of refusal)
- **false-refusal rate**: refuses the answerable (a precision cost)

The scorer takes a ``refused`` predicate (question -> bool) so it is decoupled
from the server; ``agent_refuser`` adapts the agentic serve core (ADR 0002).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..config import Settings
    from ..corpus import Corpus
    from ..gateway import Gateway, Identity
    from ..llm import Embedder


@dataclass(frozen=True)
class RefuseGateResult:
    refusal_accuracy: float  # on the unanswerable set
    false_refusal_rate: float  # on the answerable set


def _rate(questions: Iterable[str], refused: Callable[[str], bool]) -> float:
    items = list(questions)
    if not items:
        return 0.0
    return sum(1 for q in items if refused(q)) / len(items)


def eval_refuse_gate(
    answerable: Iterable[str],
    unanswerable: Iterable[str],
    refused: Callable[[str], bool],
) -> RefuseGateResult:
    """Score the refuse-gate: refusal recall on ``unanswerable`` and the
    false-refusal cost on ``answerable``, using the ``refused`` predicate."""
    return RefuseGateResult(
        refusal_accuracy=_rate(unanswerable, refused),
        false_refusal_rate=_rate(answerable, refused),
    )


def agent_refuser(
    corpus: "Corpus",
    gateway: "Gateway",
    settings: "Settings",
    identity: "Identity",
    *,
    model: Any,
    embedder: "Embedder | None" = None,
    session_id: str = "eval",
) -> Callable[[str], bool]:
    """A ``refused`` predicate that runs the agentic serve core (ADR 0002) and
    reports whether it returned a refusal (any fail-closed path: refuse-gate,
    guardrail veto, missing-edge, or coverage exhaustion). Needs a live model."""
    from ..server.agent import answer_question_agent
    from ..server.answer import ReliabilityTier

    def refused(question: str) -> bool:
        answer = answer_question_agent(
            question,
            identity,
            corpus=corpus,
            gateway=gateway,
            settings=settings,
            session_id=session_id,
            model=model,
            embedder=embedder,
        )
        return answer.tier is ReliabilityTier.refused

    return refused
