"""The eval-ladder harness (Architecture section 8; D4; terminology-refactor).

Runs a set of questions through a *solver* (question -> SQL, or None if it
declines/refuses) and scores EX plus the free behavioral signals:

- **decoy-touch rate**: share of produced queries that reference a
  manifest-flagged fake column (Analyst "three points" #1 drives this to 0 in
  dev via the suspect hard-block). Computed here from the corpus suspect set.
- **governed-path adherence**: share of questions the solver actually answered
  (produced SQL for) rather than refused.

The eval ladder's fair rungs differ only by the corpus fed into the *same*
serve path: ``baseline`` (deterministic, DB-derivable corpus, no curator LLM),
``curated`` (curator-built Inference tier + train-SQL-derived assets), and
``curated_sme`` (``curated`` + Simulated-SME clarification rounds). ``baseline``
vs ``curated`` is the moat proof; ``curated`` vs ``curated_sme`` is the SME
lift. This module supplies the reusable scorer (``run_arm``) and
``agent_solver``, which drives the agentic serve core (ADR 0002) for every
fair rung — ``run_arms`` scores whichever rungs the caller supplies solvers for.
The ``ceiling`` rung (a test-aware oracle) is designed, not built (see
``docs/plans/terminology-refactor.md``); it intentionally has no ``Arm`` member
or solver here.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Protocol, runtime_checkable

import sqlglot
from sqlglot import exp

from .ex import execution_match

if TYPE_CHECKING:
    from ..config import Settings
    from ..corpus import Corpus
    from ..gateway import Gateway, Identity
    from .dataset import EvalItem


class Arm(str, Enum):
    baseline = "baseline"  # deterministic-max, DB-derivable only; no curator LLM
    curated = "curated"  # curator LLM layer + train-SQL-derived seed joins/few-shots
    curated_sme = "curated_sme"  # curated + Simulated-SME clarification round(s)
    # ``ceiling`` (test-aware oracle) is designed, not built — no enum member /
    # solver until it exists (docs/plans/terminology-refactor.md).


@dataclass(frozen=True)
class ArmResult:
    arm: Arm
    ex: float
    decoy_touch_rate: float
    governed_path_adherence: float
    n: int


@runtime_checkable
class Solver(Protocol):
    """Turns a question into SQL, or ``None`` if it declines / refuses."""

    def solve(self, question: str) -> str | None: ...


def _touches_suspect(sql: str, suspect_columns: frozenset[str], dialect: str) -> bool:
    if not suspect_columns:
        return False
    suspect_bare = {
        (ref.split(".", 1)[1] if "." in ref else ref) for ref in suspect_columns
    }
    try:
        tree = sqlglot.parse_one(sql, read=dialect)
    except Exception:
        return False
    return any(col.name in suspect_bare for col in tree.find_all(exp.Column))


def run_arm(
    arm: Arm,
    gateway: "Gateway",
    items: "list[EvalItem]",
    solver: Solver,
    *,
    suspect_columns: frozenset[str] = frozenset(),
    dialect: str = "sqlite",
) -> ArmResult:
    """Score one arm: EX over ``items`` plus decoy-touch and governed-path rates."""
    matches = 0
    produced = 0
    decoy = 0
    for item in items:
        pred = solver.solve(item.question)
        if not pred:
            continue  # refused / no SQL: not a governed-path answer
        produced += 1
        if _touches_suspect(pred, suspect_columns, dialect):
            decoy += 1
        if execution_match(pred, item.sql, gateway):
            matches += 1
    n = len(items)
    return ArmResult(
        arm=arm,
        ex=matches / n if n else 0.0,
        decoy_touch_rate=decoy / produced if produced else 0.0,
        governed_path_adherence=produced / n if n else 0.0,
        n=n,
    )


def run_arms(
    gateway: "Gateway",
    items: "list[EvalItem]",
    solvers: dict[Arm, Solver],
    *,
    suspect_columns: frozenset[str] = frozenset(),
    dialect: str = "sqlite",
) -> dict[Arm, ArmResult]:
    """Score every provided arm. Callers supply the solvers they can run (e.g.
    just the ``curated`` arm in dev); the other fair rungs plug in the same way
    once their solvers exist."""
    return {
        arm: run_arm(
            arm, gateway, items, solver, suspect_columns=suspect_columns, dialect=dialect
        )
        for arm, solver in solvers.items()
    }


def agent_solver(
    corpus: "Corpus",
    gateway: "Gateway",
    settings: "Settings",
    identity: "Identity",
    *,
    model,
    embedder=None,
    session_id: str = "eval",
) -> Solver:
    """A :class:`Solver` that drives the ADR-0002 agentic serve core.

    Routes through ``answer_question_agent`` (the ``create_agent`` +
    governance-middleware path) — the one serve path shared by every fair rung
    of the eval ladder (``baseline`` / ``curated`` / ``curated_sme``). The outer
    rails graph is built once and invoked per question; each ``solve`` is
    independent (no working memory / cache), matching the single-round eval
    contract. ``last_solve_meta`` carries audit fields plus the
    governance-ledger length.
    """
    from ..analyst.agent import build_serve_rails

    graph = build_serve_rails(
        corpus=corpus,
        gateway=gateway,
        settings=settings,
        identity=identity,
        model=model,
        embedder=embedder,
        session_id=session_id,
    )

    class _AgentSolver:
        def __init__(self) -> None:
            self.last_solve_meta: dict = {}

        def solve(self, question: str) -> str | None:
            from ..obs import tracing_callbacks

            final = graph.invoke(
                {"question": question, "session_id": session_id},
                config={"callbacks": tracing_callbacks()},
            )
            answer = final.get("answer")
            if answer is None:
                self.last_solve_meta = {"refused_by": "no_coverage"}
                return None
            prov = dict(answer.provenance or {})
            self.last_solve_meta = {
                "refused_by": prov.get("refused_by"),
                "failed_layer": prov.get("failed_layer"),
                "graded_delivery": bool(prov.get("graded_delivery")),
                "coverage_best_effort": bool(prov.get("coverage_best_effort")),
                "tier": answer.tier.value,
                "semantic_assurance": answer.semantic_assurance.value,
                "safety_clearance": answer.safety_clearance,
                "attempts": prov.get("attempts"),
                "ledger_len": len(prov.get("governance_ledger") or []),
            }
            return answer.sql

    return _AgentSolver()
