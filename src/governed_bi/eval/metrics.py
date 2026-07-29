"""The eval metric register: one declaration of every field a run records.

Three artifacts carry a run's meaning, and every one of them used to be an
undeclared dict built independently by each driver:

- the **manifest** (``manifest.json``) — the knobs and scope that decide what a
  scored row *means*. Read by name by :data:`governed_bi.eval.index.COMPARABILITY_KEYS`
  and :data:`~governed_bi.eval.index.RESUME_DRIFT_KEYS`.
- the **generation row** (``generations.<arm>.jsonl``) — one record per
  (question, arm).
- the **arm summary** (``summary.json``) — the aggregate, read by
  :func:`governed_bi.eval.index.quotable`.

Why a register rather than two builders
---------------------------------------
``comparable()`` skips a knob that is ``None`` on both sides, on the reasoning
that two runs which both predate a knob did not differ in it. That is right, and
it is also why a *missing* key is dangerous: an absent key is indistinguishable
from "both runs agree". The single-schema driver's manifest was missing ``split``
and ``corpus_content_hash``, so two of its runs over **different corpora on
different splits** compared as identical — and the comment on
``COMPARABILITY_KEYS`` calls ``corpus_content_hash`` out as the one thing the
check did not cover, because the corpus *is* the treatment. That fix had landed
in the pooled driver only, while the single-schema driver was the one whose
numbers were historically quoted.

:func:`build_manifest` is now the only way either mode builds one, and
:func:`validate_manifest` refuses a manifest that omits a gate key. A knob that
genuinely does not apply is recorded as ``None`` *explicitly*, alongside a flag
saying so, so "not applicable" and "not recorded" stop looking alike.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from ..prompts import prompt_set_hash
from ..provenance import corpus_content_hash, corpus_release_hash

Mode = Literal["single", "datalake"]


@dataclass(frozen=True)
class Metric:
    """One recorded field.

    ``denominator`` is the population a rate is computed over — the field that has
    caused the most defects in this harness, because a rate whose denominator
    quietly includes crashes or refusals reads backwards. ``None`` for anything
    that is not a rate.
    """

    name: str
    meaning: str
    denominator: str | None = None


# --------------------------------------------------------------------------- #
# Manifest
# --------------------------------------------------------------------------- #

#: Knobs that change what a scored row means. Every one of these must be present
#: in every manifest, in every mode — ``None`` when it does not apply, never absent.
MANIFEST_KNOBS: tuple[Metric, ...] = (
    Metric("split", "which BIRD split was scored"),
    Metric("model", "the configured serve model, or None under --skip-agent"),
    Metric("llm_temperature", "decoding temperature; None = provider default"),
    Metric("prompt_variants", "stage -> variant id map, for a human"),
    Metric("prompt_set_hash", "hash of the prompt TEXT, so an in-place edit moves it"),
    Metric("corpus_content_hash", "digest of the served corpora — the treatment itself"),
    Metric("git_sha", "the commit that produced the run"),
    Metric("route_top_k", "schema shortlist size; None when routing is bypassed"),
    Metric("route_llm_pick", "LLM picks one schema; None when routing is bypassed"),
    Metric("schema_pick_max_columns", "columns shown to the picker; None when bypassed"),
    Metric("use_embedder", "embedding channel on; None when routing is bypassed"),
    Metric("skip_agent", "no model was called at all"),
)

#: Scope: not knobs, but they decide which arms exist and which questions are in
#: the pool, so a resume that disagrees is a different experiment.
MANIFEST_SCOPE: tuple[Metric, ...] = (
    Metric("mode", "'single' (one pinned schema) or 'datalake' (pooled, unpinned)"),
    Metric("arms", "the arms served"),
    Metric("oracles", "oracle rungs served"),
    Metric("replicate_of", "the arm re-served to measure the noise floor"),
    Metric("db_ids", "schemas in the pool"),
    Metric("limit", "per-schema question cap"),
    Metric("limit_dbs", "schema cap"),
    Metric("question_scope_hash", "digest of the scored question-id set"),
    Metric("routing_bypassed", "True when one schema is pinned, so the router never ran"),
)

#: Recorded, deliberately NOT gate keys: these change how long a run takes, never
#: what a scored row means.
MANIFEST_OPERATIONAL: tuple[Metric, ...] = (
    Metric("bird_dir", "dataset directory"),
    Metric("created_at_utc", "when the run started"),
    Metric("pg_dsn_host", "host actually connected to"),
    Metric("serve_workers", "serve-loop concurrency"),
    Metric("build_workers", "curator-build concurrency"),
    Metric("max_agent_steps", "recursion limit on the agent loop"),
    Metric("serve_path", "always agent_core (ADR 0002)"),
    Metric("allow_git_sha_drift", "operator opted out of the resume git-sha guard"),
)

MANIFEST_FIELDS: tuple[Metric, ...] = (
    MANIFEST_KNOBS + MANIFEST_SCOPE + MANIFEST_OPERATIONAL
)


def build_manifest(
    *,
    mode: Mode,
    bird_dir: Path | str,
    split: str,
    model_name: str | None,
    prompt_variants: dict[str, str],
    skip_agent: bool,
    created_at_utc: str,
    # Routing. Pass None for all four when one schema is pinned: the router did not
    # run, and recording a default would claim it ran with that value.
    route_top_k: int | None,
    route_llm_pick: bool | None,
    schema_pick_max_columns: int | None,
    use_embedder: bool | None,
    # Scope
    arms: tuple[str, ...] = (),
    oracles: tuple[str, ...] = (),
    replicate_of: str | None = None,
    db_ids: list[str] | None = None,
    limit: int | None = None,
    limit_dbs: int | None = None,
    question_scope_hash: str | None = None,
    # Operational
    pg_dsn_host: str | None = None,
    serve_workers: int = 1,
    build_workers: int = 1,
    max_agent_steps: int | None = None,
    allow_git_sha_drift: bool = False,
    llm_temperature: float | None = None,
) -> dict[str, Any]:
    """The one manifest builder, for both modes.

    ``model_name`` is the CONFIGURED name, not a resolved value: ``manifest_model``
    is applied inside, so a caller cannot write a model name for a run that never
    called one. Taking the resolved value was the original drift — both drivers had
    to remember to apply the rule and one forgot, which let a smoke run be reported
    comparable to a real one.

    ``corpus_content_hash`` is declared ``None`` here and filled by
    :func:`stamp_corpus_hashes` after the build: the manifest is written before any
    corpus exists, because the gold pre-flight has to run before a model is paid
    for. Declared-then-filled rather than added later, because a gate key absent
    from the manifest can never fire.
    """
    from .index import manifest_model

    routing_bypassed = route_top_k is None and route_llm_pick is None

    return {
        # ── scope ──
        "mode": mode,
        "arms": list(arms),
        "oracles": list(oracles),
        "replicate_of": replicate_of,
        "db_ids": list(db_ids) if db_ids is not None else None,
        "limit": limit,
        "limit_dbs": limit_dbs,
        "question_scope_hash": question_scope_hash,
        "routing_bypassed": routing_bypassed,
        # ── knobs ──
        "split": split,
        "model": manifest_model(model_name, skip_agent=skip_agent),
        "llm_temperature": llm_temperature,
        "prompt_variants": dict(prompt_variants),
        "prompt_set_hash": prompt_set_hash(prompt_variants),
        "corpus_content_hash": None,
        "git_sha": corpus_release_hash(),
        "route_top_k": route_top_k,
        "route_llm_pick": route_llm_pick,
        "schema_pick_max_columns": schema_pick_max_columns,
        "use_embedder": use_embedder,
        "skip_agent": skip_agent,
        # ── operational ──
        "bird_dir": str(bird_dir),
        "created_at_utc": created_at_utc,
        "pg_dsn_host": pg_dsn_host,
        "serve_workers": serve_workers,
        "build_workers": build_workers,
        "max_agent_steps": max_agent_steps,
        "serve_path": "agent_core",
        "allow_git_sha_drift": allow_git_sha_drift,
    }


def stamp_corpus_hashes(
    manifest: dict[str, Any], roots_by_arm: dict[str, Path]
) -> str:
    """Fill ``corpus_content_hash`` from the corpora that were actually built.

    Returns the observed digest. Also records the per-arm digests, so a reader can
    see *which* arm's corpus moved rather than only that one did. On a resume the
    declared hash is left alone if it is already set — the caller compares them and
    decides whether the drift is fatal.
    """
    observed = corpus_content_hash([roots_by_arm[a] for a in sorted(roots_by_arm)])
    manifest["corpus_content_hash_observed"] = observed
    manifest["corpus_content_hash_by_arm"] = {
        arm: corpus_content_hash([root]) for arm, root in sorted(roots_by_arm.items())
    }
    if manifest.get("corpus_content_hash") is None:
        manifest["corpus_content_hash"] = observed
    return observed


def write_manifest(out_dir: Path, manifest: dict[str, Any]) -> None:
    """Validate, then write ``manifest.json`` atomically.

    One writer, because the resume guard reads this file back and a torn write
    silently disables that guard — the pooled driver wrote it three times per run
    with a plain ``write_text``, while ``index.append_run`` thirty lines away did the
    same job with a lock and an atomic replace. Validation happens here rather than
    at the call sites so a new write path cannot skip it.
    """
    import json
    import os

    validate_manifest(manifest)
    path = out_dir / "manifest.json"
    tmp = path.with_suffix(f".json.tmp{os.getpid()}")
    try:
        with tmp.open("w", encoding="utf-8") as fh:
            fh.write(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def validate_manifest(manifest: dict[str, Any]) -> None:
    """Refuse a manifest that omits a declared field, or a ledger gate key.

    A key that is *absent* is indistinguishable from "both runs agree" to
    :func:`governed_bi.eval.index.comparable`, which skips a knob that is ``None``
    on both sides. So absence is the failure mode this guards, not a wrong value.
    ``None`` is allowed and meaningful; a missing key is not.
    """
    from .index import COMPARABILITY_KEYS, RESUME_DRIFT_KEYS

    declared = {m.name for m in MANIFEST_FIELDS}
    missing_declared = sorted(declared - set(manifest))
    if missing_declared:
        msg = (
            "manifest omits declared fields "
            f"{missing_declared}; build it through metrics.build_manifest"
        )
        raise ValueError(msg)

    gate_keys = {k for k, _ in COMPARABILITY_KEYS} | {k for k, _ in RESUME_DRIFT_KEYS}
    missing_gates = sorted(gate_keys - set(manifest))
    if missing_gates:
        msg = (
            f"manifest omits ledger gate keys {missing_gates}: comparable() would "
            "read them as None on both sides and call two different configurations "
            "the same one"
        )
        raise ValueError(msg)


# --------------------------------------------------------------------------- #
# Generation row
# --------------------------------------------------------------------------- #

#: One record per (question, arm). Names only, grouped by concern — the meanings
#: live beside the code that computes them. Declared here so
#: ``tests/test_eval_metrics.py`` can assert the drivers emit exactly this set:
#: the row is consumed by ``.get()`` in eight modules, where a renamed key
#: degrades silently to ``None`` instead of raising.
ROW_IDENTITY: tuple[str, ...] = (
    "arm", "question_id", "request_id", "run_id", "turn_id", "db_id", "split",
    "difficulty",
)
ROW_VERDICT: tuple[str, ...] = (
    "correct", "correct_strict", "error", "error_type", "outcome", "failed_stage",
    "failed_layer", "refused_by", "nrows_match",
)
ROW_PREDICTION: tuple[str, ...] = (
    "generated_sql", "pred_nrows", "pred_ncols", "gold_nrows", "attempts",
    "tables_used", "tables_used_unresolved", "n_tables_used_unresolved",
    "licensed_tables",
)
ROW_GOVERNANCE: tuple[str, ...] = (
    "tier", "safety_clearance", "semantic_assurance", "graded_delivery",
    "coverage_best_effort", "decoy_touch", "by_guardrail_layer", "ledger_len",
    "n_tool_calls",
)
ROW_CONTEXT: tuple[str, ...] = (
    "context_chars", "context_hash", "injected_note_ids", "n_notes_injected",
    "n_caveats_injected", "n_few_shots_injected", "n_joins_injected",
    "n_metrics_injected", "n_terms_injected", "retrieved_tables",
)
ROW_ROUTING: tuple[str, ...] = (
    "routed_schemas", "routed_hit", "routing_bypassed", "routing_escaped",
    "routing_escape_unknown", "schema_pick", "schema_pick_fallback", "pick_hit",
    "shortlisted_schemas", "total_schemas",
)
ROW_LEAKAGE: tuple[str, ...] = (
    "gold_twin_in_train", "gold_frozen", "gold_order_sensitive", "gold_schema_rank",
)
ROW_ORACLE: tuple[str, ...] = (
    "oracle_rung", "oracle_applied", "oracle_gold_tables", "oracle_corpus_tables",
    "oracle_offered_tables", "oracle_padding_degenerate",
)
ROW_COST: tuple[str, ...] = (
    "latency_sec", "cost_est_usd", "usage", "token_usage", "token_sum",
)
ROW_PROVENANCE: tuple[str, ...] = ("prompt_set_hash", "prompt_variants")

ROW_FIELDS: tuple[str, ...] = (
    ROW_IDENTITY + ROW_VERDICT + ROW_PREDICTION + ROW_GOVERNANCE + ROW_CONTEXT
    + ROW_ROUTING + ROW_LEAKAGE + ROW_ORACLE + ROW_COST + ROW_PROVENANCE
)


# --------------------------------------------------------------------------- #
# Arm summary
# --------------------------------------------------------------------------- #

#: Every rate, with the population it is computed over. This is the register's
#: main job: the recurring defect class in this harness is a rate whose
#: denominator silently absorbs another outcome's failures, so an arm that
#: refuses more looks like an arm that governs better.
SUMMARY_RATES: tuple[Metric, ...] = (
    Metric("ex_lenient", "headline execution accuracy", "all scored rows (n)"),
    Metric("ex_strict", "EX under the strict normaliser", "all scored rows (n)"),
    Metric("ex_gradeable", "EX excluding un-gradeable gold", "gradeable rows"),
    Metric("ex_twin", "EX where the gold statement exists in train", "twin rows"),
    Metric("ex_no_twin", "EX with no train twin — the defensible headline", "twin-free rows"),
    Metric("conditional_ex_lenient", "EX among turns that produced SQL", "rows that produced SQL"),
    Metric("cond_ex_given_routing", "EX among correctly-routed turns", "rows the router hit"),
    Metric("refusal_rate", "GENUINE refusals; a crash is not a refusal", "all scored rows (n)"),
    Metric("crash_rate", "our bug, counted apart from refusals", "all scored rows (n)"),
    Metric("decoy_touch_rate", "predictions touching a suspect column", "rows that produced SQL"),
    Metric("safety_clearance_rate", "delivered answers that cleared the guardrails", "delivered rows"),
    Metric("graded_delivery_rate", "delivered answers served as unverified", "delivered rows"),
    Metric("coverage_best_effort_rate", "answers delivered on partial coverage", "delivered rows"),
    Metric("routing_recall", "router included the gold schema", "rows with a recorded routing decision"),
    Metric("routing_escape_rate", "SQL reached outside the routed schemas", "rows where escape was observable"),
    Metric("schema_pick_accuracy", "LLM picked the gold schema", "rows that recorded a pick"),
    Metric("schema_pick_accuracy_excl_fallback", "…excluding picker fallbacks", "picks that did not fall back"),
    Metric("share_with_a_note", "turns that received at least one note", "all scored rows (n)"),
)

#: Conditional diagnostics: blocks that report a rate on both sides of something
#: the corpus injected, so a per-arm number can say *which part* of the governance
#: is doing the work. Every input was already recorded per row and aggregated
#: against nothing before 2026-07-28.
#:
#: Each block carries its own ``n_*`` and, where a row can fail to record the
#: input, ``n_unstamped`` — an absent input is counted out, never filed on the
#: negative side. That is the same trap the twin strata document: ``not
#: r.get(...)`` puts an ABSENT key in the FALSE stratum, which silently turns one
#: side of the split into the pooled figure.
SUMMARY_CONDITIONALS: tuple[Metric, ...] = (
    Metric(
        "ex_by_semantic_assurance",
        "EX per assurance level — the calibration of the semantic axis. If "
        "`unflagged` does not out-score `heuristic`, the stamp is decoration.",
        "rows that recorded an assurance level",
    ),
    Metric(
        "ex_by_tier",
        "EX per display tier — the same calibration for the compact projection",
        "rows that recorded a tier",
    ),
    Metric(
        "decoy_touch_by_caveat",
        "decoy-touch rate with vs without an injected suspect caveat — whether the "
        "caveat is what stops the model reaching for the decoy",
        "delivered rows that recorded a caveat count",
    ),
    Metric(
        "ex_by_note_injected",
        "EX with vs without an injected note (ADR 0003's claim, previously unscored)",
        "rows that recorded a note count",
    ),
    Metric(
        "ex_by_repair",
        "EX after a repair (>1 run_query attempt) vs first-attempt — whether "
        "self-repair recovers correctness or just produces valid-but-wrong SQL",
        "rows that recorded an attempt count",
    ),
    Metric(
        "guardrail_cost_ceiling",
        "CEILING on answers a guardrail block may have cost, not the cost: blocked "
        "SQL cannot be graded without executing un-guardrailed SQL. Counts turns "
        "where a layer blocked and the turn still ended wrong. Note that "
        "`by_guardrail_layer` creates a key at 0 when a layer is merely evaluated, "
        "so blocked means `any(v > 0)`, never a truthiness test on the dict.",
        "rows where at least one layer blocked",
    ),
)

#: Counts. Each exists so an exclusion from some rate above stays visible: a rate
#: reported without its excluded count reads as full coverage.
SUMMARY_COUNTS: tuple[str, ...] = (
    "n", "n_answered", "n_correct", "n_refused", "n_crashed", "n_missing_gold",
    "n_gradeable", "n_gold_unusable", "n_frozen_gold", "n_order_sensitive_gold",
    "n_twin_gradeable", "n_no_twin_gradeable", "n_twin_unstamped",
    "n_gold_twin_in_train", "n_decoy_touch", "n_wrong_but_nrows_match",
    "n_unmapped_refused_by", "n_with_difficulty", "n_with_governance_stamp",
    "n_tables_used_unresolved", "n_rows_no_db_id", "n_pick_fallback",
    "n_routing_observed", "n_routing_bypassed", "n_routing_crashed",
    "n_routing_unrecorded", "n_routing_escaped", "n_routing_escape_observed",
    "n_routing_escape_unknown", "n_correct_routed", "n_correct_unrouted",
    "n_correct_bypassed", "n_correct_routing_crashed",
    "n_correct_routing_unrecorded", "n_correct_via_routing_escape",
    "n_correct_unaccounted", "n_safety_clearance_observed",
    "n_graded_delivery_observed", "n_coverage_best_effort_observed",
    # Grading free passes (audit E2): a correct answer that was correct for the
    # wrong reason. quotable() reads all three.
    "n_correct_with_empty_gold", "n_correct_and_pred_has_no_from",
    "n_correct_and_zero_table_overlap",
)

#: Means, and the nested breakdown blocks.
SUMMARY_MEANS: tuple[str, ...] = (
    "mean_attempts", "mean_context_chars", "mean_ledger_len",
    "mean_notes_injected", "mean_few_shots_injected",
)
SUMMARY_BLOCKS: tuple[str, ...] = (
    "arm", "question_ids", "treatment", "cost", "tool_calls", "errors",
    "by_db", "by_difficulty", "by_outcome", "by_failed_stage", "by_error_type",
    "by_guardrail_layer", "by_tier", "by_semantic_assurance", "by_gold_rank",
)

SUMMARY_FIELDS: tuple[str, ...] = (
    tuple(m.name for m in SUMMARY_RATES)
    + tuple(m.name for m in SUMMARY_CONDITIONALS)
    + SUMMARY_COUNTS
    + SUMMARY_MEANS
    + SUMMARY_BLOCKS
)
