"""A ledger of runs, and a rule for which two of them may be compared.

The failure this exists to prevent already happened: a set of arm-to-arm EX
deltas was quoted from runs that turned out not to be comparable, under metric
definitions that turned out to be wrong, and nothing in the artifacts said so.
Recovering that took reading code, not reading results.

So every completed run appends one flat record here — configuration and headline
numbers together — and two rules are computed rather than remembered:

**Quotable.** Is this run's own number safe to state? A run with crashes, a run
built on a corpus that failed to curate, or a run scored on the train split is
not, and says so in the artifact instead of in someone's memory. Absence of
evidence counts as not-quotable: a run that never recorded its crash rate cannot
claim it had none.

**Comparable.** May these two runs be put in the same sentence? Only if the
independent variable is the one you think it is — same split, same model, same
prompt set, same routing knobs. Comparing across a changed knob is the specific
mistake that produced numbers we had to throw away.

Both are advisory in the sense that nothing blocks you, and load-bearing in the
sense that the report prints the reasons and you have to read them.

CLI::

    uv run python -m governed_bi.eval.index                    # render the ledger
    uv run python -m governed_bi.eval.index --add runs/datalake/<ts>
"""

from __future__ import annotations

import argparse
import json
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable

from ..provenance import CORPUS_HASH_UNKNOWN
from .atomic import atomic_write_text
from .metrics import MANIFEST_KNOBS, MANIFEST_SCHEMA_VERSION

DEFAULT_INDEX = Path("runs/index.jsonl")

#: Share of an arm's questions that may score correct for a non-SQL reason before the
#: run stops being quotable. Not tuned against anything — a deliberate, visible line
#: rather than the previous absence of one (AUDIT E2).
FREE_PASS_QUOTABLE_FRACTION = 0.10

#: Knobs the gate deliberately does NOT check, each with the reason. This is the only
#: way a manifest knob leaves :data:`COMPARABILITY_KEYS`, because the list is DERIVED
#: from :data:`~governed_bi.eval.metrics.MANIFEST_KNOBS` rather than spelled again.
#:
#: It used to be spelled again, and it had already drifted: ``llm_temperature`` was
#: declared a knob — a field documented as changing what a scored row *means* — and
#: was simply absent here, so two runs decoded at different temperatures compared as
#: the same experiment. Nothing failed; the key was just not in the tuple. Deriving
#: makes the next knob's default membership *inclusion*, so skipping the gate takes an
#: entry here and a reason beside it.
#:
#: An exclusion removes the key from the loop entirely. It must never be implemented by
#: leaving the value ``None`` instead — ``comparable()`` skips a key that is ``None`` on
#: both sides, so a value-level exclusion would read as agreement and be invisible.
COMPARABILITY_EXCLUSIONS: dict[str, str] = {
    "prompt_variants": (
        "the human-readable stage->variant map. `prompt_set_hash` is the "
        "machine-checkable identity of the same thing, and it hashes the prompt TEXT, "
        "so it also catches an in-place edit that leaves the variant ids identical"
    ),
    "git_sha": (
        "two runs built at different commits are the NORMAL case here — that is what "
        "comparing a change against its baseline IS, so gating on it would declare "
        "almost every pair in the ledger incomparable. Corrupting *within* one "
        "directory, which is why it is in RESUME_DRIFT_KEYS instead"
    ),
    "skip_agent": (
        "`quotable()` refuses a --skip-agent run outright, and `manifest_model` forces "
        "its `model` to None, so a smoke/real pair is already caught by a key that IS "
        "gated. Corrupting within one directory: RESUME_DRIFT_KEYS"
    ),
}

#: Human labels for the derived keys, where the field name is not the clearest label.
#: Preserved verbatim from the hand-written tuple this replaced, because they appear in
#: `comparable()`'s diff strings and in the rendered ledger.
_KNOB_LABELS: dict[str, str] = {
    "llm_temperature": "temperature",
    "prompt_set_hash": "prompt set",
    "route_llm_pick": "llm_pick",
    "use_embedder": "embedder",
    # The corpus IS the treatment, and it was the one thing the comparability check
    # did not cover (AUDIT E5): two runs over different corpora compared cleanly.
    # `corpus_content_hash` is the per-arm corpus digest; `git_sha` covers the code.
    "corpus_content_hash": "corpus content",
    # The graded question pool. Labelled rather than left as the field name because the
    # difference it reports — "these two runs scored different questions" — is the one a
    # reader is least likely to suspect: the dataset is filtered in a sibling repo, so
    # the pool moves with no knob in this repo changing.
    "question_pool_hash": "question pool",
}

#: Knobs that must match before two runs may be compared. Each is (record key,
#: human label). Derived from the register in declaration order, minus
#: :data:`COMPARABILITY_EXCLUSIONS`, so a knob added to the register joins the gate by
#: default. Keep in mind that ``run_datalake``'s resume-drift guard reads
#: :data:`RESUME_DRIFT_KEYS`, which is built from this: both answer the same question —
#: "would mixing these two mislead?" — one across a resume, one across two runs.
COMPARABILITY_KEYS: tuple[tuple[str, str], ...] = tuple(
    (knob.name, _KNOB_LABELS.get(knob.name, knob.name))
    for knob in MANIFEST_KNOBS
    if knob.name not in COMPARABILITY_EXCLUSIONS
)

#: Knobs whose change *within a single run directory* corrupts that run, checked by
#: :func:`_resume_drift`.
#:
#: A superset of :data:`COMPARABILITY_KEYS`, because the two answer different
#: questions and the extra keys belong to only one of them. ``comparable()`` asks
#: whether two *separate* runs may be compared, and two runs built at different
#: commits are the normal case there — that is what comparing a change against its
#: baseline *is*, so putting ``git_sha`` in the comparability list would declare
#: almost every pair in the ledger incomparable and the ledger would stop being
#: usable for the thing it exists for.
#:
#: Inside one directory the same difference is fatal instead of normal. Rows scored
#: before and after a code edit sit in one ``generations.<arm>.jsonl`` with no field
#: distinguishing them, so the arm's score averages two harness versions. Likewise
#: ``skip_agent``: resuming a ``--skip-agent`` smoke directory without the flag
#: blends refuse-all rows scoring zero with real model rows, and the runbook asks
#: for a ``--skip-agent`` smoke run immediately before the real one, so those two
#: directories sit side by side.
#:
#: ``run_datalake._RESUME_KNOBS`` is derived from this tuple rather than spelled
#: again. It used to be a second hand-maintained list, and it had already drifted:
#: it named ``git_sha`` and ``skip_agent`` with comments explaining why each was
#: dangerous, while the ledger check iterated the comparability list and saw
#: neither. A resume after a code edit warned once on the console — which scrolls
#: past in a multi-hour run — and then recorded no drift and stayed quotable.
RESUME_DRIFT_KEYS: tuple[tuple[str, str], ...] = COMPARABILITY_KEYS + (
    ("skip_agent", "skip_agent"),
    ("git_sha", "git_sha"),
)


def manifest_model(model_name: str | None, *, skip_agent: bool) -> str | None:
    """The ``model`` a manifest may claim. ``None`` under ``--skip-agent``.

    ``model`` is a comparability key, and a run that never called a model has none.
    Writing the configured name anyway made a smoke run match a real one on every
    key, so ``comparable()`` paired them — the quotability gate stops such a run
    being *quoted*, but nothing stopped it being *paired*.

    Lives here, beside the keys, because both drivers write this field into the same
    ledger and they had drifted: the pooled driver cleared it and ``run_experiment``
    did not. One definition is the only way that stays true.
    """
    return None if skip_agent else model_name


# --------------------------------------------------------------------------- #
# Building a record from a finished run directory
# --------------------------------------------------------------------------- #


def _read_json(path: Path) -> dict[str, Any] | None:
    """Parsed JSON object, or ``None`` when the file is absent or unreadable.

    ``None`` rather than ``{}``: an empty dict makes every field read as "not
    recorded", which ``comparable()`` then treats as matching (``None == None``),
    so two runs whose configuration is simply *unknown* would be declared the same
    experiment. The caller has to be able to tell "no manifest" from "a manifest
    that says nothing".
    """
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return loaded if isinstance(loaded, dict) else None


def _resume_drift(manifest: dict[str, Any]) -> list[str]:
    """Knobs whose value changed across a resume of this run.

    Reads the `resumes` list that `_merge_resume_manifest` appends. A directory
    resumed under a different model or shortlist size holds rows scored both ways,
    and the top-level manifest shows only the first — so the drift has to be
    surfaced here or the run reads as internally consistent when it is not.

    Iterates :data:`RESUME_DRIFT_KEYS`, not :data:`COMPARABILITY_KEYS`: a changed
    ``git_sha`` or ``skip_agent`` is unremarkable *between* runs and corrupting
    *within* one. See that tuple for why the distinction is the whole point.
    """
    resumes = manifest.get("resumes")
    if not isinstance(resumes, list):
        return []
    drifted: set[str] = set()
    for attempt in resumes:
        if not isinstance(attempt, dict):
            continue
        for key, label in RESUME_DRIFT_KEYS:
            was, now = manifest.get(key), attempt.get(key)
            if now is not None and was != now:
                drifted.add(label)
    return sorted(drifted)


def _undelivered(summary: dict[str, Any]) -> list[str]:
    """Reasons this run's arms cannot be compared, from its treatment checks.

    Silent when the run predates the checks. That is deliberate and is the one place
    this module does *not* fail closed: retro-flagging every archived run as
    undelivered would bury the genuine cases in noise. The absence shows up instead
    as a missing ``treatment_divergence`` key, which ``render_index`` marks.
    """
    reasons: list[str] = []
    pairs = [p for p in (summary.get("treatment_divergence") or []) if isinstance(p, dict)]
    fair_pairs = [p for p in pairs if not p.get("diagnostic_pair")]
    for pair in fair_pairs:
        # A pair involving a counterfactual oracle rung is a diagnostic. If it did
        # not diverge, that rung's own number is meaningless and the artifact says
        # so — but it does not make the fair ladder's comparisons unquotable, which
        # are the results the run exists to produce.
        if pair.get("treatment_delivered") is False:
            reasons.extend(pair.get("reasons") or [])

    # ...unless there IS no fair ladder. The exemption above assumes the diagnostic
    # pairs sit beside real comparisons that carry the run's result. In a rung-only
    # run — ``--arms baseline --oracle ...``, which is exactly what the runbook's
    # step 3 prescribes — every pair is diagnostic, so the gate went completely
    # inert and the run reported ``quotable: true`` while its only product, the
    # headroom bounds, was measuring nothing. Verified on a synthetic record whose
    # rungs delivered byte-identical context to the base arm on all 2030 questions.
    if pairs and not fair_pairs:
        broken = [p for p in pairs if p.get("treatment_delivered") is False]
        for pair in broken:
            reasons.extend(
                f"{r} (the run has no non-diagnostic comparison, so this is its result)"
                for r in (pair.get("reasons") or ["treatment not delivered"])
            )

    for arm, arm_summary in (summary.get("arms") or {}).items():
        if not isinstance(arm_summary, dict):
            continue
        treatment = arm_summary.get("treatment")
        if not isinstance(treatment, dict):
            continue
        # Both of ``treatment.treatment_reasons``' arm-level checks, not just the
        # notes one. An arm whose rows recorded no delivery fields at all cannot have
        # its numbers attributed to its corpus, and that check existed but was never
        # read here — so a run with a dead provenance relay passed the gate.
        n_rows = treatment.get("n_rows")
        if n_rows and not treatment.get("n_rows_observed"):
            reasons.append(
                f"arm {arm} recorded no delivery fields on any of its {n_rows} rows "
                "— what reached the model is unknown, so its numbers cannot be "
                "attributed to its corpus"
            )
            continue
        corpus_notes = treatment.get("corpus_note_assets")
        if corpus_notes and not treatment.get("n_notes_injected"):
            reasons.append(
                f"arm {arm} served a corpus of {corpus_notes} notes and injected none"
            )
    return reasons


def record_for_run(run_dir: Path | str) -> dict[str, Any]:
    """Flatten one run directory's manifest + summary into a ledger record.

    Reads only artifacts, never a database or a model, so it can be run over an
    archive of old runs as easily as over a fresh one. Missing fields stay
    ``None`` rather than defaulting: a record that cannot distinguish "zero" from
    "never measured" is what made the previous numbers unrecoverable.
    """
    run_dir = Path(run_dir)
    raw_manifest = _read_json(run_dir / "manifest.json")
    raw_summary = _read_json(run_dir / "summary.json")
    manifest = raw_manifest or {}
    summary = raw_summary or {}
    arms = summary.get("arms") or {}

    headline: dict[str, Any] = {}
    for arm, s in arms.items():
        if not isinstance(s, dict):
            continue
        headline[arm] = {
            "n": s.get("n"),
            "ex_lenient": s.get("ex_lenient"),
            "ex_gradeable": s.get("ex_gradeable"),
            "refusal_rate": s.get("refusal_rate"),
            "crash_rate": s.get("crash_rate"),
            "routing_recall": s.get("routing_recall"),
            "schema_pick_accuracy": s.get("schema_pick_accuracy"),
            # Resume that deleted crashed rows and re-served them (audit E1).
            "n_re_served": s.get("n_re_served"),
            # Rows scored correct for a reason other than good SQL (audit E2): an
            # empty gold result, a prediction with no FROM, zero table overlap.
            "n_correct_with_empty_gold": s.get("n_correct_with_empty_gold"),
            "n_correct_and_pred_has_no_from": s.get("n_correct_and_pred_has_no_from"),
            "n_correct_and_zero_table_overlap": s.get("n_correct_and_zero_table_overlap"),
        }

    record = {
        "run_dir": str(run_dir).replace("\\", "/"),
        # Lifted so `comparable()` can tell a manifest that GUARANTEES every knob is
        # present from one that merely happens to carry the knobs it recorded. Without
        # it the None-on-both-sides rule is applied to records that cannot support it.
        "manifest_schema_version": manifest.get("manifest_schema_version"),
        "mode": summary.get("mode") or manifest.get("mode"),
        "created_at_utc": manifest.get("created_at_utc"),
        "completed_at_utc": manifest.get("completed_at_utc"),
        "git_sha": manifest.get("git_sha") or manifest.get("corpus_release_hash"),
        "model": manifest.get("model"),
        "prompt_variants": manifest.get("prompt_variants"),
        "prompt_set_hash": manifest.get("prompt_set_hash"),
        "split": summary.get("split") or manifest.get("split"),
        "arms": sorted(arms) or list(summary.get("arms_run") or []),
        "n_questions": summary.get("n_questions") or summary.get("n_test"),
        "n_dbs": summary.get("n_dbs_built"),
        "route_top_k": manifest.get("route_top_k"),
        "route_llm_pick": manifest.get("route_llm_pick"),
        "schema_pick_max_columns": manifest.get("schema_pick_max_columns"),
        "use_embedder": manifest.get("use_embedder"),
        "serve_workers": manifest.get("serve_workers"),
        "headline": headline,
        # Kept verbatim so `quotable` can explain itself without re-reading files.
        "build_errors": sorted((summary.get("build_errors") or {}).keys()),
        "curator_error_keys": sorted((summary.get("curator_errors") or {}).keys()),
        # Knobs that differed between the original run and a later `--resume` of it.
        # `_merge_resume_manifest` keeps the ORIGINAL values at the top level and
        # files each resume's under `resumes`, so without this the record — and
        # therefore `comparable()` — describes only the first half of a directory
        # whose rows were scored under two configurations.
        "resumed_with_drift": _resume_drift(manifest),
        # Arm pairs whose delivered context did not actually differ, and arms whose
        # treatment reached no prompt. Read from the summary the run already wrote,
        # so this works over an archive as well as a fresh run.
        "treatment_not_delivered": _undelivered(summary),
        # Whether the configuration was readable at all. Without this, an
        # unreadable manifest leaves every comparability knob ``None``, and
        # ``comparable()`` reads two configuration-unknown runs as matching.
        "manifest_readable": raw_manifest is not None,
        # --- signals the run already computed and the ledger used to drop -------- #
        # Each of these was written into summary.json, printed as a warning at most,
        # and never consulted by the gate. A run could therefore be marked quotable
        # while its own artifact recorded a corpus full of dangling references, an
        # SME arm that folded nothing, or few-shots drawn from the test split. The
        # detectors existed; nothing read them. That is the shape of every defect
        # this ledger was built to end, reproduced one layer up.
        #
        # ``corpus_validation`` -> per-arm reference-integrity finding counts. A note
        # whose scope can never match is a ``dangling-ref`` here, and that exact
        # defect once silently zeroed 9,154 notes.
        "corpus_finding_counts": {
            arm: block.get("finding_count")
            for arm, block in (summary.get("corpus_validation") or {}).items()
            if isinstance(block, dict) and block.get("finding_count")
        },
        # ``sme_fold`` -> the SME arm produced a corpus byte-identical to the arm it
        # is supposed to improve on. Its EX equals ``curated`` by construction, not
        # by measurement.
        "sme_noop_dbs": sorted(
            db
            for db, fold in (summary.get("sme_fold") or {}).items()
            if isinstance(fold, dict) and fold.get("identical_to_curated")
        ),
        # ``leakage`` -> train/test contamination found before scoring.
        "leakage": summary.get("leakage") or {},
        # A ``--skip-agent`` run serves a refuse-all solver: every arm scores 0 by
        # construction. The runbook asks for one immediately before every real run, so
        # these accumulate in the ledger — and they were landing there marked quotable.
        "skip_agent": manifest.get("skip_agent"),
        # ``gold_hash_self_check`` -> schemas whose gold would not execute, so the
        # grader was never confirmed against them. Below the abort threshold the run
        # proceeds (one slow query must not make the split unrunnable), but a score for
        # a schema whose gold nothing verified is not a score anyone should quote.
        # Schemas the run asked for and Postgres did not have. A 40-schema result is
        # not the 69-schema benchmark however internally consistent it is, and this used
        # to be a console warning only.
        "dbs_absent_from_postgres": sorted(
            summary.get("dbs_absent_from_postgres") or []
        ),
        "n_dbs_requested": summary.get("n_dbs_requested"),
        "gold_unverified_dbs": sorted(
            (summary.get("gold_hash_self_check") or {}).get("exec_error_dbs") or {}
        ),
        # Per-arm counts of crashed turns deleted and re-served on resume. A non-zero
        # total launders ``crash_rate`` back to zero (audit E1); ``quotable`` refuses it.
        "n_re_served_by_arm": {
            arm: s.get("n_re_served")
            for arm, s in arms.items()
            if isinstance(s, dict) and s.get("n_re_served")
        },
    }
    ok, reasons = quotable(record)
    _attach_hygiene_and_claim_fields(record, ok=ok, reasons=reasons)
    return record


# --------------------------------------------------------------------------- #
# The two rules
# --------------------------------------------------------------------------- #


#: Below this, a comparison between two arms cannot reach significance whatever the
#: outcome, so the run's numbers are not a result under any reading.
#:
#: Derived, not chosen, and derived against the rule the runbook actually applies.
#: The paired test is an exact two-sided binomial on the discordant pairs: with ``d``
#: discordant pairs all falling one way, ``p = 2 * 0.5**d``. Raw, that first clears
#: 0.05 at ``d = 6`` — but the pre-quote checklist requires ``p_value_holm``, and the
#: default ladder is four arms, so Holm's multiplier on the most significant test is
#: ``C(4,2) = 6``::
#:
#:     d=6  raw 0.03125   holm 0.1875
#:     d=7  raw 0.015625  holm 0.09375
#:     d=8  raw 0.007812  holm 0.046875   <- first clear
#:
#: Discordant pairs cannot exceed the question count, so a run of fewer than 8
#: questions is arithmetically incapable of producing a hygiene-ok comparison under
#: the default four-arm family. The ledger was carrying 2- and 4-question smoke runs
#: marked as if they were publishable and reporting them COMPARABLE to real runs on
#: every configuration key.
#:
#: This is a floor for the DEFAULT four-arm family, and it is not a sufficiency test
#: for claim readiness. A wider ladder is a larger Holm family and needs more (five
#: arms is ten tests, so ``d = 9`` — see :func:`arithmetic_floor_for_arms`). Clearing
#: this says only that the run is not impossible to interpret as ledger hygiene;
#: replicate / MDE / Holm / cluster / single-variable / twin conditions live in the
#: experiment-runbook checklist and are **not** encoded here.
MIN_QUOTABLE_QUESTIONS = 8

#: What a published claim still needs beyond ledger/artifact hygiene. The index never
#: sets ``claim_ready: true`` — those checks require replicate/MDE/Holm/cluster/
#: single-variable/twin context the ledger does not recompute.
CLAIM_READY_REQUIRES: tuple[str, ...] = (
    "ledger_ok (artifact / hygiene gate — this record's quotable/ledger_ok flag)",
    "serve-replicate noise floor measured and not drifted",
    "quoted delta clears the run's MDE",
    "Holm-adjusted significance on the comparison family",
    "cluster sign-test does not contradict the question-level claim",
    "single-variable ladder step (or bundles disclosed and not quoted as one mechanism)",
    "twin strata only under full gold_twin_in_train stamp coverage",
)


def holm_family_size(n_arms: int) -> int:
    """Holm family size for all pairwise arm comparisons: ``C(n_arms, 2)``."""
    if n_arms < 2:
        return 0
    return n_arms * (n_arms - 1) // 2


def arithmetic_floor_for_arms(n_arms: int) -> int:
    """Smallest question count that can clear Holm α=0.05 for a full pairwise family.

    Same derivation as :data:`MIN_QUOTABLE_QUESTIONS` (four arms → 8). Five arms need
    9; six need 10. Returns :data:`MIN_QUOTABLE_QUESTIONS` when the arm count is
    unknown or below two.
    """
    family = holm_family_size(n_arms)
    if family <= 0:
        return MIN_QUOTABLE_QUESTIONS
    d = 1
    while True:
        if 2.0 * (0.5**d) * family < 0.05:
            return d
        d += 1


def _arms_for_family(record: dict[str, Any]) -> list[str]:
    """Fair arms that define the Holm family, excluding serve-replicate controls."""
    raw = list(record.get("arms") or [])
    if not raw:
        headline = record.get("headline") or {}
        raw = list(headline) if isinstance(headline, dict) else []
    return [a for a in raw if "__replicate" not in str(a)]


def _attach_hygiene_and_claim_fields(
    record: dict[str, Any], *, ok: bool, reasons: list[str]
) -> None:
    """Stamp ledger/hygiene aliases and the explicit non-claim-ready contract."""
    record["quotable"] = ok
    record["not_quotable_because"] = reasons
    # Backward-compatible aliases: ``quotable`` remains, but operator-facing copy
    # should read these as ledger/artifact hygiene, not "publishable".
    record["ledger_ok"] = ok
    record["hygiene_ok"] = ok
    record["not_ledger_ok_because"] = list(reasons)
    arms = _arms_for_family(record)
    n_arms = len(arms)
    family = holm_family_size(n_arms) if n_arms else None
    floor = arithmetic_floor_for_arms(n_arms) if n_arms else MIN_QUOTABLE_QUESTIONS
    n_questions = record.get("n_questions")
    record["n_arms_for_family"] = n_arms or None
    record["holm_family_size"] = family
    record["arithmetic_floor_questions"] = floor
    record["floor_assumes_default_four_arm_family"] = n_arms < 2
    record["floor_sufficient_for_family"] = (
        None
        if n_questions is None
        else bool(n_questions >= floor)
    )
    # Never auto-computed: claim readiness needs the runbook checklist.
    record["claim_ready"] = False
    record["claim_ready_requires"] = list(CLAIM_READY_REQUIRES)
    if ok:
        record["claim_ready_blocked_because"] = [
            "ledger_ok is hygiene only — claim readiness is the experiment-runbook "
            "checklist (replicate, MDE, Holm, cluster, single-variable, twin), which "
            "this index does not evaluate"
        ]
    else:
        record["claim_ready_blocked_because"] = [
            "ledger_ok is false; fix hygiene before any claim checklist",
            *reasons,
        ]


def quotable(record: dict[str, Any]) -> tuple[bool, list[str]]:
    """Is this run's artifact hygiene good enough to *consider* quoting?

    This is **ledger / artifact hygiene**, not statistical claim readiness.
    ``True`` means crashes, build errors, resume drift, treatment delivery, and
    the arithmetic floor cleared — it does **not** mean replicate / MDE / Holm /
    cluster / single-variable / twin conditions from the runbook are satisfied.
    Prefer the aliases ``ledger_ok`` / ``hygiene_ok`` in operator copy; ``claim_ready``
    is never set true by this module.

    Returns ``(ok, reasons_it_is_not)``. Fails closed on unknowns: a run that did
    not record whether it crashed has not shown that it didn't.
    """
    reasons: list[str] = []

    # Explicitly `is False` — a record built before this field existed says nothing
    # about its manifest and must not be failed on the absence.
    if record.get("manifest_readable") is False:
        reasons.append(
            "manifest.json is missing or unreadable, so every configuration knob is "
            "unknown — the run cannot be compared to anything, including itself"
        )

    if record.get("skip_agent"):
        reasons.append(
            "run with --skip-agent: no model was called, so every fair arm refused and "
            "scores 0 by construction. (An oracle_sql rung under --skip-agent is still "
            "meaningful — it submits gold SQL to the grader — but it is the grader's "
            "ceiling, not a system result.)"
        )

    if str(record.get("split")) == "train":
        reasons.append(
            "scored on the train split, which the curator read — a diagnostic, not a result"
        )

    arms = _arms_for_family(record)
    n_arms = len(arms)
    floor = arithmetic_floor_for_arms(n_arms) if n_arms else MIN_QUOTABLE_QUESTIONS
    family = holm_family_size(n_arms) if n_arms else holm_family_size(4)
    n_questions = record.get("n_questions")
    if n_questions is None:
        reasons.append("run recorded no question count, so its size is unknown")
    elif n_questions < floor:
        if n_arms >= 2:
            reasons.append(
                f"{n_questions} questions is below the arithmetic floor of {floor} "
                f"for a {n_arms}-arm Holm family of {family} tests "
                f"(default four-arm floor is {MIN_QUOTABLE_QUESTIONS}) — see "
                "arithmetic_floor_for_arms"
            )
        else:
            reasons.append(
                f"{n_questions} questions is below the arithmetic floor of "
                f"{MIN_QUOTABLE_QUESTIONS} — see MIN_QUOTABLE_QUESTIONS "
                "(arm count unknown, so the default four-arm floor was used; a wider "
                "family needs a higher floor)"
            )

    headline = record.get("headline") or {}
    if not headline:
        reasons.append("no per-arm summary recorded")
    else:
        crashed = {
            arm: s.get("crash_rate")
            for arm, s in headline.items()
            if isinstance(s, dict) and s.get("crash_rate")
        }
        if crashed:
            detail = ", ".join(f"{a}={v}" for a, v in sorted(crashed.items()))
            reasons.append(f"arms crashed during serve ({detail})")
        re_served = {
            arm: s.get("n_re_served")
            for arm, s in headline.items()
            if isinstance(s, dict) and s.get("n_re_served")
        }
        if re_served:
            detail = ", ".join(f"{a}={v}" for a, v in sorted(re_served.items()))
            reasons.append(
                f"resume re-served crashed turns ({detail}) — those draws were "
                "resampled after failure, so crash_rate no longer describes the "
                "original sample; use --replay-crashed to keep crashes visible, or "
                "start a fresh run"
            )
        # Free passes cut the OTHER way from crashes: they inflate a positive result.
        # The counters existed and fed no gate, so the harness could block a negative
        # result and never a flattering one (AUDIT E2 / C8). A free pass on more than
        # a tenth of the scored questions means EX is not measuring better SQL.
        _FREE_PASS_KEYS = (
            "n_correct_with_empty_gold",
            "n_correct_and_pred_has_no_from",
            "n_correct_and_zero_table_overlap",
        )
        inflated: list[str] = []
        unmeasured_free_pass: list[str] = []
        for arm, s in sorted(headline.items()):
            if not isinstance(s, dict):
                continue
            n = s.get("n") or 0
            # An ABSENT counter is not a measured zero. ``s.get(k) or 0`` read the two
            # the same way, so a run whose free-pass rate was never computed passed this
            # gate silently — the exact asymmetry the ``crash_rate is None`` check below
            # exists to prevent, on the counter that guards a FLATTERING result instead
            # of a damning one. Fails closed now, like its neighbour.
            missing = [k for k in _FREE_PASS_KEYS if s.get(k) is None]
            if missing:
                unmeasured_free_pass.append(f"{arm} ({', '.join(missing)})")
                continue
            worst = max(int(s[k]) for k in _FREE_PASS_KEYS)
            if n and worst / n > FREE_PASS_QUOTABLE_FRACTION:
                inflated.append(f"{arm}={worst}/{n}")
        if unmeasured_free_pass:
            reasons.append(
                "free-pass counters not recorded for "
                + "; ".join(unmeasured_free_pass)
                + " — so it is unknown whether the correct rows are real answers or "
                "empty gold / no-FROM / zero-table-overlap free passes"
            )
        if inflated:
            reasons.append(
                f"free passes dominate the correct rows ({', '.join(inflated)}) — more "
                f"than {FREE_PASS_QUOTABLE_FRACTION:.0%} of an arm's questions scored "
                "correct with empty gold, no FROM clause, or zero table overlap, so EX "
                "does not distinguish better SQL from more over-filtering"
            )
        unmeasured = sorted(
            arm
            for arm, s in headline.items()
            if not isinstance(s, dict) or s.get("crash_rate") is None
        )
        if unmeasured:
            reasons.append(
                "crash rate not recorded for "
                + ", ".join(unmeasured)
                + " — predates the crash/refusal split, so its refusal_rate and EX "
                "silently absorb any crashes"
            )

    if record.get("resumed_with_drift"):
        reasons.append(
            "resumed under changed "
            + ", ".join(record["resumed_with_drift"])
            + " — its rows were scored under more than one configuration, and the "
            "manifest reports only the first"
        )

    if record.get("build_errors"):
        reasons.append(
            "dbs failed to build: " + ", ".join(record["build_errors"])
        )
    if record.get("curator_error_keys"):
        reasons.append(
            "curator build errors on: " + ", ".join(record["curator_error_keys"])
        )

    # An arm pair that delivered the model identical context is not a measured null
    # result — it is one experiment run twice, and the difference between its scores
    # is sampling noise. Two interventions on this project were reported as nulls on
    # exactly this footing before anyone checked, so it disqualifies the run here
    # rather than relying on a reader to notice.
    reasons.extend(record.get("treatment_not_delivered") or [])

    # A corpus that does not pass its own reference check was still scored. The
    # findings were computed, written to summary.json, printed as a warning, and
    # never read by anything that could stop the number being quoted — which is how
    # a corpus whose 9,154 notes all had unmatchable scopes produced a published
    # result. A dangling reference is not a style issue: an asset nothing resolves to
    # is an asset that never reaches a prompt.
    findings = record.get("corpus_finding_counts") or {}
    if findings:
        detail = ", ".join(f"{arm}={n}" for arm, n in sorted(findings.items()))
        reasons.append(
            f"corpus reference-integrity findings ({detail}) — assets that resolve to "
            "nothing cannot reach a prompt, so the arm did not serve what it holds"
        )

    # The SME arm produced a corpus byte-identical to the arm it is meant to improve
    # on. Its EX equals `curated` by construction; any difference is noise. This is
    # the incident that ran for weeks, and the detector for it was a print statement.
    noop = record.get("sme_noop_dbs") or []
    if noop:
        reasons.append(
            "curated_sme folded nothing on "
            + ", ".join(noop[:10])
            + (" (+more)" if len(noop) > 10 else "")
            + " — its corpus is identical to curated there, so the SME delta on "
            "those dbs is not a measurement"
        )

    # Schemas the run asked for that Postgres did not have. The one kind of attrition no
    # gate in the driver can catch: a missing schema never enters ``wanted``, and both
    # the build-coverage check and the gold share measure against that already-filtered
    # list. So a default run against a partially-loaded Postgres scores a fraction of the
    # split and reports full coverage of what it attempted.
    absent = record.get("dbs_absent_from_postgres") or []
    if absent:
        requested = record.get("n_dbs_requested")
        scale = f" of {requested}" if requested else ""
        reasons.append(
            f"{len(absent)}{scale} requested schema(s) were not on Postgres and went "
            "unscored ("
            + ", ".join(absent[:10])
            + (" +more" if len(absent) > 10 else "")
            + ") — the pool measured is smaller than the pool requested, so this is not "
            "the benchmark it names"
        )

    # Gold that would not execute on some schemas. The run was allowed to proceed — the
    # abort threshold is deliberately proportional, because one query crossing the
    # gateway timeout must not make the whole split unrunnable — but a score for a schema
    # whose gold nothing ever confirmed is not a number to quote, and the warning that
    # said so went to a console that scrolls.
    unverified = record.get("gold_unverified_dbs") or []
    if unverified:
        reasons.append(
            "gold would not execute on "
            + ", ".join(unverified[:10])
            + (" (+more)" if len(unverified) > 10 else "")
            + " — the grader was never confirmed against those schemas, so their "
            "contribution to every arm's score is unverified"
        )

    # Explicit negative only, matching `manifest_readable` above: a run that never
    # recorded the check is not accused of failing it, but one that recorded a
    # failure cannot be quoted.
    if (record.get("leakage") or {}).get("train_test_disjoint") is False:
        reasons.append(
            "train and test question ids overlap — scored questions were in the "
            "curator's own input, and no downstream metric can see that"
        )

    return (not reasons), reasons


def comparable(a: dict[str, Any], b: dict[str, Any]) -> tuple[bool, list[str]]:
    """May these two runs be compared?

    Returns ``(ok, differences)``. A difference in any comparability key means the
    independent variable is not what a reader would assume, so the pair is
    reported as incomparable with the offending knobs named.

    A knob that is ``None`` on both sides counts as matching — two runs that both
    predate a knob did not differ in it. A knob recorded on one side and not the
    other is a genuine difference, because one of them is unknown.

    That rule is only sound where the manifest GUARANTEES every knob is present, which
    is what ``manifest_schema_version`` records. Without the guarantee, "``None`` on
    both sides" cannot be told apart from "neither run ever recorded this" — so a pair
    of pre-guarantee records would be reported as agreeing on knobs they never wrote
    down, which is the exact failure the guarantee was added to end. Such a pair is
    refused here rather than passed, because the previous behaviour was to pass it.
    """
    diffs: list[str] = []
    # A run whose manifest could not be read has no configuration to match on, so
    # every knob is None and every comparison would trivially succeed.
    for side, rec in (("a", a), ("b", b)):
        if rec.get("manifest_readable") is False:
            diffs.append(f"run {side}'s manifest is unreadable, so its knobs are unknown")
    # Explicitly a REFUSAL, not a warning. A record with no version stamp predates the
    # presence guarantee, so every knob it omitted reads as "we agree" below.
    for side, rec in (("a", a), ("b", b)):
        if rec.get("manifest_schema_version") is None:
            diffs.append(
                f"run {side} records no manifest_schema_version (current: "
                f"{MANIFEST_SCHEMA_VERSION}), so it predates the guarantee that every "
                "knob is present — a knob it never recorded would be read below as "
                "agreement. Re-index it with `--reindex` if its manifest carries the "
                "field, or treat the pair as incomparable"
            )
    # ``"unknown"`` is a sentinel, not a digest — ``corpus_content_hash`` returns it when
    # there was no corpus tree to read. It compares EQUAL to itself, so two runs over two
    # different missing corpora agreed on the one field whose job is being the treatment's
    # identity (AUDIT E5), and the pair passed with zero diffs. Refused here for the same
    # reason ``manifest_schema_version`` is: an unknown must never read as agreement.
    for side, rec in (("a", a), ("b", b)):
        if rec.get("corpus_content_hash") == CORPUS_HASH_UNKNOWN:
            diffs.append(
                f"run {side}'s corpus_content_hash is {CORPUS_HASH_UNKNOWN!r} — no corpus "
                "tree was readable when its manifest was written, so the treatment has no "
                "recorded identity and cannot be matched against anything"
            )
    for key, label in COMPARABILITY_KEYS:
        av, bv = a.get(key), b.get(key)
        if av is None and bv is None:
            continue
        if av != bv:
            diffs.append(f"{label}: {av!r} vs {bv!r}")
    return (not diffs), diffs


# --------------------------------------------------------------------------- #
# Ledger I/O
# --------------------------------------------------------------------------- #


def load_index(path: Path | str = DEFAULT_INDEX) -> list[dict[str, Any]]:
    """Every record in the ledger, oldest first, skipping unreadable lines."""
    path = Path(path)
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except ValueError:
                continue  # truncated tail; a torn ledger must not block a run
    return out


@contextmanager
def _ledger_lock(path: Path, *, timeout_s: float = 30.0):
    """Hold an exclusive inter-process lock on the ledger, or raise.

    Two eval runs finishing at once is ordinary (a baseline in one terminal, a
    ``--prompt`` variant in another), and the upsert below is a
    read-modify-rewrite. Unsynchronised, the second writer's stale snapshot
    overwrites the first's record *and* any run indexed in between — measured at
    16 of 17 records destroyed under 12 concurrent writers, with no exception
    raised. A ledger that silently fails to record a run is precisely the failure
    the ledger exists to prevent, so this raises rather than degrades.

    ``O_CREAT | O_EXCL`` is the portable primitive here: ``fcntl`` is POSIX-only
    and ``msvcrt`` Windows-only, and this repo runs on both.
    """
    lock = path.with_suffix(path.suffix + ".lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout_s
    fd = None
    last_err: OSError | None = None
    while True:
        try:
            fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        # ``PermissionError`` as well as ``FileExistsError``. On Windows, opening a lock
        # file another writer is unlinking raises ``PermissionError`` (the delete is
        # pending, so the name resolves but the open is refused) rather than
        # ``FileExistsError`` — so it escaped this loop and propagated out of
        # ``append_run``, and a finished run was never indexed. That is the exact loss the
        # ledger exists to prevent, on the platform this is developed on: reproduced
        # across concurrent writers, and it is why this module's own
        # ``test_concurrent_appends_do_not_lose_records`` was intermittently red.
        except (FileExistsError, PermissionError) as err:
            last_err = err
            if time.monotonic() >= deadline:
                # Chained, not ``from None``. Broadening the catch to
                # ``PermissionError`` also swallowed the non-contention kind — a deny
                # ACL on the directory used to surface immediately as ``WinError 5``
                # and now spends 30s pretending to be contention. The advice below is
                # right for a stale lock and wrong for a permission problem, so the
                # cause has to travel with it.
                raise TimeoutError(
                    f"could not lock {lock} within {timeout_s}s. If no other run is "
                    f"writing the ledger, delete {lock} — it is a stale lock from a "
                    "killed process. If it cannot be created at all, the chained "
                    "error below is the real cause."
                ) from last_err
            time.sleep(0.05)
    try:
        yield
    finally:
        os.close(fd)
        try:
            lock.unlink()
        except OSError:  # already gone; nothing to release
            pass


def append_run(
    record: dict[str, Any],
    path: Path | str = DEFAULT_INDEX,
    *,
    lock_timeout_s: float = 30.0,
) -> Path:
    """Upsert one record into the ledger, keyed by ``run_dir``.

    Upsert rather than append: a resumed run is written twice and must leave one
    row, or the ledger's own count of runs becomes a count of invocations.

    Locked and written atomically through :func:`governed_bi.eval.atomic.atomic_write_text`.
    The rewrite is the whole file, so an unsynchronised writer loses other runs'
    records, and a kill mid-write would truncate the ledger rather than damage a tail.
    This path used to swap with a retry but write with a bare ``Path.write_text``, so
    the bytes were not synced before the swap; the shared writer has both halves.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _ledger_lock(path, timeout_s=lock_timeout_s):
        existing = [
            r for r in load_index(path) if r.get("run_dir") != record.get("run_dir")
        ]
        existing.append(record)
        text = "".join(
            json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in existing
        )
        atomic_write_text(path, text, timeout_s=lock_timeout_s)
    return path


def index_run(
    run_dir: Path | str, path: Path | str = DEFAULT_INDEX
) -> dict[str, Any]:
    """Build and store the ledger record for a finished run. Returns the record."""
    record = record_for_run(run_dir)
    append_run(record, path)
    return record


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #


def _fmt(value: Any, places: int = 3) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "y" if value else "n"
    if isinstance(value, float):
        return f"{value:.{places}f}"
    return str(value)


def _table(rows: list[list[str]], headers: list[str]) -> str:
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    line = "  ".join(h.ljust(widths[i]) for i, h in enumerate(headers)).rstrip()
    sep = "  ".join("-" * widths[i] for i in range(len(headers)))
    body = [
        "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)).rstrip()
        for row in rows
    ]
    return "\n".join([line, sep, *body])


def render_index(records: Iterable[dict[str, Any]]) -> str:
    """The ledger as text: one row per run x arm, then quotability, then pairs."""
    records = list(records)
    if not records:
        return "(no runs indexed yet)"

    headers = [
        "run",
        "split",
        "model",
        "prompts",
        "arm",
        "n",
        "EX",
        "EX_grad",
        "refuse",
        "crash",
        "route_rec",
        "pick_acc",
        "ok",
    ]
    rows: list[list[str]] = []
    for r in records:
        headline = r.get("headline") or {}
        run_label = Path(str(r.get("run_dir", "?"))).name
        prompts = r.get("prompt_set_hash")
        prompts = str(prompts)[:8] if prompts else "-"
        if not headline:
            rows.append(
                [
                    run_label,
                    _fmt(r.get("split")),
                    _fmt(r.get("model")),
                    prompts,
                    "(no arms)",
                    *["-"] * 7,
                    "n" if not r.get("quotable") else "y",
                ]
            )
            continue
        for arm in sorted(headline):
            s = headline[arm] if isinstance(headline[arm], dict) else {}
            rows.append(
                [
                    run_label,
                    _fmt(r.get("split")),
                    _fmt(r.get("model")),
                    prompts,
                    arm,
                    _fmt(s.get("n")),
                    _fmt(s.get("ex_lenient")),
                    _fmt(s.get("ex_gradeable")),
                    _fmt(s.get("refusal_rate")),
                    _fmt(s.get("crash_rate")),
                    _fmt(s.get("routing_recall")),
                    _fmt(s.get("schema_pick_accuracy")),
                    "y" if r.get("quotable") else "n",
                ]
            )

    parts = [_table(rows, headers)]

    not_ok = [r for r in records if not r.get("quotable")]
    if not_ok:
        parts.append(
            "\nNot ledger_ok (artifact hygiene — not the same as claim_ready):"
        )
        for r in not_ok:
            name = Path(str(r.get("run_dir", "?"))).name
            for reason in r.get("not_quotable_because") or ["(no reason recorded)"]:
                parts.append(f"  {name}: {reason}")
        parts.append(
            "\nclaim_ready is never set by this ledger; see claim_ready_requires "
            "on each record and the experiment-runbook checklist."
        )

    if len(records) > 1:
        parts.append("\nPairwise comparability:")
        for i, a in enumerate(records):
            for b in records[i + 1 :]:
                an = Path(str(a.get("run_dir", "?"))).name
                bn = Path(str(b.get("run_dir", "?"))).name
                ok, diffs = comparable(a, b)
                if ok:
                    parts.append(f"  {an} <-> {bn}: comparable")
                else:
                    parts.append(f"  {an} <-> {bn}: NOT comparable ({'; '.join(diffs)})")

    return "\n".join(parts)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def prune_index(
    path: Path | str = DEFAULT_INDEX, *, drop_outside_repo: bool = False
) -> list[str]:
    """Drop records whose run directory no longer exists. Returns what was dropped.

    With ``drop_outside_repo``, also drop records whose run directory is outside the
    repository. Those are scratch runs — smoke tests, review sessions, anything written
    under a temp dir — and they are unreachable the moment the directory is collected,
    so the record outlives the only thing that could verify it. Anticipating that is
    the same rule as the existence check, applied before the deletion rather than
    after: a review left this ledger at 116 records of which 78 pointed into a session
    scratchpad, so the file the runbook sends an operator to read was mostly other
    people's throwaway runs.

    A record outlives its run: scratch and temp directories get collected, and the
    row stays, unverifiable and still carrying a ``quotable`` verdict. Worse, the
    verdict was computed under whatever gates existed when it was written — the
    ``--skip-agent`` gate is forward-only, so rows that predate the manifest field
    sit in the ledger advertising a smoke run as quotable. Pruning the dead ones and
    re-indexing the live ones (``reindex_all``) puts every surviving verdict under
    the current rules.
    """
    path = Path(path)
    repo_root = Path.cwd().resolve()
    dropped: list[str] = []
    with _ledger_lock(path):
        kept = []
        for record in load_index(path):
            run_dir = record.get("run_dir")
            if run_dir and not Path(run_dir).exists():
                dropped.append(str(run_dir))
                continue
            if run_dir and drop_outside_repo:
                try:
                    outside = not Path(run_dir).resolve().is_relative_to(repo_root)
                except OSError:  # unresolvable path: treat as outside
                    outside = True
                if outside:
                    dropped.append(str(run_dir))
                    continue
            kept.append(record)
        if dropped:
            text = "".join(
                json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in kept
            )
            atomic_write_text(path, text, timeout_s=30.0)
    return dropped


def reindex_all(path: Path | str = DEFAULT_INDEX) -> tuple[list[str], list[str]]:
    """Rebuild every surviving record from its run directory.

    Returns ``(rebuilt, failed)``. Re-reads each ``manifest.json`` and
    ``summary.json``, so a verdict recorded before a gate existed is recomputed
    under the gate. Idempotent — ``append_run`` upserts on ``run_dir``.
    """
    rebuilt: list[str] = []
    failed: list[str] = []
    for record in load_index(path):
        run_dir = record.get("run_dir")
        if not run_dir or not Path(run_dir).exists():
            continue
        try:
            index_run(run_dir, path)
        except Exception as err:  # a half-written run must not stop the sweep
            failed.append(f"{run_dir}: {type(err).__name__}: {err}")
        else:
            rebuilt.append(str(run_dir))
    return rebuilt, failed


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument(
        "--index", type=Path, default=DEFAULT_INDEX, help="ledger path (JSONL)"
    )
    p.add_argument(
        "--add",
        type=Path,
        default=None,
        help="index this run directory before rendering",
    )
    p.add_argument(
        "--prune",
        action="store_true",
        help="drop records whose run directory no longer exists",
    )
    p.add_argument(
        "--prune-outside-repo",
        action="store_true",
        help="with --prune, also drop records whose run directory is outside this "
        "repository (scratch and smoke runs, unverifiable once collected)",
    )
    p.add_argument(
        "--reindex",
        action="store_true",
        help="recompute every surviving record from its run directory, so verdicts "
        "written before a gate existed are re-judged under it",
    )
    p.add_argument(
        "--quiet",
        action="store_true",
        help="skip the rendered table (it is O(n^2) in the pairwise block)",
    )
    args = p.parse_args(argv)

    if args.prune:
        dropped = prune_index(args.index, drop_outside_repo=args.prune_outside_repo)
        print(f"pruned {len(dropped)} record(s) with no run directory")
        for run_dir in dropped:
            print(f"  - {run_dir}")
        print()

    if args.reindex:
        rebuilt, failed = reindex_all(args.index)
        print(f"re-indexed {len(rebuilt)} record(s), {len(failed)} failed")
        for line in failed:
            print(f"  ! {line}")
        print()

    if args.add is not None:
        record = index_run(args.add, args.index)
        if record["quotable"]:
            ok = "ledger_ok (hygiene; not claim_ready)"
        else:
            ok = "NOT ledger_ok"
        print(f"indexed {record['run_dir']} ({ok})")
        for reason in record["not_quotable_because"]:
            print(f"  - {reason}")
        if record.get("claim_ready_blocked_because") and record["quotable"]:
            print("  claim_ready: false — checklist still required:")
            for item in record.get("claim_ready_requires") or []:
                print(f"    * {item}")
        print()

    if not args.quiet:
        print(render_index(load_index(args.index)))


if __name__ == "__main__":
    main()
