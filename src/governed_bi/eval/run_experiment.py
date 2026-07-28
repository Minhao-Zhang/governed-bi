"""One-command eval-ladder accuracy experiment (plan W4/W5).

Run::

    uv run python -m governed_bi.eval.run_experiment \\
      --db cs_semester \\
      --bird-dir ../BIRD-Data-Obfuscation \\
      --pg-dsn "host=127.0.0.1 port=5435 dbname=bird user=bird password=bird" \\
      --out runs/
"""

from __future__ import annotations

import argparse
import json
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..config import DataSourceConfig, Environment, Settings, load_dotenv, load_settings
from ..corpus import load_corpus
from ..corpus.schemas import NoteAsset, ReliabilityStatus, TableAsset
from ..gateway import Gateway, Identity
from ..gateway.connectors.postgres import PostgresConnector
from ..prompts import (
    parse_cli_overrides,
    prompt_set_hash,
)
from ..prompts import (
    resolve as resolve_prompts,
)
from ..prompts import (
    text as prompt_text,
)
from ..provenance import corpus_release_hash
from ..stages import INFRA_ERROR_PREFIX, Outcome, Stage, classify_outcome
from .arms import _touches_suspect, agent_solver
from .bird_loader import description_dir, load_bird_items, load_rename_map
from .error_taxonomy import attribute_rows, summarise_attributions
from .hash_grade import (
    crosscheck_execution_match,
    free_pass_counts,
    load_gold_hashes,
    load_trap_columns,
    score_sql_hashes,
    validate_gold_hashes_live,
)
from .index import manifest_model
from .parallel import ServeWorker, resolve_workers, run_ordered_pool
from .treatment import fingerprint_arm


@dataclass
class ArmSummary:
    arm: str
    n: int
    # Every rate is ``None`` when its denominator is empty. An arm that scored no
    # rows measured nothing, and 0.0 claims it measured everything and got none of
    # it right — which downstream quotability checks then read as a real observation.
    ex_lenient: float | None
    ex_strict: float | None
    # GENUINE refusals only, matching the pooled driver. A crash is our bug, not the
    # model declining, and the two used to be one number here.
    refusal_rate: float | None
    decoy_touch_rate: float | None
    conditional_ex_lenient: float | None  # EX among rows that produced SQL
    by_difficulty: dict[str, float]
    # Crashes, counted apart from refusals (governed_bi.stages). This driver produces
    # the single-DB ladder numbers, so the split has to exist here too or the two
    # drivers disagree about what a refusal is.
    crash_rate: float | None = None
    n_crashed: int = 0
    # The complete outcome partition, so the headline counts can be checked against n.
    by_outcome: dict[str, int] = field(default_factory=dict)
    by_failed_stage: dict[str, int] = field(default_factory=dict)
    # ``refused_by`` is free text; a value the stage table has never heard of is
    # counted rather than silently attributed to a stage nothing observed.
    n_unmapped_refused_by: int = 0
    # Items with no gold-hash key: scored EX=0 in the denominator. Surface the
    # count so a nonzero value reads as a keying defect, not a "hard db".
    n_missing_gold: int = 0
    # Wrong answer, right row count: the projection / ordering / formatting class.
    # Sizes how much of the remaining gap is a grading-contract artifact rather than
    # a semantic error — the difference between fixing the generator and changing
    # the grader. Same field the pooled driver reports, from the same grade dict.
    n_wrong_but_nrows_match: int = 0
    # Correct answers that are grading free passes (Audit E2). Empty gold, no-FROM
    # predictions, and (when table sets are available) zero table overlap.
    n_correct_with_empty_gold: int = 0
    n_correct_and_pred_has_no_from: int = 0
    n_correct_and_zero_table_overlap: int = 0
    # ``None`` (not ``0.0``) when no row recorded ``attempts``: an arm whose solver
    # never reported a repair count did not average zero attempts.
    mean_attempts: float | None = None


def _dsn_host(dsn: str) -> str:
    """``host:port`` from a libpq DSN, with no credentials.

    The manifest used to hard-code ``127.0.0.1:5435`` regardless of ``--pg-dsn``, so
    two runs against different databases were indistinguishable in the record. Only
    the host and port are taken: a DSN carries a password, and a manifest is an
    artifact people paste into issues.
    """
    parts = dict(
        kv.split("=", 1) for kv in str(dsn).split() if "=" in kv
    )
    host = parts.get("host", "?")
    port = parts.get("port", "")
    return f"{host}:{port}" if port else host


def _utc_ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def build_manifest(
    *,
    db_id: str,
    bird_dir: Any,
    pg_dsn: str,
    max_agent_steps: int,
    skip_agent: bool,
    model_name: str | None,
    resolved_prompts: dict[str, str],
) -> dict[str, Any]:
    """The single-schema driver's manifest.

    Module-level so it can be asserted on directly. Inline in ``run_experiment`` it
    was only reachable by running the whole driver, so the one property that matters
    to the shared ledger — ``model`` is ``None`` under ``--skip-agent`` — could only
    be checked by reading the source. A test that reads source text passes a
    semantically equivalent rewrite, and that is exactly how this field drifted away
    from the pooled driver's the first time, letting a smoke run be reported
    *comparable* to a real one.
    """
    return {
        "db_id": db_id,
        "bird_dir": str(bird_dir),
        # The host actually connected to, not a literal: a manifest that always says
        # 127.0.0.1:5435 cannot tell two runs against different databases apart.
        "pg_dsn_host": _dsn_host(pg_dsn),
        "created_at_utc": _utc_ts(),
        # Which commit produced this. Recoverable from nothing else in the directory,
        # and ``eval.index`` reads it — the pooled driver already records it.
        "git_sha": corpus_release_hash(),
        "max_agent_steps": max_agent_steps,
        "skip_agent": skip_agent,
        "serve_path": "agent_core",  # agent-only serve (ADR 0002)
        # One definition, shared with the pooled driver, because both write this
        # field into the same ledger and had already drifted apart once.
        "model": manifest_model(model_name, skip_agent=skip_agent),
        # Same key names the pooled driver writes and ``eval.index`` reads: the map
        # for a human, the text hash for the comparability rule.
        "prompt_variants": resolved_prompts,
        "prompt_set_hash": prompt_set_hash(resolved_prompts),
    }


def _sum_or_none(rows: list[dict[str, Any]], key: str) -> float | None:
    """Sum of a numeric row field, or ``None`` when no row recorded it.

    A run with no cost data and a run that genuinely cost nothing are different
    facts, so the two must not both collapse to one value: ``0.0`` is reported only
    when some row actually observed a zero.
    """
    vals = [float(r[key]) for r in rows if isinstance(r.get(key), (int, float))]
    return sum(vals) if vals else None


def _mean_or_none(rows: list[dict[str, Any]], key: str) -> float | None:
    """Mean over the rows that recorded ``key``, else ``None`` — never ``0.0``,
    which would read as a real observation of zero."""
    vals = [float(r[key]) for r in rows if isinstance(r.get(key), (int, float))]
    return sum(vals) / len(vals) if vals else None


def _total_tokens(rows: list[dict[str, Any]]) -> int | None:
    """Total tokens across rows, from whichever usage shape the provider returned."""
    total = 0
    seen = False
    for r in rows:
        usage = r.get("usage") or r.get("token_sum")
        if isinstance(usage, dict):
            value = usage.get("total_tokens")
            if isinstance(value, (int, float)):
                total += int(value)
                seen = True
    return total if seen else None


def _round_or_none(value: float | None, digits: int) -> float | None:
    """Rounding that preserves "not measured": ``round(value or 0.0)`` would turn an
    unrecorded field into a measured zero."""
    return None if value is None else round(value, digits)


def _cost_block(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Wall-clock + token/dollar cost for one arm (parity with the pooled driver).

    Kept out of :class:`ArmSummary` on purpose: latency is scheduler-dependent, and
    the workers-invariance test compares the whole summary — a serial and a pooled
    run must agree on every number that is a *result*
    (docs/plans/eval-concurrency-design.md). Nesting it here instead is what lets
    that comparison stay total while the cost still lands in ``summary.json``.
    """
    return {
        "total_latency_sec": _round_or_none(_sum_or_none(rows, "latency_sec"), 2),
        "mean_latency_sec": _round_or_none(_mean_or_none(rows, "latency_sec"), 3),
        "total_cost_est_usd": _sum_or_none(rows, "cost_est_usd"),
        # How many rows the total actually covers. _sum_or_none sums only the rows
        # that carried the key, so a crashed turn — which burned model calls and recorded
        # no meta — contributes nothing and deflates the total. That was cosmetic while
        # cost was context; it is load-bearing now that ladder_deltas divides by it,
        # which is why that function refuses to price a step whose cost covers fewer rows
        # than the arm scored.
        "n_rows_priced": sum(1 for r in rows if r.get("cost_est_usd") is not None),
        "total_tokens": _total_tokens(rows),
    }


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    """Overwrite ``path`` atomically (temp + flush/fsync + replace).

    Crash-row resume rewrites generations files through this helper. An in-place
    ``open("w")`` truncate left a kill mid-write able to destroy already-scored
    rows; the temp-file swap keeps the previous file until the new one is durable.
    """
    import os

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp{os.getpid()}")
    try:
        with tmp.open("w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _validate_corpora(corpora: dict[str, Any], *, connector: Any = None) -> dict[str, dict]:
    """CI-green gate: run ``validate_corpus`` on each arm's corpus so a corpus
    with a reference-integrity defect can never be scored *silently*. Returns a
    per-arm ``{finding_count, findings[:20]}`` block for ``summary.json``.

    This closes the gap that let dangling term bindings ride into a scored arm
    unnoticed: the count is now a headline field, not something buried in a
    per-corpus manifest. ``connector`` (optional) additionally checks physical
    existence against the live catalog.
    """
    from ..corpus.validate import validate_corpus

    out: dict[str, dict] = {}
    for arm_name, loaded in corpora.items():
        findings = validate_corpus(loaded.assets, connector=connector)
        out[arm_name] = {
            "finding_count": len(findings),
            "findings": [f"{f.code} [{f.asset_id}]: {f.message}" for f in findings[:20]],
        }
    return out


def _collect_curator_errors(corpus_dirs: dict[str, Path]) -> dict[str, dict]:
    """Surface swallowed curator failures at the run level.

    ``_invoke_agent`` catches agent crashes and records them in the per-corpus
    ``run_manifest.json`` (``error`` / ``fix_pass_error``) without aborting — so a
    crashed fold or fix-pass is invisible in the headline ``summary.json``. Lift
    the short form of any recorded error up so it is not swallowed silently. The
    full traceback stays in the per-corpus manifest.
    """
    out: dict[str, dict] = {}
    for arm, d in corpus_dirs.items():
        # A diagnostic that could not be promoted leaves this marker. Checked FIRST,
        # and reported even though it is not itself a curator error: it means the
        # manifest below may be absent or stale, and "absent" is read two lines down
        # as "no error". Without this, a build whose crash record went missing is
        # indistinguishable from a build that did not crash — the exact swallowed
        # failure this function exists to surface.
        marker = d / "UNPROMOTED_SIDECARS.json"
        unpromoted: str | None = None
        if marker.exists():
            try:
                names = json.loads(marker.read_text(encoding="utf-8")).get("unpromoted")
            except ValueError:
                names = None
            unpromoted = (
                "curator diagnostics could not be promoted "
                f"({', '.join(names) if names else 'see marker'}) — this db's "
                "curator verdict is unknown"
            )
        mpath = d / "run_manifest.json"
        # Both are reported when both exist. Returning early on the marker would drop
        # a real, recorded curator crash in favour of the note saying a *different*
        # file went missing — losing the more specific finding of the two.
        if not mpath.exists():
            if unpromoted:
                out[arm] = {"error": unpromoted, "fix_pass_error": None}
            continue
        m = json.loads(mpath.read_text(encoding="utf-8"))
        err, fix_err = m.get("error"), m.get("fix_pass_error")
        if err or fix_err or unpromoted:
            first = (err or "").splitlines()[0] if err else None
            out[arm] = {
                "error": "; ".join(x for x in (unpromoted, first) if x) or None,
                "fix_pass_error": (fix_err or "").splitlines()[0] if fix_err else None,
            }
    return out


def _warn_if_curator_errors(curator_errors: dict[str, dict]) -> None:
    for arm, block in curator_errors.items():
        print(
            f"\n*** WARNING: curator error on arm {arm!r} was swallowed during "
            f"build (corpus still scored): error={block['error']!r} "
            f"fix_pass_error={block['fix_pass_error']!r} ***"
        )


def _sme_fold_signal(
    curated_root: Path, curated_sme_root: Path, schema: str
) -> dict[str, Any]:
    """Surface the SME fold outcome so a curated_sme arm that ended byte-identical
    to curated (empty/missing ledger → nothing folded, or a resume that reused a
    prior no-op corpus) is visible in summary.json instead of masquerading as
    "SME added nothing". Reads the Phase-B manifest (root, or the
    data-lake-relocated ``<schema>/_build/`` location)."""
    from ..curator.pipeline import _corpora_differ

    manifest = curated_sme_root / "run_manifest.json"
    if not manifest.exists():
        manifest = curated_sme_root / schema / "_build" / "run_manifest.json"
    data: dict[str, Any] = {}
    if manifest.exists():
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
    return {
        "fold_mode": data.get("fold_mode"),
        "ledger_source": data.get("ledger_source"),
        "clarification_count": data.get("clarification_count"),
        "clarifications_applied": data.get("clarifications_applied"),
        "identical_to_curated": not _corpora_differ(
            curated_root, curated_sme_root, schema
        ),
    }


def _warn_if_sme_noop(sme_fold: dict[str, Any], *, db_id: str) -> None:
    if sme_fold.get("identical_to_curated"):
        print(
            f"\n*** WARNING: curated_sme is byte-identical to curated for {db_id!r} "
            f"(fold_mode={sme_fold.get('fold_mode')!r}, "
            f"ledger_source={sme_fold.get('ledger_source')!r}) — the SME arm folded "
            "nothing; its EX equals curated by construction, not by measurement ***"
        )


def _warn_if_not_green(corpus_validation: dict[str, dict]) -> None:
    """Emit a loud, unmissable warning for any arm whose corpus is not CI-green.
    Non-fatal (a long live run should not be lost to a stray finding), but the
    signal is impossible to overlook — and the count is persisted in summary.json.
    """
    for arm_name, block in corpus_validation.items():
        if block["finding_count"]:
            print(
                f"\n*** WARNING: arm {arm_name!r} corpus is NOT CI-green — "
                f"{block['finding_count']} finding(s); scored numbers may be "
                f"corrupted. ***"
            )
            for line in block["findings"]:
                print(f"    - {line}")


def _suspect_from_corpus(corpus_root: Path, schema: str) -> frozenset[str]:
    corpus = load_corpus(corpus_root, schema=schema)
    refs: set[str] = set()
    for asset in corpus.assets:
        if not isinstance(asset, TableAsset):
            continue
        for col in asset.columns:
            if col.reliability.status is ReliabilityStatus.suspect:
                refs.add(f"{asset.physical_name}.{col.physical_name}")
                refs.add(col.physical_name)
    return frozenset(refs)


def _run_arm_generations(
    *,
    arm: str,
    solver,
    items,
    gold_hashes,
    gateway: Gateway,
    identity: Identity,
    bird_dir: Path,
    suspect_columns: frozenset[str],
    dialect: str,
    serve_workers: int = 1,
    worker_factory: "Callable[[int], ServeWorker] | None" = None,
    # Notes held by the corpus this arm served. Without it the treatment check
    # "held notes, injected none" is unreachable, because its guard is
    # ``if count and not injected``.
    corpus_note_assets: int | None = None,
) -> tuple[list[dict[str, Any]], ArmSummary, dict[str, Any]]:
    """Serve + grade one arm over ``items``.

    ``serve_workers == 1`` (default) runs the fully serial loop against the
    passed ``solver`` / ``gateway`` — byte-identical to the pre-concurrency path.
    ``serve_workers > 1`` fans the per-question ``solve+grade`` unit out across a
    thread pool where each worker owns its own connector/gateway/solver (built by
    ``worker_factory``); results are reassembled in the original item order so the
    generations rows and every aggregate are identical to the serial run.
    """
    items = list(items)

    def _grade_one(item, *, solver, gateway) -> dict[str, Any]:
        """Solve + grade ONE question against the given (solver, gateway) — the
        atomic unit shared by the serial and pooled paths. Returns the row plus
        the booleans the summary needs, so no counter is touched off-thread."""
        qid = item.question_id or item.question
        t0 = time.perf_counter()
        try:
            sql, meta_raw = solver.solve_with_meta(item.question)
            err_msg = None
        except Exception as err:
            # Class name AND message. ``str(KeyError("schema"))`` is just "'schema'",
            # which names neither the failure kind nor the frame — and this string is
            # the only record of the crash that reaches the row.
            sql, meta_raw = None, {}
            err_msg = f"{type(err).__name__}: {err}"
        latency = time.perf_counter() - t0

        gold = gold_hashes.get(str(qid))
        grade = score_sql_hashes(sql, gold, gateway, identity, bird_dir)
        if err_msg and grade.get("error") in (None, "refusal"):
            grade["error"] = err_msg

        xcheck = crosscheck_execution_match(sql, item.sql, gateway)
        diff = item.difficulty or "unknown"
        meta = dict(meta_raw or {})
        row = {
            "request_id": str(qid),
            "question_id": str(qid),
            "arm": arm,
            "generated_sql": sql,
            "latency_sec": round(latency, 4),
            "usage": meta.get("usage"),
            "cost_est_usd": meta.get("cost_est_usd"),
            "correct": grade["correct"],
            "correct_strict": grade["correct_strict"],
            "error": grade.get("error"),
            # Result shape: same row count + wrong hash is a projection / ordering
            # failure, a different count is a different answer. Absent from the
            # refusal / missing-gold / exec-error branches of score_sql_hashes, so
            # these stay None there instead of claiming zero rows.
            "pred_nrows": grade.get("pred_nrows"),
            "pred_ncols": grade.get("pred_ncols"),
            "gold_nrows": grade.get("gold_nrows"),
            "nrows_match": grade.get("nrows_match"),
            "ex_crosscheck": xcheck,
            "difficulty": diff,
            "refused_by": meta.get("refused_by"),
            "failed_layer": meta.get("failed_layer"),
            "graded_delivery": meta.get("graded_delivery"),
            "coverage_best_effort": meta.get("coverage_best_effort"),
            "tier": meta.get("tier"),
            "semantic_assurance": meta.get("semantic_assurance"),
            "safety_clearance": meta.get("safety_clearance"),
            "attempts": meta.get("attempts"),
            # Prompt identity per row, relayed from the serve path's own stamp.
            # ``None`` when nothing served the row (offline refuse-all): no prompt
            # was sent, and claiming one would be worse than the gap.
            "prompt_variants": meta.get("prompt_variants"),
            "prompt_set_hash": meta.get("prompt_set_hash"),
            # Delivery. ``fingerprint_arm`` reads exactly these four, and this driver
            # recorded none of them — so the treatment check built to catch "the
            # corpus never reached the prompt" reported "unobserved" on every row of
            # every run it produced, which is indistinguishable from a clean pass to
            # anyone reading the summary.
            "injected_note_ids": meta.get("injected_note_ids"),
            "n_notes_injected": meta.get("n_notes_injected"),
            "context_chars": meta.get("context_chars"),
            "context_hash": meta.get("context_hash"),
            "error_type": meta.get("error_type"),
        }
        # Classify here, where a solver exception is still distinguishable from every
        # other kind of ``error`` string. Parity with the pooled driver is the point:
        # this driver produces the single-DB ladder numbers, and until now it still
        # scored a crash as a refusal — the defect the shared vocabulary exists to end.
        # ``infra_error:`` from the grader is a harness failure, not a wrong answer
        # (audit E4) — same stamp as ``run_datalake``.
        grade_err = grade.get("error")
        infra_msg = (
            grade_err
            if isinstance(grade_err, str) and grade_err.startswith(INFRA_ERROR_PREFIX)
            else None
        )
        outcome, failed_stage, recognised = classify_outcome(
            generated_sql=sql,
            exception=err_msg or infra_msg,
            refused_by=meta.get("refused_by"),
            recursion_exhausted=meta.get("recursion_exhausted"),
        )
        if infra_msg and outcome is Outcome.crashed and failed_stage is None:
            failed_stage = Stage.execute
        row["outcome"] = outcome.value
        row["failed_stage"] = failed_stage.value if failed_stage else None
        if not recognised:
            print(
                f"*** WARNING: unrecognised refused_by={meta.get('refused_by')!r} on "
                f"{qid} — counted in n_unmapped_refused_by, not attributed to a stage"
            )
        return {
            "row": row,
            "correct": bool(grade["correct"]),
            "correct_strict": bool(grade["correct_strict"]),
            "outcome": outcome,
            "recognised_refusal": recognised or meta.get("refused_by") is None,
            "refused": outcome is Outcome.refused,
            "decoy": sql is not None and _touches_suspect(sql, suspect_columns, dialect),
            "xcheck": xcheck,
            "diff": diff,
        }

    if serve_workers > 1:
        if worker_factory is None:
            raise ValueError("serve_workers > 1 requires a worker_factory")
        bundles = run_ordered_pool(
            items,
            workers=serve_workers,
            make_worker=worker_factory,
            run_task=lambda w, item: _grade_one(item, solver=w.solver, gateway=w.gateway),
        )
    else:
        bundles = [_grade_one(item, solver=solver, gateway=gateway) for item in items]

    # --- aggregation on the calling thread, in original item order ---
    rows: list[dict[str, Any]] = []
    n_correct = 0
    n_strict = 0
    n_refused = 0
    n_decoy = 0
    n_produced = 0
    n_xcheck = 0
    n_xcheck_agree = 0
    n_missing_gold = 0
    n_wrong_but_nrows_match = 0
    n_crashed = 0
    n_unmapped_refused_by = 0
    by_outcome: dict[str, int] = {}
    by_failed_stage: dict[str, int] = {}
    by_diff_correct: dict[str, list[bool]] = {}

    for b in bundles:
        rows.append(b["row"])
        if b["row"].get("error") == "missing_gold_hash":
            n_missing_gold += 1
        if not b["correct"] and b["row"].get("nrows_match"):
            n_wrong_but_nrows_match += 1
        if b["xcheck"] is not None:
            n_xcheck += 1
            if b["xcheck"] == b["correct"]:
                n_xcheck_agree += 1
        if b["refused"]:
            n_refused += 1
        if b["outcome"] is Outcome.crashed:
            n_crashed += 1
        if not b["recognised_refusal"]:
            n_unmapped_refused_by += 1
        stage = b["row"].get("failed_stage")
        if stage:
            by_failed_stage[stage] = by_failed_stage.get(stage, 0) + 1
        by_outcome[b["outcome"].value] = by_outcome.get(b["outcome"].value, 0) + 1
        if b["row"].get("generated_sql"):
            n_produced += 1
            if b["decoy"]:
                n_decoy += 1
        if b["correct"]:
            n_correct += 1
        if b["correct_strict"]:
            n_strict += 1
        by_diff_correct.setdefault(b["diff"], []).append(b["correct"])

    free_passes = free_pass_counts(
        rows,
        gold={
            str(item.question_id or item.question): item.sql
            for item in items
            if getattr(item, "sql", None)
        },
    )
    n = len(items)
    summary = ArmSummary(
        arm=arm,
        n=n,
        # Rates are None at n == 0: an arm that scored nothing measured nothing, and
        # 0.0 claims it measured everything and got none of it right.
        ex_lenient=(n_correct / n) if n else None,
        ex_strict=(n_strict / n) if n else None,
        # GENUINE refusals only, matching the pooled driver. A crash is our bug.
        refusal_rate=(n_refused / n) if n else None,
        crash_rate=(n_crashed / n) if n else None,
        n_crashed=n_crashed,
        by_outcome=by_outcome,
        by_failed_stage=by_failed_stage,
        n_unmapped_refused_by=n_unmapped_refused_by,
        decoy_touch_rate=(n_decoy / n_produced) if n_produced else None,
        conditional_ex_lenient=(n_correct / n_produced) if n_produced else None,
        by_difficulty={
            d: (sum(1 for x in xs if x) / len(xs) if xs else 0.0)
            for d, xs in sorted(by_diff_correct.items())
        },
        n_missing_gold=n_missing_gold,
        n_wrong_but_nrows_match=n_wrong_but_nrows_match,
        n_correct_with_empty_gold=free_passes["n_correct_with_empty_gold"],
        n_correct_and_pred_has_no_from=free_passes["n_correct_and_pred_has_no_from"],
        n_correct_and_zero_table_overlap=free_passes[
            "n_correct_and_zero_table_overlap"
        ],
        mean_attempts=_mean_or_none(rows, "attempts"),
    )
    # Attach cross-check agreement onto the generations sidecar via a sentinel row
    # isn't ideal; return it through the summary dict in the caller instead.
    summary_extra = {
        "ex_crosscheck_n": n_xcheck,
        "ex_crosscheck_agree_rate": (n_xcheck_agree / n_xcheck) if n_xcheck else None,
        # Same two diagnostics the pooled driver records, so a single-schema run and
        # a data-lake run can be read with one vocabulary. ``errors`` attributes the
        # wrong answers to a stage; ``treatment`` records what actually reached the
        # model. Kept in ``summary_extra`` rather than on ``ArmSummary`` because
        # that dataclass is the stable cross-driver contract and these are nested
        # blocks, not scalars.
        "errors": summarise_attributions(
            attribute_rows(
                rows,
                {
                    str(item.question_id or item.question): item.sql
                    for item in items
                    if getattr(item, "sql", None)
                },
            )
        ),
        "treatment": fingerprint_arm(
            str(arm), rows, corpus_note_assets=corpus_note_assets
        ).to_dict(),
    }
    return rows, summary, summary_extra


def _delta(hi: float | None, lo: float | None) -> float | None:
    """``hi - lo``, or ``None`` when either side was never measured.

    Rates became ``None`` at an empty denominator so a measurement of zero could be
    told from no measurement, but the delta arithmetic here kept subtracting them
    and raised ``TypeError`` on the offline ``--skip-agent`` path, where no arm
    produces SQL and every ``decoy_touch_rate`` is ``None``. The crash landed after
    the whole run, before ``summary.json`` was written, so the run's own artifacts
    were lost — which is exactly the shape of failure the offline smoke exists to
    catch before a live run hits it.
    """
    if hi is None or lo is None:
        return None
    return hi - lo


class _RefuseAllSolver:
    """Trivial solver for ``--skip-agent`` offline smoke runs (no live model):
    refuses every question so the layered arms still produce a well-formed run.

    Implements ``solve_with_meta`` so both drivers use one uniform call path
    (the return-meta contract, not a stashed attribute)."""

    def solve_with_meta(self, question: str) -> tuple[str | None, dict]:
        del question
        return None, {"refused_by": "no_model"}

    def solve(self, question: str) -> str | None:
        return self.solve_with_meta(question)[0]


def run_experiment(
    *,
    db_id: str,
    bird_dir: Path,
    pg_dsn: str,
    out_dir: Path,
    max_agent_steps: int = 25,
    skip_agent: bool = False,
    limit: int | None = None,
    resume_curated: Path | None = None,
    serve_workers: int = 1,
    prompt_variants: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Run baseline/curated/curated_sme for one DB; write generations + summary
    under ``out_dir``.

    ``serve_workers`` fans the per-question serve loop across that many threads
    (each with its own connector + gateway + graph, all pinned to ``schema=db_id``
    like the shared connector). 1 = serial (the default). Ignored when there is no
    live model — the offline refuse-all path stays serial.

    ``prompt_variants`` (``stage -> variant``, empty = all ``v1``) selects
    registered prompt text per stage; it reaches serve through ``Settings``, and
    the resolved map plus its text hash land in ``manifest.json``."""
    # Resolved before any database work: a bad stage or variant must cost nothing.
    resolved_prompts = resolve_prompts(prompt_variants)
    load_dotenv()
    dataset_dir = bird_dir / "eval_dataset"
    train = load_bird_items(
        dataset_dir, db_id, split="train", gold_sql_field="sql_rename"
    )
    test = load_bird_items(
        dataset_dir, db_id, split="test", gold_sql_field="sql_rename"
    )
    if limit is not None:
        test = test[:limit]

    train_ids = {it.question_id for it in train if it.question_id}
    test_ids = {it.question_id for it in test if it.question_id}
    overlap = train_ids & test_ids
    if overlap:
        raise AssertionError(f"train/test question_id overlap: {sorted(overlap)[:5]}")

    gold_hashes = load_gold_hashes(bird_dir, db_id=db_id)
    trap_cols = load_trap_columns(bird_dir, db_id)

    connector = PostgresConnector(pg_dsn, schema=db_id)
    schemas = connector.list_schemas()
    if db_id not in schemas:
        connector.close()
        raise RuntimeError(f"schema {db_id!r} not on pg_rename_decoy; have {schemas[:20]}")
    # Smoke SELECT through the gateway to confirm the schema is queryable end-to-end.
    gateway = Gateway(connector, max_rows=200_000, timeout_s=60.0)
    identity = Identity(user="eval", all_access=True)
    tables = connector.list_tables()
    if not tables:
        connector.close()
        raise RuntimeError(f"schema {db_id!r} has no tables to smoke-test")
    gateway.execute(f'SELECT 1 AS n FROM "{db_id}"."{tables[0]}" LIMIT 1', identity)

    base_settings = load_settings()
    datasource = DataSourceConfig(
        kind="postgres",
        corpus_pin=db_id,
        schema=db_id,
        dsn=pg_dsn,
    )
    settings = Settings.for_env(
        Environment.dev,
        models=base_settings.models,
        datasource=datasource,
        corpus_root=str(out_dir),
    )
    # pipeline-design §6: semantic/coverage/repair-exhaustion deliver-and-grade;
    # suspect soft-warn only. Safety (L2 + refuse-gate) stays hard.
    settings = replace(
        settings,
        hard_block_suspect_columns=False,
        grade_semantic_failures=True,
        # The full resolved map: one description of what this run sends, read by
        # the graph build, the config hash, and every stamped row alike.
        prompt_variants=resolved_prompts,
    )

    # Live self-check: re-exec a sample of gold SQL and confirm hash_grade matches
    # the precomputed gold hashes (catches normalizer drift / bad DSN).
    gold_check = validate_gold_hashes_live(
        test, gold_hashes, gateway, identity, sample=min(5, len(test))
    )
    # Fail closed when NOTHING was checkable: n_checked==0 means the "prove the
    # normalizer agrees with precomputed gold before scoring" gate never ran (e.g.
    # a db_id/split/dsn_key filter mismatch in load_gold_hashes) — silently
    # skipping it would then score every arm as missing_gold_hash with no signal.
    if not gold_check["n_checked"]:
        raise RuntimeError(
            "hash_grade self-check verified 0 gold rows (n_checked=0): no test item "
            "had a usable gold hash + SQL. Check the db_id / split / dsn_key filters "
            f"in load_gold_hashes before trusting any score. {gold_check}"
        )
    if gold_check["agree_rate"] < 1.0:
        raise RuntimeError(
            f"hash_grade self-check failed against live gold SQL: {gold_check}"
        )

    # --- LLM clients ---
    chat = None
    lc_model = None
    if not skip_agent:
        from ..llm import LangChainChatClient

        chat_client = LangChainChatClient.from_config(settings.models)
        chat = chat_client
        lc_model = chat_client.model
    else:
        from ..llm import StaticChatClient

        chat = StaticChatClient(responses="CANNOT_ANSWER")

    run_root = out_dir
    run_root.mkdir(parents=True, exist_ok=True)
    corpus_baseline = run_root / "corpus_baseline"
    corpus_curated = run_root / "corpus_curated"
    corpus_curated_sme = run_root / "corpus_curated_sme"

    # --- baseline corpus (D5: deterministic-max, DB-derivable only; no LLM) ---
    from ..curator.pipeline import (
        build_baseline_corpus,
        build_curated_corpus,
        build_curated_corpus_with_sme,
    )
    from ..curator.sme import SimulatedSme, assert_brief_no_leakage, build_sme_brief

    build_baseline_corpus(connector, db_id, corpus_baseline)

    # --- curated corpus ---
    if resume_curated is not None:
        corpus_curated = Path(resume_curated)
    else:
        build_curated_corpus(
            connector,
            gateway,
            db_id,
            train,
            corpus_curated,
            model=None if skip_agent else lc_model,
            dialect="postgres",
            max_agent_steps=max_agent_steps,
            run_agent=not skip_agent,
            system_prompt=prompt_text("curator_phase_a", resolved_prompts),
            settings=settings,
        )

    # --- curated_sme corpus ---
    # Always rebuild + assert the SME brief (even on --resume-curated) so leakage
    # invariants execute for every headline number.
    # ``description_dir`` covers both BIRD trees, and ``rename_map`` re-addresses the
    # original identifiers in those CSVs to the obfuscated schema the agent queries.
    desc_dir = description_dir(bird_dir, db_id)
    if desc_dir is None:
        print(
            f"\n*** WARNING: no database_description/ for {db_id!r} under either "
            "BIRD tree — the SME brief carries no column docs ***"
        )
    brief = build_sme_brief(
        desc_dir or bird_dir / "_nonexistent-sme-docs",
        train,
        system_rules=prompt_text("sme_rules", resolved_prompts),
        rename_map=load_rename_map(bird_dir, db_id),
    )
    assert_brief_no_leakage(
        brief,
        gold_sqls=[it.sql for it in train],
        test_questions=[it.question for it in test],
    )
    brief_checked = True

    existing_curated_sme = corpus_curated.parent / "corpus_curated_sme"
    if (
        resume_curated is not None
        and existing_curated_sme.is_dir()
        and any(existing_curated_sme.rglob("*.yaml"))
    ):
        corpus_curated_sme = existing_curated_sme
    else:
        if skip_agent:
            from ..curator.clarifications import StaticResponder

            responder = StaticResponder(
                default="Domain column used in analytics; treat as reliable unless samples conflict."
            )
            build_curated_corpus_with_sme(
                connector,
                gateway,
                db_id,
                train,
                corpus_curated_sme,
                responder=responder,
                curated_root=corpus_curated,
                model=None,
                run_agent_repass=False,
                seed_ledger_if_empty=True,
                system_prompt=prompt_text("curator_phase_b", resolved_prompts),
                settings=settings,
            )
        else:
            responder = SimulatedSme(chat, brief, gateway=gateway, settings=settings)
            build_curated_corpus_with_sme(
                connector,
                gateway,
                db_id,
                train,
                corpus_curated_sme,
                responder=responder,
                curated_root=corpus_curated,
                model=lc_model,
                run_agent_repass=True,
                seed_ledger_if_empty=False,
                system_prompt=prompt_text("curator_phase_b", resolved_prompts),
                settings=settings,
            )

    # --- Solvers ---
    # Every rung of the eval ladder routes through the same agentic serve core
    # (ADR 0002 — the only serve path); rungs differ only by the corpus fed in.
    # ``--skip-agent`` has no live model, so every rung degrades to a trivial
    # refuse-all (offline smoke).
    baseline_corpus_loaded = load_corpus(corpus_baseline, schema=db_id)
    curated_corpus_loaded = load_corpus(corpus_curated, schema=db_id)
    curated_sme_corpus_loaded = load_corpus(corpus_curated_sme, schema=db_id)

    # CI-green gate: never score a corpus with reference-integrity defects
    # silently. Count goes into summary.json; a non-green arm warns loudly.
    corpus_validation = _validate_corpora(
        {
            "baseline": baseline_corpus_loaded,
            "curated": curated_corpus_loaded,
            "curated_sme": curated_sme_corpus_loaded,
        },
        connector=connector,
    )
    _warn_if_not_green(corpus_validation)

    # Lift any swallowed curator build errors (fold / fix-pass crashes) from the
    # per-corpus manifests into the headline so they are not invisible.
    curator_errors = _collect_curator_errors(
        {"curated": corpus_curated, "curated_sme": corpus_curated_sme}
    )
    _warn_if_curator_errors(curator_errors)

    sme_fold = _sme_fold_signal(corpus_curated, corpus_curated_sme, db_id)
    _warn_if_sme_noop(sme_fold, db_id=db_id)

    if lc_model is not None:
        baseline = agent_solver(
            baseline_corpus_loaded,
            gateway,
            settings,
            identity,
            model=lc_model,
            session_id="eval-baseline",
        )
        curated = agent_solver(
            curated_corpus_loaded,
            gateway,
            settings,
            identity,
            model=lc_model,
            session_id="eval-curated",
        )
        curated_sme = agent_solver(
            curated_sme_corpus_loaded,
            gateway,
            settings,
            identity,
            model=lc_model,
            session_id="eval-curated_sme",
        )
    else:
        baseline = curated = curated_sme = _RefuseAllSolver()

    suspect_baseline = _suspect_from_corpus(corpus_baseline, db_id) | trap_cols
    suspect_curated = _suspect_from_corpus(corpus_curated, db_id) | trap_cols
    suspect_curated_sme = _suspect_from_corpus(corpus_curated_sme, db_id) | trap_cols

    # Serve concurrency (docs/plans/eval-concurrency-design.md): only fan out when
    # there is a live model — the offline refuse-all path has nothing to overlap.
    effective_workers = serve_workers if lc_model is not None else 1
    if effective_workers > 1:
        print(
            f"  serve concurrency: {effective_workers} worker(s)/arm — each owns "
            f"its own connector+gateway+graph (schema={db_id!r})"
        )

    def _make_arm_factory(
        arm_corpus: Any, session_base: str
    ) -> "Callable[[int], ServeWorker]":
        """Build a per-worker (connector, gateway, solver) factory for one arm.

        Mirrors the shared connector: ``schema=db_id`` (pinned driver), and one
        ``agent_solver`` — hence one graph — per worker, with a distinct
        ``session_id`` so worker graphs never collide."""

        def factory(idx: int) -> ServeWorker:
            conn = PostgresConnector(pg_dsn, schema=db_id)
            gw = Gateway(conn, max_rows=200_000, timeout_s=60.0)
            slv = agent_solver(
                arm_corpus,
                gw,
                settings,
                identity,
                model=lc_model,
                session_id=f"{session_base}-w{idx}",
            )
            return ServeWorker(connector=conn, gateway=gw, solver=slv)

        return factory

    summaries: dict[str, ArmSummary] = {}
    crosschecks: dict[str, dict] = {}
    arm_costs: dict[str, dict[str, Any]] = {}
    for arm_name, solver, suspects, arm_corpus, session_base in (
        ("baseline", baseline, suspect_baseline, baseline_corpus_loaded, "eval-baseline"),
        ("curated", curated, suspect_curated, curated_corpus_loaded, "eval-curated"),
        (
            "curated_sme",
            curated_sme,
            suspect_curated_sme,
            curated_sme_corpus_loaded,
            "eval-curated_sme",
        ),
    ):
        worker_factory = (
            _make_arm_factory(arm_corpus, session_base)
            if effective_workers > 1
            else None
        )
        gens, summary, xtra = _run_arm_generations(
            arm=arm_name,
            solver=solver,
            items=test,
            gold_hashes=gold_hashes,
            gateway=gateway,
            identity=identity,
            bird_dir=bird_dir,
            suspect_columns=suspects,
            dialect="postgres",
            serve_workers=effective_workers,
            worker_factory=worker_factory,
            corpus_note_assets=sum(
                1 for a in arm_corpus.assets if isinstance(a, NoteAsset)
            ),
        )
        _write_jsonl(run_root / f"generations.{arm_name}.jsonl", gens)
        summaries[arm_name] = summary
        crosschecks[arm_name] = xtra
        arm_costs[arm_name] = _cost_block(gens)

    baseline_s = summaries["baseline"]
    curated_s = summaries["curated"]
    curated_sme_s = summaries["curated_sme"]

    # Refuse-gate: BIRD test questions are all answerable, so the curated_sme
    # arm's refusal_rate IS the false-refusal rate. The missing half — refusal
    # *accuracy* on truly-unanswerable questions — is measured here against a
    # cross-DB negative set (questions from other db_ids, unanswerable by
    # construction). Needs the live model; skipped on the offline (no-model) path.
    refuse_gate: dict[str, Any] | None = None
    if lc_model is not None:
        from .bird_loader import load_cross_db_unanswerable
        from .refuse_gate import agent_refuser, eval_refuse_gate

        try:
            unanswerable = load_cross_db_unanswerable(dataset_dir, db_id, k=20)
            if unanswerable:
                refused = agent_refuser(
                    curated_sme_corpus_loaded, gateway, settings, identity, model=lc_model
                )
                rg = eval_refuse_gate([], unanswerable, refused)  # accuracy on unanswerable
                refuse_gate = {
                    "refusal_accuracy": rg.refusal_accuracy,
                    "false_refusal_rate": curated_sme_s.refusal_rate,  # answerable-set refusals
                    "n_unanswerable": len(unanswerable),
                    "n_answerable": curated_sme_s.n,
                    "note": (
                        "refusal_accuracy on a cross-DB unanswerable set (curated_sme "
                        "corpus); false_refusal_rate reuses the curated_sme arm's "
                        "refusal_rate since every BIRD test question is answerable"
                    ),
                }
            else:
                refuse_gate = {"skipped": "no cross-DB unanswerable questions available"}
        except Exception as err:  # noqa: BLE001 — never lose the computed arm summaries
            # The arm summaries above are already computed; a refuse-gate crash must
            # not abort before summary.json is written and discard the whole run.
            refuse_gate = {"error": f"{type(err).__name__}: {err}"}
            print(
                f"refuse-gate failed ({type(err).__name__}: {err}); "
                "arm summaries preserved, writing summary.json anyway"
            )

    # ``cost`` sits beside the scored fields under the same ``arms.<arm>.cost`` path
    # the pooled driver writes, so one reader works on both drivers' summary.json.
    arms_block = {k: asdict(v) for k, v in summaries.items()}
    for arm_name, block in arms_block.items():
        block["cost"] = arm_costs[arm_name]
        # ``errors`` and ``treatment`` belong under ``arms.<arm>`` — the path
        # ``eval.index._undelivered`` reads and the pooled driver writes. They used to
        # land only under ``ex_crosscheck.<arm>``, so even a manual ``index_run`` over
        # this driver's output found no treatment block and, per its own rule, read
        # the absence as "predates the check" rather than as a failure.
        extra = crosschecks.get(arm_name) or {}
        for key in ("errors", "treatment"):
            if key in extra:
                block[key] = extra[key]

    result = {
        "db_id": db_id,
        "n_train": len(train),
        "n_test": len(test),
        "arms": arms_block,
        "deltas": {
            "curated_minus_baseline_ex": _delta(
                curated_s.ex_lenient, baseline_s.ex_lenient
            ),
            "curated_sme_minus_curated_ex": _delta(
                curated_sme_s.ex_lenient, curated_s.ex_lenient
            ),
            "curated_minus_baseline_decoy_touch": _delta(
                curated_s.decoy_touch_rate, baseline_s.decoy_touch_rate
            ),
            "curated_sme_minus_curated_decoy_touch": _delta(
                curated_sme_s.decoy_touch_rate, curated_s.decoy_touch_rate
            ),
        },
        "ex_crosscheck": crosschecks,
        "corpus_validation": corpus_validation,
        "curator_errors": curator_errors,
        "sme_fold": sme_fold,
        "refuse_gate": refuse_gate,
        "gold_hash_self_check": gold_check,
        "serve_policy": {
            "hard_block_suspect_columns": settings.hard_block_suspect_columns,
            "grade_semantic_failures": settings.grade_semantic_failures,
            "note": (
                "grade_semantic_failures=True: coverage/L3–L5/execution exhaustion "
                "deliver SQL with unverified assurance (§6); L2 + refuse-gate stay hard"
            ),
        },
        "leakage": {
            "train_test_disjoint": True,
            "sme_brief_checked": brief_checked,
        },
    }
    (run_root / "summary.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    manifest = build_manifest(
        db_id=db_id,
        bird_dir=bird_dir,
        pg_dsn=pg_dsn,
        max_agent_steps=max_agent_steps,
        skip_agent=skip_agent,
        model_name=settings.models.llm_model,
        resolved_prompts=resolved_prompts,
    )
    (run_root / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )

    connector.close()
    return result


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Eval-ladder BIRD accuracy experiment")
    parser.add_argument("--db", required=True, help="BIRD db_id / Postgres schema")
    parser.add_argument(
        "--bird-dir",
        type=Path,
        default=Path("../BIRD-Data-Obfuscation"),
        help="Path to BIRD-Data-Obfuscation checkout",
    )
    parser.add_argument(
        "--pg-dsn",
        default="host=127.0.0.1 port=5435 dbname=bird user=bird password=bird",
    )
    parser.add_argument("--out", type=Path, default=Path("runs"))
    parser.add_argument("--max-agent-steps", type=int, default=25)
    parser.add_argument(
        "--skip-agent",
        action="store_true",
        help="Deterministic seed-only curation + StaticChatClient (offline smoke)",
    )
    parser.add_argument("--limit", type=int, default=None, help="Cap test questions")
    parser.add_argument(
        "--resume-curated",
        type=Path,
        default=None,
        help="Reuse an existing corpus_curated directory",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=None,
        help=(
            "Serve-loop worker threads (overrides [eval] workers in "
            "governed_bi.toml; default 1 = serial). Size to your Postgres "
            "max_connections; each worker holds its own connection + graph."
        ),
    )
    parser.add_argument(
        "--prompt",
        action="append",
        metavar="STAGE=VARIANT",
        default=None,
        help=(
            "Select a registered prompt variant for one stage, e.g. "
            "--prompt agent_core=v2 (repeatable). Default: every stage on v1. "
            "An unknown stage or variant is an error, never a fallback to v1."
        ),
    )
    args = parser.parse_args(argv)

    try:
        prompt_overrides = parse_cli_overrides(args.prompt)
    except (KeyError, ValueError) as err:
        # Usage error, not a traceback — and it exits before any database work.
        parser.error(str(err.args[0] if err.args else err))

    # CLI overrides config; config overrides the code default of 1.
    workers = args.workers if args.workers is not None else load_settings().serve_worker_count()
    workers = resolve_workers(workers)

    bird_dir = args.bird_dir.resolve()
    run_dir = args.out / f"{_utc_ts()}_{args.db}"
    print(f"run dir: {run_dir}")
    try:
        result = run_experiment(
            db_id=args.db,
            bird_dir=bird_dir,
            pg_dsn=args.pg_dsn,
            out_dir=run_dir,
            max_agent_steps=args.max_agent_steps,
            skip_agent=args.skip_agent,
            limit=args.limit,
            resume_curated=args.resume_curated,
            serve_workers=workers,
            prompt_variants=prompt_overrides,
        )
        print(json.dumps(result["arms"], indent=2))
        print("deltas:", json.dumps(result["deltas"], indent=2))
        # Gate this driver's output the same way the pooled one is gated. It never
        # touched the ledger before, so nothing it produced could be marked
        # not-quotable no matter what went wrong — and it is the driver whose numbers
        # were quoted. Wrapped: `summary.json` is already on disk, so an indexing
        # fault must cost visibility, not the run.
        try:
            from .index import index_run

            record = index_run(run_dir)
            if not record.get("quotable"):
                print(f"\n*** run indexed as NOT quotable: {run_dir}")
                for reason in record.get("not_quotable_because") or []:
                    print(f"  - {reason}")
        except Exception as err:  # noqa: BLE001
            print(f"*** WARNING: could not index run {run_dir}: {err}")
    finally:
        # Deterministic trace delivery: this is a short-lived process, so flush the
        # background exporter rather than trusting the atexit hook (LF1).
        from ..obs import flush_tracing

        flush_tracing()


if __name__ == "__main__":
    main()
