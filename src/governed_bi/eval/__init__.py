"""Eval / telemetry service: the shared scoreboard (Architecture section 8; D3/D4).

Near-term eval = the BIRD-Obfuscation dataset (verified ground truth). Headline
metric = **execution accuracy (EX)** vs gold; no hand-grading of semantic layers.

Three arms, all scored on EX (run on the ``rename_decoy`` instance; ``base`` as
sanity reference, since only the corpus differs across arms, the physical DB is
one):

1. no semantic layer
2. curator-built layer
3. gold layer (deterministic de-obfuscation oracle)

Moat = the share of the obfuscation-induced accuracy drop the curator recovers;
Arm 3 = the recoverable *reference line* (not a strict ceiling, since Arm 2 can
beat it on skill-sensitive questions where gold has no skills). Arm 2 vs 3 =
curator quality.

The **curator reads ``train_final.jsonl`` only**; grading is on held-out
``test_final.jsonl`` (disjoint seeded split = structural leakage prevention).

- ``ex``: execution-accuracy scoring vs gold SQL.
- ``arms``: the arm harness (EX + free behavioral signals) and solvers.
- ``dataset``: a small vendored beer_factory gold set until the BIRD jsonl lands.
- ``gold``: Arm 3, the deterministic de-obfuscation oracle (needs manifests).
- ``refuse_gate``: refusal recall / false-refusal rate on an unanswerable set.
"""

from __future__ import annotations

from .arms import Arm, ArmResult, Solver, flow_solver, run_arm, run_arms
from .bird_loader import available_dbs, load_bird_items
from .dataset import BEER_FACTORY_EVAL, BEER_FACTORY_UNANSWERABLE, EvalItem
from .ex import execution_match
from .refuse_gate import RefuseGateResult, eval_refuse_gate, flow_refuser

__all__ = [
    "Arm",
    "ArmResult",
    "BEER_FACTORY_EVAL",
    "BEER_FACTORY_UNANSWERABLE",
    "EvalItem",
    "RefuseGateResult",
    "Solver",
    "available_dbs",
    "eval_refuse_gate",
    "execution_match",
    "flow_refuser",
    "flow_solver",
    "load_bird_items",
    "run_arm",
    "run_arms",
]
