"""The three-arm eval harness (Architecture section 8; D4).

Runs a set of questions through a *solver* (question -> SQL, or None if it
declines/refuses) and scores EX plus the free behavioral signals:

- **decoy-touch rate**: share of produced queries that reference a
  manifest-flagged fake column (Server "three points" #1 drives this to 0 in dev
  via the suspect hard-block). Computed here from the corpus suspect set.
- **governed-path adherence**: share of questions the solver actually answered
  (produced SQL for) rather than refused.

The three arms differ only by the corpus/solver they use: (1) no semantic layer,
(2) curator-built layer, (3) gold layer. Arm 2 vs 1 is the moat proof; Arm 2 vs 3
is curator quality. This module supplies the reusable scorer (``run_arm``) and a
``flow_solver`` that drives the deterministic server flow as the curator arm; the
no-layer (LLM baseline) and gold (manifest oracle) solvers plug into the same
``run_arms`` orchestrator when available.
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
    from ..server.sqlgen import SqlGenerator
    from .dataset import EvalItem


class Arm(str, Enum):
    no_layer = "no_layer"  # Arm 1
    curator = "curator"  # Arm 2
    gold = "gold"  # Arm 3


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
    just the curator arm in dev); the no-layer and gold arms plug in the same way
    once their solvers exist."""
    return {
        arm: run_arm(
            arm, gateway, items, solver, suspect_columns=suspect_columns, dialect=dialect
        )
        for arm, solver in solvers.items()
    }


def flow_solver(
    corpus: "Corpus",
    gateway: "Gateway",
    settings: "Settings",
    identity: "Identity",
    *,
    session_id: str = "eval",
    sql_generator: "SqlGenerator | None" = None,
) -> Solver:
    """A :class:`Solver` that drives the server flow (the curator arm).

    Returns the flow's generated SQL, or ``None`` when the flow refuses (a
    refusal is not a governed-path answer and scores as unsolved). After each
    ``solve``, ``last_solve_meta`` holds audit fields from ``Answer.provenance``
    (``refused_by``, ``failed_layer``, ``graded_delivery``, …).
    """
    from ..server import answer_question

    class _FlowSolver:
        def __init__(self) -> None:
            self.last_solve_meta: dict = {}

        def solve(self, question: str) -> str | None:
            answer = answer_question(
                question,
                identity,
                corpus=corpus,
                gateway=gateway,
                settings=settings,
                session_id=session_id,
                sql_generator=sql_generator,
            )
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
            }
            return answer.sql

    return _FlowSolver()


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
    """A :class:`Solver` that drives the ADR-0002 agentic serve core (ledger arm).

    Mirrors :func:`flow_solver` but routes through ``answer_question_agent`` (the
    ``create_agent`` + governance-middleware path). The outer rails graph is built
    once and invoked per question; each ``solve`` is independent (no working memory
    / cache), matching the single-round eval contract. ``last_solve_meta`` carries
    the same audit fields as ``flow_solver`` plus the governance-ledger length.
    """
    from ..server.agent import build_serve_rails

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
            final = graph.invoke({"question": question, "session_id": session_id})
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
