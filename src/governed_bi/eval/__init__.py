"""Eval / telemetry service: the shared scoreboard (Architecture section 8; D3/D4).

Near-term eval = the BIRD-Obfuscation dataset (verified ground truth). Headline
metric = **execution accuracy (EX)** vs gold; no hand-grading of semantic layers.

The eval ladder, all scored on EX (run on the ``rename_decoy`` instance; ``base``
as sanity reference, since only the corpus differs across rungs, the physical DB
is one):

- ``baseline``: deterministic, DB-derivable corpus only (no curator LLM).
- ``curated``: curator-built Inference tier + train-SQL-derived seed joins/few-shots.
- ``curated_sme``: ``curated`` + Simulated-SME clarification round(s).
- ``ceiling``: test-aware oracle reference line — designed, not built.

Moat = the share of the obfuscation-induced accuracy drop the curator recovers
(``baseline`` -> ``curated``); the SME lift is ``curated`` -> ``curated_sme``.

The **curator reads ``train_final.jsonl`` only**; grading is on held-out
``test_final.jsonl`` (disjoint seeded split = structural leakage prevention).

- ``ex``: execution-accuracy scoring vs gold SQL.
- ``arms``: the arm harness (EX + free behavioral signals) and solvers.
- ``dataset``: a small vendored beer_factory gold set until the BIRD jsonl lands.
- ``refuse_gate``: refusal recall / false-refusal rate on an unanswerable set.
"""

from __future__ import annotations

from .arms import Arm, ArmResult, Solver, agent_solver, run_arm, run_arms
from .bird_loader import available_dbs, load_bird_items
from .dataset import BEER_FACTORY_EVAL, BEER_FACTORY_UNANSWERABLE, EvalItem
from .ex import execution_match
from .refuse_gate import RefuseGateResult, agent_refuser, eval_refuse_gate

__all__ = [
    "Arm",
    "ArmResult",
    "BEER_FACTORY_EVAL",
    "BEER_FACTORY_UNANSWERABLE",
    "EvalItem",
    "RefuseGateResult",
    "Solver",
    "agent_refuser",
    "agent_solver",
    "available_dbs",
    "eval_refuse_gate",
    "execution_match",
    "load_bird_items",
    "run_arm",
    "run_arms",
]
