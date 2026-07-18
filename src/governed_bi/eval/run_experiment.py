"""One-command eval-ladder accuracy experiment (plan W4/W5).

Run::

    uv run --extra agents --extra postgres python -m governed_bi.eval.run_experiment \\
      --db cs_semester \\
      --bird-dir ../BIRD-Data-Obfuscation \\
      --pg-dsn "host=127.0.0.1 port=5435 dbname=bird user=bird password=bird" \\
      --out runs/
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..config import DataSourceConfig, Environment, Settings, load_dotenv, load_settings
from ..corpus import load_corpus
from ..corpus.schemas import ReliabilityStatus, TableAsset
from ..gateway import Gateway, Identity
from ..gateway.connectors.postgres import PostgresConnector
from .arms import _touches_suspect, agent_solver
from .bird_loader import load_bird_items
from .hash_grade import (
    crosscheck_execution_match,
    load_gold_hashes,
    load_trap_columns,
    score_sql_hashes,
    validate_gold_hashes_live,
)


@dataclass
class ArmSummary:
    arm: str
    n: int
    ex_lenient: float
    ex_strict: float
    refusal_rate: float
    decoy_touch_rate: float
    conditional_ex_lenient: float  # EX among non-refused only (None-rate excluded)
    by_difficulty: dict[str, float]


def _utc_ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


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
        mpath = d / "run_manifest.json"
        if not mpath.exists():
            continue
        m = json.loads(mpath.read_text(encoding="utf-8"))
        err, fix_err = m.get("error"), m.get("fix_pass_error")
        if err or fix_err:
            out[arm] = {
                "error": (err or "").splitlines()[0] if err else None,
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
) -> tuple[list[dict[str, Any]], ArmSummary, dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    n_correct = 0
    n_strict = 0
    n_refused = 0
    n_decoy = 0
    n_produced = 0
    n_xcheck = 0
    n_xcheck_agree = 0
    by_diff_correct: dict[str, list[bool]] = {}

    for item in items:
        qid = item.question_id or item.question
        t0 = time.perf_counter()
        try:
            sql = solver.solve(item.question)
        except Exception as err:
            sql = None
            err_msg = str(err)
        else:
            err_msg = None
        latency = time.perf_counter() - t0

        gold = gold_hashes.get(str(qid))
        grade = score_sql_hashes(sql, gold, gateway, identity, bird_dir)
        if err_msg and grade.get("error") in (None, "refusal"):
            grade["error"] = err_msg

        xcheck = crosscheck_execution_match(sql, item.sql, gateway)
        if xcheck is not None:
            n_xcheck += 1
            if xcheck == bool(grade["correct"]):
                n_xcheck_agree += 1

        refused = sql is None
        if refused:
            n_refused += 1
        else:
            n_produced += 1
            if _touches_suspect(sql, suspect_columns, dialect):
                n_decoy += 1
        if grade["correct"]:
            n_correct += 1
        if grade["correct_strict"]:
            n_strict += 1

        diff = item.difficulty or "unknown"
        by_diff_correct.setdefault(diff, []).append(bool(grade["correct"]))

        meta = dict(getattr(solver, "last_solve_meta", None) or {})
        rows.append(
            {
                "request_id": str(qid),
                "question_id": str(qid),
                "arm": arm,
                "generated_sql": sql,
                "latency_sec": round(latency, 4),
                "usage": None,
                "correct": grade["correct"],
                "correct_strict": grade["correct_strict"],
                "error": grade.get("error"),
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
            }
        )

    n = len(items)
    summary = ArmSummary(
        arm=arm,
        n=n,
        ex_lenient=n_correct / n if n else 0.0,
        ex_strict=n_strict / n if n else 0.0,
        refusal_rate=n_refused / n if n else 0.0,
        decoy_touch_rate=n_decoy / n_produced if n_produced else 0.0,
        conditional_ex_lenient=(n_correct / n_produced) if n_produced else 0.0,
        by_difficulty={
            d: (sum(1 for x in xs if x) / len(xs) if xs else 0.0)
            for d, xs in sorted(by_diff_correct.items())
        },
    )
    # Attach cross-check agreement onto the generations sidecar via a sentinel row
    # isn't ideal; return it through the summary dict in the caller instead.
    summary_extra = {
        "ex_crosscheck_n": n_xcheck,
        "ex_crosscheck_agree_rate": (n_xcheck_agree / n_xcheck) if n_xcheck else None,
    }
    return rows, summary, summary_extra


class _RefuseAllSolver:
    """Trivial solver for ``--skip-agent`` offline smoke runs (no live model):
    refuses every question so the layered arms still produce a well-formed run."""

    def __init__(self) -> None:
        self.last_solve_meta: dict = {"refused_by": "no_model"}

    def solve(self, question: str) -> str | None:
        del question
        return None


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
) -> dict[str, Any]:
    """Run baseline/curated/curated_sme for one DB; write generations + summary
    under ``out_dir``."""
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
        )

    # --- curated_sme corpus ---
    # Always rebuild + assert the SME brief (even on --resume-curated) so leakage
    # invariants execute for every headline number.
    desc_dir = (
        bird_dir
        / "data"
        / "train"
        / "train_databases"
        / db_id
        / "database_description"
    )
    brief = build_sme_brief(desc_dir, train)
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
            )
        else:
            responder = SimulatedSme(chat, brief, gateway=gateway)
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

    if lc_model is not None:
        baseline = agent_solver(baseline_corpus_loaded, gateway, settings, identity, model=lc_model)
        curated = agent_solver(curated_corpus_loaded, gateway, settings, identity, model=lc_model)
        curated_sme = agent_solver(
            curated_sme_corpus_loaded, gateway, settings, identity, model=lc_model
        )
    else:
        baseline = curated = curated_sme = _RefuseAllSolver()

    suspect_baseline = _suspect_from_corpus(corpus_baseline, db_id) | trap_cols
    suspect_curated = _suspect_from_corpus(corpus_curated, db_id) | trap_cols
    suspect_curated_sme = _suspect_from_corpus(corpus_curated_sme, db_id) | trap_cols

    summaries: dict[str, ArmSummary] = {}
    crosschecks: dict[str, dict] = {}
    for arm_name, solver, suspects in (
        ("baseline", baseline, suspect_baseline),
        ("curated", curated, suspect_curated),
        ("curated_sme", curated_sme, suspect_curated_sme),
    ):
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
        )
        _write_jsonl(run_root / f"generations.{arm_name}.jsonl", gens)
        summaries[arm_name] = summary
        crosschecks[arm_name] = xtra

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

    result = {
        "db_id": db_id,
        "n_train": len(train),
        "n_test": len(test),
        "arms": {k: asdict(v) for k, v in summaries.items()},
        "deltas": {
            "curated_minus_baseline_ex": curated_s.ex_lenient - baseline_s.ex_lenient,
            "curated_sme_minus_curated_ex": curated_sme_s.ex_lenient - curated_s.ex_lenient,
            "curated_minus_baseline_decoy_touch": (
                curated_s.decoy_touch_rate - baseline_s.decoy_touch_rate
            ),
            "curated_sme_minus_curated_decoy_touch": (
                curated_sme_s.decoy_touch_rate - curated_s.decoy_touch_rate
            ),
        },
        "ex_crosscheck": crosschecks,
        "corpus_validation": corpus_validation,
        "curator_errors": curator_errors,
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
    manifest = {
        "db_id": db_id,
        "bird_dir": str(bird_dir),
        "pg_dsn_host": "127.0.0.1:5435",
        "created_at_utc": _utc_ts(),
        "max_agent_steps": max_agent_steps,
        "skip_agent": skip_agent,
        "serve_path": "agent_core",  # agent-only serve (ADR 0002)
        "model": settings.models.llm_model,
    }
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
    args = parser.parse_args(argv)

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
        )
        print(json.dumps(result["arms"], indent=2))
        print("deltas:", json.dumps(result["deltas"], indent=2))
    finally:
        # Deterministic trace delivery: this is a short-lived process, so flush the
        # background exporter rather than trusting the atexit hook (LF1).
        from ..obs import flush_tracing

        flush_tracing()


if __name__ == "__main__":
    main()
