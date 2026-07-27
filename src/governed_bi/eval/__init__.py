"""Eval / telemetry service: the shared scoreboard (Architecture section 8; D3/D4).

Near-term eval = the BIRD-Obfuscation dataset (verified ground truth). Headline
metric = **execution accuracy (EX)** vs gold; no hand-grading of semantic layers.

The fair eval ladder, all scored on EX (run on the ``rename_decoy`` instance;
``base`` as sanity reference — only the corpus differs across rungs, the physical
DB is one). Default adjacent steps:

- ``baseline``: deterministic, DB-derivable corpus only (no train SQL, no curator LLM).
- ``seeded``: + train-SQL-derived joins/metrics and decoy / negative-space marking;
  still no LLM and **no few-shots** (``run_agent=False``).
- ``curated``: + curator LLM agent over that seed (Inference tier, including few-shots).
- ``curated_sme_blind`` (opt-in): + Simulated-SME clarification protocol, blind to
  BIRD's human column docs.
- ``curated_sme``: + the same SME round with those human docs in the brief.

Moat claims need the adjacent steps above, not a bundled ``baseline -> curated``.
The default SME lift without the blind rung is ``curated -> curated_sme`` (protocol
and docs together — disclose that confound). ``ceiling`` remains a test-aware
oracle reference line — designed, not built; counterfactual oracle rungs live in
:mod:`governed_bi.eval.oracle` and are not fair ``Arm`` members.

The **curator reads ``train_final.jsonl`` only**; grading is on held-out
``test_final.jsonl`` (disjoint seeded split = structural leakage prevention).

- ``ex``: execution-accuracy scoring vs gold SQL.
- ``arms``: the arm harness (EX + free behavioral signals) and solvers.
- ``dataset``: a small vendored beer_factory gold set until the BIRD jsonl lands.
- ``refuse_gate``: refusal recall / false-refusal rate on an unanswerable set.
"""

from __future__ import annotations

from .analysis import (
    McNemarResult,
    census_delta,
    corpus_census,
    TableSelectionReport,
    analyse_run,
    gradeable_report,
    mcnemar,
    rank_report,
    sql_tables,
    table_selection_report,
)

# Two exact-binomial McNemar implementations exist and both are live. The bare
# name ``mcnemar`` above is ``analysis``'s, which takes row iterables and writes
# ``analysis.json``. The drivers use ``power``'s, which takes name+dict pairs and
# reports a noise floor and minimum detectable effect alongside the p-value — that
# is the one whose numbers land in ``summary.json``, and the one to reach for when
# the question is "is this delta real". Exported under an unambiguous name rather
# than shadowing, so neither import silently gets the other.
from .power import (  # noqa: E402
    DetectableEffect,
    NoiseFloor,
    comparison_report,
    correct_by_question,
    cluster_sign_test,
    holm_adjust,
    measure_floor,
    minimum_detectable_effect,
)
from .power import mcnemar as paired_mcnemar  # noqa: E402
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
    "McNemarResult",
    "RefuseGateResult",
    "Solver",
    "TableSelectionReport",
    "agent_refuser",
    "agent_solver",
    "analyse_run",
    "census_delta",
    "corpus_census",
    "available_dbs",
    "eval_refuse_gate",
    "execution_match",
    "gradeable_report",
    "load_bird_items",
    "mcnemar",
    "paired_mcnemar",
    "DetectableEffect",
    "NoiseFloor",
    "comparison_report",
    "correct_by_question",
    "cluster_sign_test",
    "holm_adjust",
    "measure_floor",
    "minimum_detectable_effect",
    "rank_report",
    "run_arm",
    "run_arms",
    "sql_tables",
    "table_selection_report",
]
