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
- ``curated_sme``: + the Simulated-SME clarification round, whose brief carries
  BIRD's human column docs.

Moat claims need the adjacent steps above, not a bundled ``baseline -> curated``.
``curated -> curated_sme`` bundles the protocol and the human docs permanently —
the ``curated_sme_blind`` rung that split them was removed 2026-07-28 as
meaningless (it briefed the SME on inputs Phase A already had). Disclose that
confound; do not read the delta as evidence for the protocol. ``ceiling`` remains a test-aware
oracle reference line — designed, not built; counterfactual oracle rungs live in
:mod:`governed_bi.eval.oracle` and are not fair ``Arm`` members.

The **curator reads ``train_final.jsonl`` only**. Grading defaults to held-out
``test_final.jsonl`` (disjoint seeded split = structural leakage prevention), and
that is the **only quotable split**. ``run_datalake --split train|both`` will also
score the train questions, but only as a diagnostic: the curator was built from
that gold SQL, so a curated arm's train EX is partly recall of statements it read.
:func:`governed_bi.eval.index.quotable` refuses a train-scored run for exactly
that reason ("a diagnostic, not a result"); what the pair is *for* is the
train-vs-test gap in :mod:`governed_bi.eval.split_gap`.

- ``ex``: execution-accuracy scoring vs gold SQL.
- ``arms``: the arm harness (EX + free behavioral signals) and solvers.
- ``dataset``: a small vendored beer_factory gold set until the BIRD jsonl lands.
- ``refuse_gate``: the refusal scorer. No driver calls it — the cross-DB negative
  set it was wired to is invalid once schemas are pooled (open-work X6); the
  scorer waits for a genuinely out-of-scope set.
"""

from __future__ import annotations

from .analysis import (
    McNemarResult,
    TableSelectionReport,
    analyse_run,
    census_delta,
    corpus_census,
    gradeable_report,
    mcnemar,
    rank_report,
    sql_tables,
    table_selection_report,
)
from .arms import Arm, ArmResult, Solver, agent_solver, run_arm, run_arms
from .bird_loader import available_dbs, load_bird_items
from .dataset import BEER_FACTORY_EVAL, BEER_FACTORY_UNANSWERABLE, EvalItem
from .ex import execution_match

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
    cluster_sign_test,
    comparison_report,
    correct_by_question,
    holm_adjust,
    measure_floor,
    minimum_detectable_effect,
)
from .power import mcnemar as paired_mcnemar  # noqa: E402
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
