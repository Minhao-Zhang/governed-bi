"""One-command three-arm accuracy experiment (plan W4/W5).

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
from .baseline_solver import no_layer_solver
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
) -> tuple[list[dict[str, Any]], ArmSummary]:
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
    resume_a2: Path | None = None,
) -> dict[str, Any]:
    """Run A1/A2/A3 for one DB; write generations + summary under ``out_dir``."""
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
    # Smoke SELECT through the gateway.
    gateway = Gateway(connector, max_rows=200_000, timeout_s=60.0)
    identity = Identity(user="eval", all_access=True)
    smoke = gateway.execute(f'SELECT 1 AS n FROM "{db_id}"."{connector.list_tables()[0]}" LIMIT 1', identity)
    if smoke.row_count < 1 and smoke.rows is not None:
        pass  # empty table is still a valid connection

    base_settings = load_settings()
    datasource = DataSourceConfig(
        kind="postgres",
        db=db_id,
        schema=db_id,
        dsn=pg_dsn,
        multi_schema=False,
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
    if gold_check["n_checked"] and gold_check["agree_rate"] < 1.0:
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
    corpus_a2 = run_root / "corpus_a2"
    corpus_a3 = run_root / "corpus_a3"

    # --- A2 corpus ---
    from ..curator.pipeline import build_curated_corpus, build_curated_corpus_with_sme
    from ..curator.sme import SimulatedSme, assert_brief_no_leakage, build_sme_brief

    if resume_a2 is not None:
        corpus_a2 = Path(resume_a2)
    else:
        build_curated_corpus(
            connector,
            gateway,
            db_id,
            train,
            corpus_a2,
            model=None if skip_agent else lc_model,
            dialect="postgres",
            max_agent_steps=max_agent_steps,
            run_agent=not skip_agent,
        )

    # --- A3 corpus (SME) ---
    # Always rebuild + assert the SME brief (even on --resume-a2) so leakage
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

    existing_a3 = corpus_a2.parent / "corpus_a3"
    if resume_a2 is not None and existing_a3.is_dir() and any(existing_a3.rglob("*.yaml")):
        corpus_a3 = existing_a3
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
                corpus_a3,
                responder=responder,
                a2_root=corpus_a2,
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
                corpus_a3,
                responder=responder,
                a2_root=corpus_a2,
                model=lc_model,
                run_agent_repass=True,
                seed_ledger_if_empty=False,
            )

    # --- Solvers ---
    a1 = no_layer_solver(connector, gateway, chat, schema=db_id, dialect="postgres")

    corpus2 = load_corpus(corpus_a2, schema=db_id)
    corpus3 = load_corpus(corpus_a3, schema=db_id)
    # The layered arms (curator/gold) route through the agentic serve core (ADR
    # 0002 — the only serve path). A1 is model-only. ``--skip-agent`` has no live
    # model, so the layered arms degrade to a trivial refuse-all (offline smoke).
    if lc_model is not None:
        a2 = agent_solver(corpus2, gateway, settings, identity, model=lc_model)
        a3 = agent_solver(corpus3, gateway, settings, identity, model=lc_model)
    else:
        a2 = a3 = _RefuseAllSolver()

    suspect_a1 = trap_cols
    suspect_a2 = _suspect_from_corpus(corpus_a2, db_id) | trap_cols
    suspect_a3 = _suspect_from_corpus(corpus_a3, db_id) | trap_cols

    summaries: dict[str, ArmSummary] = {}
    crosschecks: dict[str, dict] = {}
    for arm_name, solver, suspects in (
        ("a1_baseline", a1, suspect_a1),
        ("a2_curated", a2, suspect_a2),
        ("a3_sme", a3, suspect_a3),
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

    a1s, a2s, a3s = summaries["a1_baseline"], summaries["a2_curated"], summaries["a3_sme"]
    result = {
        "db_id": db_id,
        "n_train": len(train),
        "n_test": len(test),
        "arms": {k: asdict(v) for k, v in summaries.items()},
        "deltas": {
            "a2_minus_a1_ex": a2s.ex_lenient - a1s.ex_lenient,
            "a3_minus_a2_ex": a3s.ex_lenient - a2s.ex_lenient,
            "a2_minus_a1_decoy_touch": a2s.decoy_touch_rate - a1s.decoy_touch_rate,
            "a3_minus_a2_decoy_touch": a3s.decoy_touch_rate - a2s.decoy_touch_rate,
        },
        "ex_crosscheck": crosschecks,
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
    parser = argparse.ArgumentParser(description="Three-arm BIRD accuracy experiment")
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
        "--resume-a2",
        type=Path,
        default=None,
        help="Reuse an existing corpus_a2 directory",
    )
    args = parser.parse_args(argv)

    bird_dir = args.bird_dir.resolve()
    run_dir = args.out / f"{_utc_ts()}_{args.db}"
    print(f"run dir: {run_dir}")
    result = run_experiment(
        db_id=args.db,
        bird_dir=bird_dir,
        pg_dsn=args.pg_dsn,
        out_dir=run_dir,
        max_agent_steps=args.max_agent_steps,
        skip_agent=args.skip_agent,
        limit=args.limit,
        resume_a2=args.resume_a2,
    )
    print(json.dumps(result["arms"], indent=2))
    print("deltas:", json.dumps(result["deltas"], indent=2))


if __name__ == "__main__":
    main()
