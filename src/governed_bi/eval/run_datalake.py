"""Pooled **data-lake** eval driver (D15 scale run).

Where :mod:`governed_bi.eval.run_experiment` pins ONE ``db_id`` to one Postgres
schema, this driver serves the whole BIRD test set with **every** schema living
in one database at once, so the schema router (``analyst.agent`` +
``retrieval.schema_router``) must pick the right schema per question. It is the
"one database, many schemas" experiment (docs/design-decisions.md D15).

Shape (mirrors the eval ladder — three fair rungs, same serve path):

1. **Build** ``baseline`` / ``curated`` / ``curated_sme`` for N ``db_id``s into
   three *shared* corpus roots (each db writes its own ``<root>/<db_id>/``
   subtree). Per-db curator sidecars are relocated so a shared root does not
   clobber them. Resumable: a db whose subtree already has YAML is skipped.
2. **Pool** the test questions (tagged with their ``db_id``), the gold hashes
   (keyed by globally-unique ``question_id``), and a **per-db** suspect-column
   set (the decoy metric is bare-column-name, so pooling suspect sets would
   cross-contaminate — each db's questions are scored against that db's set).
3. **Serve** every arm through ONE unpinned connector (``schema=None`` → the
   engine emits fully schema-qualified ``schema.table`` and the router routes),
   with an embedder for BM25+embedding RRF and (default on here) a single-schema
   LLM pick. Score EX against the pooled gold, and — separately — the routing
   recall (did the router keep the true schema?) so mis-routing is visible.

Run (subset first — this is the heaviest run in the project)::

    uv run python -m governed_bi.eval.run_datalake \\
      --bird-dir ../BIRD-Data-Obfuscation \\
      --pg-dsn "host=127.0.0.1 port=5435 dbname=bird user=bird password=bird" \\
      --limit-dbs 5 --out runs/datalake/

The gold self-check runs against a schema-*pinned* gateway per sampled db (gold
``sql_rename`` is schema-unqualified, so it needs ``search_path``); serve uses
the unpinned gateway. Cross-check EX (which re-executes gold SQL) is therefore
skipped in this mode.
"""

from __future__ import annotations

import argparse
import json
import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

from ..config import DataSourceConfig, Environment, Settings, load_dotenv, load_settings
from ..corpus import load_corpus
from ..gateway import Gateway, Identity
from ..gateway.connectors.postgres import PostgresConnector
from .arms import _touches_suspect, agent_solver
from .parallel import ServeWorker, resolve_workers, run_ordered_pool
from .bird_loader import available_dbs, load_bird_items
from .hash_grade import (
    load_gold_hashes,
    load_trap_columns,
    score_sql_hashes,
    validate_gold_hashes_live,
)
from .run_experiment import (
    _RefuseAllSolver,
    _suspect_from_corpus,
    _utc_ts,
    _validate_corpora,
    _warn_if_not_green,
    _write_jsonl,
)

_ARMS = ("baseline", "curated", "curated_sme")
# Curator sidecar files written to the corpus *root* (not the per-schema subtree):
# on a shared root each db would overwrite the last. Relocated per-db after build.
_SIDECARS = (
    "run_manifest.json",
    "validate_findings.jsonl",
    "adversary_findings.jsonl",
    "sme_clarifications.jsonl",
    "clarifications.jsonl",
)


def _has_yaml(root: Path, db_id: str) -> bool:
    d = root / db_id
    return d.is_dir() and any(d.rglob("*.yaml"))


def _relocate_sidecars(root: Path, db_id: str) -> None:
    """Move any root-level curator sidecars into ``<root>/<db_id>/_build/`` so the
    next db's build cannot clobber them (the scored YAML lives per-schema and is
    already safe)."""
    dest = root / db_id / "_build"
    for name in _SIDECARS:
        src = root / name
        if src.exists():
            dest.mkdir(parents=True, exist_ok=True)
            src.replace(dest / name)


def _pooled_test(
    dataset_dir: Path, db_ids: list[str], *, limit: int | None
) -> list[tuple[Any, str]]:
    """Load the test items for each db, tagged with their ``db_id`` (``EvalItem``
    carries no db_id). ``limit`` caps *per db* to keep a subset run balanced."""
    pairs: list[tuple[Any, str]] = []
    for db in db_ids:
        items = load_bird_items(
            dataset_dir, db, split="test", gold_sql_field="sql_rename"
        )
        if limit is not None:
            items = items[:limit]
        pairs.extend((it, db) for it in items)
    return pairs


def _build_db_corpora(
    *,
    db_id: str,
    pg_dsn: str,
    bird_dir: Path,
    roots: dict[str, Path],
    arms: tuple[str, ...],
    chat_client: Any,
    lc_model: Any,
    skip_agent: bool,
    max_agent_steps: int,
    resume: bool,
) -> None:
    """Build the requested arms for one ``db_id`` into the shared roots. Baseline is
    always built (it's deterministic and anchors the per-db suspect set); curated is
    built when curated or curated_sme is requested; the SME arm only when requested.
    Raises on any build failure (the caller records it and drops the db)."""
    need_curated = "curated" in arms or "curated_sme" in arms
    need_sme = "curated_sme" in arms
    from ..curator.clarifications import StaticResponder
    from ..curator.pipeline import (
        build_baseline_corpus,
        build_curated_corpus,
        build_curated_corpus_with_sme,
    )
    from ..curator.sme import SimulatedSme, assert_brief_no_leakage, build_sme_brief

    connector = PostgresConnector(pg_dsn, schema=db_id)  # build profiles ONE schema
    try:
        if db_id not in connector.list_schemas():
            raise RuntimeError(f"schema {db_id!r} not present on the Postgres instance")
        gateway = Gateway(connector, max_rows=200_000, timeout_s=60.0)
        train = load_bird_items(
            bird_dir / "eval_dataset", db_id, split="train", gold_sql_field="sql_rename"
        )
        test = load_bird_items(
            bird_dir / "eval_dataset", db_id, split="test", gold_sql_field="sql_rename"
        )

        # --- baseline (deterministic, no LLM) ---
        if not (resume and _has_yaml(roots["baseline"], db_id)):
            build_baseline_corpus(connector, db_id, roots["baseline"])
            _relocate_sidecars(roots["baseline"], db_id)

        # --- curated ---
        if need_curated and not (resume and _has_yaml(roots["curated"], db_id)):
            build_curated_corpus(
                connector,
                gateway,
                db_id,
                train,
                roots["curated"],
                model=None if skip_agent else lc_model,
                dialect="postgres",
                max_agent_steps=max_agent_steps,
                run_agent=not skip_agent,
            )
            _relocate_sidecars(roots["curated"], db_id)

        if not need_sme:
            return

        # --- SME brief + leakage invariant (asserted whenever the SME arm builds) ---
        desc_dir = (
            bird_dir / "data" / "train" / "train_databases" / db_id / "database_description"
        )
        brief = build_sme_brief(desc_dir, train)
        assert_brief_no_leakage(
            brief,
            gold_sqls=[it.sql for it in train],
            test_questions=[it.question for it in test],
        )

        # --- curated_sme ---
        if not (resume and _has_yaml(roots["curated_sme"], db_id)):
            if skip_agent:
                responder = StaticResponder(
                    default="Domain column used in analytics; treat as reliable unless samples conflict."
                )
                build_curated_corpus_with_sme(
                    connector,
                    gateway,
                    db_id,
                    train,
                    roots["curated_sme"],
                    responder=responder,
                    curated_root=roots["curated"],
                    model=None,
                    run_agent_repass=False,
                    seed_ledger_if_empty=True,
                )
            else:
                responder = SimulatedSme(chat_client, brief, gateway=gateway)
                build_curated_corpus_with_sme(
                    connector,
                    gateway,
                    db_id,
                    train,
                    roots["curated_sme"],
                    responder=responder,
                    curated_root=roots["curated"],
                    model=lc_model,
                    run_agent_repass=True,
                    seed_ledger_if_empty=False,
                )
            _relocate_sidecars(roots["curated_sme"], db_id)
    finally:
        connector.close()


def _datalake_gold_selfcheck(
    pairs: list[tuple[Any, str]],
    gold_hashes: dict[str, Any],
    pg_dsn: str,
    identity: Identity,
    *,
    per_db: int = 1,
) -> dict[str, Any]:
    """Prove the vendored normalizer agrees with the precomputed gold hashes, using
    a schema-*pinned* gateway per db (gold ``sql_rename`` is unqualified, so it
    needs ``search_path``). Samples ``per_db`` items from each db in the pool.
    """
    by_db: dict[str, list] = {}
    for item, db in pairs:
        if len(by_db.setdefault(db, [])) < per_db:
            by_db[db].append(item)

    n_checked = 0
    n_agree = 0
    per_db_fail: list[str] = []
    for db, items in sorted(by_db.items()):
        conn = PostgresConnector(pg_dsn, schema=db)
        try:
            gw = Gateway(conn, max_rows=200_000, timeout_s=60.0)
            res = validate_gold_hashes_live(
                items, gold_hashes, gw, identity, sample=len(items)
            )
        finally:
            conn.close()
        n_checked += res["n_checked"]
        n_agree += round(res["agree_rate"] * res["n_checked"]) if res["n_checked"] else 0
        if res["n_checked"] and res["agree_rate"] < 1.0:
            per_db_fail.append(db)
    return {
        "n_checked": n_checked,
        "agree_rate": (n_agree / n_checked) if n_checked else 0.0,
        "n_dbs": len(by_db),
        "failed_dbs": per_db_fail,
    }


def _run_pool_arm(
    *,
    arm: str,
    solver,
    pairs: list[tuple[Any, str]],
    gold_hashes: dict[str, Any],
    gateway: Gateway,
    identity: Identity,
    bird_dir: Path,
    suspect_by_db: dict[str, frozenset[str]],
    dialect: str,
    serve_workers: int = 1,
    worker_factory: "Callable[[int], ServeWorker] | None" = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Serve + grade one arm over the pooled (item, db_id) stream. Decoy touches
    use the item's OWN db suspect set; routing recall is scored from the router
    provenance returned in the per-question ``meta``.

    ``serve_workers == 1`` (default) runs the serial loop against the passed
    ``solver`` / ``gateway`` — byte-identical to the pre-concurrency path.
    ``serve_workers > 1`` fans the per-question ``solve+grade`` unit across a
    thread pool of ``worker_factory``-built workers (each its own unpinned
    connector + gateway + graph); results reassemble in the original pair order,
    so rows and every aggregate match the serial run."""
    pairs = list(pairs)
    n = len(pairs)

    def _grade_one(pair: tuple[Any, str], *, solver, gateway) -> dict[str, Any]:
        """Solve + grade ONE pooled (item, db) pair against the given
        (solver, gateway). Returns the row plus the booleans the summary needs, so
        the caller aggregates on one thread in submission order."""
        item, db = pair
        qid = item.question_id or item.question
        t0 = time.perf_counter()
        try:
            sql, meta_raw = solver.solve_with_meta(item.question)
            err_msg = None
        except Exception as err:  # a solver crash is a refusal, not a lost run
            sql, meta_raw, err_msg = None, {}, str(err)
        latency = time.perf_counter() - t0

        gold = gold_hashes.get(str(qid))
        grade = score_sql_hashes(sql, gold, gateway, identity, bird_dir)
        if err_msg and grade.get("error") in (None, "refusal"):
            grade["error"] = err_msg

        meta = dict(meta_raw or {})
        routed = meta.get("routed_schemas") or []
        routed_hit = db in routed
        pick = meta.get("schema_pick")
        pick_hit = (pick == db) if pick is not None else None
        diff = item.difficulty or "unknown"
        row = {
            "request_id": str(qid),
            "question_id": str(qid),
            "db_id": db,
            "arm": arm,
            "generated_sql": sql,
            "latency_sec": round(latency, 4),
            "usage": meta.get("usage"),
            "cost_est_usd": meta.get("cost_est_usd"),
            "correct": grade["correct"],
            "correct_strict": grade["correct_strict"],
            "error": grade.get("error"),
            "difficulty": diff,
            "routed_schemas": routed,
            "routed_hit": routed_hit,
            "schema_pick": pick,
            "pick_hit": pick_hit,
            "total_schemas": meta.get("total_schemas"),
            "refused_by": meta.get("refused_by"),
            "failed_layer": meta.get("failed_layer"),
            "graded_delivery": meta.get("graded_delivery"),
            "tier": meta.get("tier"),
            "semantic_assurance": meta.get("semantic_assurance"),
        }
        return {
            "row": row,
            "db": db,
            "correct": bool(grade["correct"]),
            "correct_strict": bool(grade["correct_strict"]),
            "refused": sql is None,
            "decoy": (
                sql is not None
                and _touches_suspect(sql, suspect_by_db.get(db, frozenset()), dialect)
            ),
            "routed_hit": routed_hit,
            "pick": pick,
            "pick_hit": pick_hit,
            "diff": diff,
        }

    if serve_workers > 1:
        if worker_factory is None:
            raise ValueError("serve_workers > 1 requires a worker_factory")
        bundles = run_ordered_pool(
            pairs,
            workers=serve_workers,
            make_worker=worker_factory,
            run_task=lambda w, pair: _grade_one(pair, solver=w.solver, gateway=w.gateway),
        )
    else:
        bundles = [_grade_one(pair, solver=solver, gateway=gateway) for pair in pairs]

    # --- aggregation on the calling thread, in original pair order ---
    rows: list[dict[str, Any]] = []
    n_correct = n_strict = n_refused = n_produced = n_decoy = 0
    n_routed_hit = n_pick_hit = n_pick = 0
    by_diff: dict[str, list[bool]] = {}
    by_db: dict[str, list[bool]] = {}

    for b in bundles:
        rows.append(b["row"])
        if b["refused"]:
            n_refused += 1
        else:
            n_produced += 1
            if b["decoy"]:
                n_decoy += 1
        if b["correct"]:
            n_correct += 1
        if b["correct_strict"]:
            n_strict += 1
        if b["routed_hit"]:
            n_routed_hit += 1
        if b["pick"] is not None:
            n_pick += 1
            if b["pick_hit"]:
                n_pick_hit += 1
        by_diff.setdefault(b["diff"], []).append(b["correct"])
        by_db.setdefault(b["db"], []).append(b["correct"])

    summary = {
        "arm": arm,
        "n": n,
        "ex_lenient": n_correct / n if n else 0.0,
        "ex_strict": n_strict / n if n else 0.0,
        "refusal_rate": n_refused / n if n else 0.0,
        "decoy_touch_rate": n_decoy / n_produced if n_produced else 0.0,
        "conditional_ex_lenient": (n_correct / n_produced) if n_produced else 0.0,
        # Routing recall: share of questions whose TRUE schema survived routing.
        # This is the ceiling on EX in the data lake — a mis-routed question is 0.
        "routing_recall": n_routed_hit / n if n else 0.0,
        # Single-schema pick accuracy (only when schema_route_llm_pick is on).
        "schema_pick_accuracy": (n_pick_hit / n_pick) if n_pick else None,
        "by_difficulty": {
            d: (sum(1 for x in xs if x) / len(xs) if xs else 0.0)
            for d, xs in sorted(by_diff.items())
        },
        "by_db": {
            d: {"ex_lenient": sum(1 for x in xs if x) / len(xs), "n": len(xs)}
            for d, xs in sorted(by_db.items())
        },
    }
    return rows, summary


def run_datalake(
    *,
    bird_dir: Path,
    pg_dsn: str,
    out_dir: Path,
    db_ids: list[str] | None = None,
    arms: tuple[str, ...] = _ARMS,
    limit_dbs: int | None = None,
    limit: int | None = None,
    max_agent_steps: int = 25,
    skip_agent: bool = False,
    resume: bool = True,
    route_top_k: int = 8,
    route_llm_pick: bool = True,
    use_embedder: bool = True,
    serve_workers: int = 1,
) -> dict[str, Any]:
    """Build all arms for the requested dbs into shared corpora, then serve the
    pooled test set through the unpinned (data-lake) agentic core. Writes
    ``generations.<arm>.jsonl`` + ``summary.json`` + ``manifest.json`` under
    ``out_dir`` and returns the summary dict."""
    load_dotenv()
    dataset_dir = bird_dir / "eval_dataset"
    out_dir.mkdir(parents=True, exist_ok=True)
    roots = {arm: out_dir / f"corpus_{arm}" for arm in _ARMS}

    # --- resolve the db set: requested (or every test db), present on Postgres ---
    probe = PostgresConnector(pg_dsn, schema=None)
    try:
        present = set(probe.list_schemas())
    finally:
        probe.close()
    wanted = db_ids if db_ids is not None else sorted(available_dbs(dataset_dir, "test"))
    if limit_dbs is not None:
        wanted = wanted[:limit_dbs]
    missing = [d for d in wanted if d not in present]
    if missing:
        print(f"*** WARNING: {len(missing)} requested db(s) not on Postgres, skipped: {missing[:10]}")
    wanted = [d for d in wanted if d in present]
    if not wanted:
        raise RuntimeError("no requested db_ids are loaded on the Postgres instance")

    # --- Settings + clients (built once; models resolved from config) ---
    base_settings = load_settings()
    datasource = DataSourceConfig(
        kind="postgres", corpus_pin="datalake", schema=None, dsn=pg_dsn
    )
    settings = Settings.for_env(
        Environment.dev,
        models=base_settings.models,
        datasource=datasource,
        corpus_root=str(out_dir),
    )
    # pipeline-design §6 (deliver-and-grade semantic failures); D15 routing knobs.
    settings = replace(
        settings,
        hard_block_suspect_columns=False,
        grade_semantic_failures=True,
        schema_route_top_k=route_top_k,
        schema_route_llm_pick=route_llm_pick,
    )

    chat_client = None
    lc_model = None
    embedder = None
    if not skip_agent:
        from ..llm import LangChainChatClient, LangChainEmbedder

        chat_client = LangChainChatClient.from_config(settings.models)
        lc_model = chat_client.model
        if use_embedder:
            embedder = LangChainEmbedder.from_config(settings.models)

    # --- BUILD phase (per-db, into shared roots) ---
    built: list[str] = []
    build_errors: dict[str, str] = {}
    for db in wanted:
        try:
            _build_db_corpora(
                db_id=db,
                pg_dsn=pg_dsn,
                bird_dir=bird_dir,
                roots=roots,
                arms=arms,
                chat_client=chat_client,
                lc_model=lc_model,
                skip_agent=skip_agent,
                max_agent_steps=max_agent_steps,
                resume=resume,
            )
            built.append(db)
            print(f"  built corpora: {db} ({len(built)}/{len(wanted)})")
        except Exception as err:  # one bad db must not lose the whole run
            build_errors[db] = f"{type(err).__name__}: {err}"
            print(f"*** build FAILED for {db!r} — dropped from pool: {build_errors[db]}")
    if not built:
        raise RuntimeError(f"every db failed to build: {build_errors}")

    # --- POOL gold + test + per-db suspects (only successfully-built dbs) ---
    pairs = _pooled_test(dataset_dir, built, limit=limit)
    gold_hashes: dict[str, Any] = {}
    suspect_by_db: dict[str, frozenset[str]] = {}
    for db in built:
        gold_hashes.update(load_gold_hashes(bird_dir, db_id=db))
        trap = load_trap_columns(bird_dir, db)
        suspect_by_db[db] = _suspect_from_corpus(roots["baseline"], db) | trap

    # --- SERVE phase: ONE unpinned connector spans every schema ---
    connector = PostgresConnector(pg_dsn, schema=None)
    gateway = Gateway(connector, max_rows=200_000, timeout_s=60.0)
    identity = Identity(user="eval", all_access=True)

    # Gold self-check on schema-pinned gateways (search_path per db).
    gold_check = _datalake_gold_selfcheck(pairs, gold_hashes, pg_dsn, identity)
    if not gold_check["n_checked"]:
        connector.close()
        raise RuntimeError(f"gold self-check verified 0 rows: {gold_check}")
    if gold_check["agree_rate"] < 1.0:
        connector.close()
        raise RuntimeError(f"gold self-check disagreed with live gold: {gold_check}")

    # Load each requested arm's MERGED corpus (schema=None → every built subtree).
    corpora = {arm: load_corpus(roots[arm], schema=None) for arm in arms}
    corpus_validation = _validate_corpora(corpora)  # no connector: public-default
    _warn_if_not_green(corpus_validation)

    # Serve concurrency (docs/plans/eval-concurrency-design.md): only fan out when
    # there is a live model — the offline refuse-all path has nothing to overlap.
    effective_workers = serve_workers if lc_model is not None else 1
    if effective_workers > 1:
        print(
            f"  serve concurrency: {effective_workers} worker(s)/arm — each owns "
            f"its own unpinned connector+gateway+graph (schema=None)"
        )

    def _make_arm_factory(arm: str) -> "Callable[[int], ServeWorker]":
        """Per-worker (connector, gateway, solver) factory for one arm. Mirrors
        the shared connector: ``schema=None`` (the pooled data-lake driver spans
        every schema), one graph per worker, distinct ``session_id`` per worker."""

        def factory(idx: int) -> ServeWorker:
            conn = PostgresConnector(pg_dsn, schema=None)
            gw = Gateway(conn, max_rows=200_000, timeout_s=60.0)
            slv = agent_solver(
                corpora[arm],
                gw,
                settings,
                identity,
                model=lc_model,
                embedder=embedder,
                session_id=f"eval-{arm}-w{idx}",
            )
            return ServeWorker(connector=conn, gateway=gw, solver=slv)

        return factory

    summaries: dict[str, Any] = {}
    try:
        for arm in arms:
            if lc_model is not None:
                solver = agent_solver(
                    corpora[arm],
                    gateway,
                    settings,
                    identity,
                    model=lc_model,
                    embedder=embedder,
                    session_id=f"eval-{arm}",
                )
            else:
                solver = _RefuseAllSolver()
            worker_factory = (
                _make_arm_factory(arm) if effective_workers > 1 else None
            )
            rows, summary = _run_pool_arm(
                arm=arm,
                solver=solver,
                pairs=pairs,
                gold_hashes=gold_hashes,
                gateway=gateway,
                identity=identity,
                bird_dir=bird_dir,
                suspect_by_db=suspect_by_db,
                dialect="postgres",
                serve_workers=effective_workers,
                worker_factory=worker_factory,
            )
            _write_jsonl(out_dir / f"generations.{arm}.jsonl", rows)
            summaries[arm] = summary
            print(
                f"  [{arm}] EX={summary['ex_lenient']:.3f} "
                f"routing_recall={summary['routing_recall']:.3f} "
                f"refuse={summary['refusal_rate']:.3f}"
            )
    finally:
        connector.close()

    deltas: dict[str, float] = {}
    if "baseline" in summaries and "curated" in summaries:
        deltas["curated_minus_baseline_ex"] = (
            summaries["curated"]["ex_lenient"] - summaries["baseline"]["ex_lenient"]
        )
    if "curated" in summaries and "curated_sme" in summaries:
        deltas["curated_sme_minus_curated_ex"] = (
            summaries["curated_sme"]["ex_lenient"] - summaries["curated"]["ex_lenient"]
        )
    result = {
        "mode": "datalake",
        "arms_run": list(arms),
        "n_dbs_requested": len(wanted),
        "n_dbs_built": len(built),
        "built_dbs": built,
        "build_errors": build_errors,
        "n_test": len(pairs),
        "arms": summaries,
        "deltas": deltas,
        "routing": {
            "top_k": route_top_k,
            "llm_pick": route_llm_pick,
            "embedder": bool(embedder),
            "note": (
                "routing_recall per arm is the share of questions whose true schema "
                "survived routing; it caps EX (a mis-routed question scores 0)."
            ),
        },
        "corpus_validation": corpus_validation,
        "gold_hash_self_check": gold_check,
        "serve_policy": {
            "hard_block_suspect_columns": settings.hard_block_suspect_columns,
            "grade_semantic_failures": settings.grade_semantic_failures,
        },
    }
    (out_dir / "summary.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (out_dir / "manifest.json").write_text(
        json.dumps(
            {
                "mode": "datalake",
                "bird_dir": str(bird_dir),
                "pg_dsn_host": "127.0.0.1:5435",
                "created_at_utc": _utc_ts(),
                "model": settings.models.llm_model,
                "route_top_k": route_top_k,
                "route_llm_pick": route_llm_pick,
                "use_embedder": bool(embedder),
                "skip_agent": skip_agent,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return result


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description="Pooled data-lake BIRD eval (D15 scale run)")
    p.add_argument(
        "--bird-dir", type=Path, default=Path("../BIRD-Data-Obfuscation"),
        help="Path to BIRD-Data-Obfuscation checkout",
    )
    p.add_argument(
        "--pg-dsn", default="host=127.0.0.1 port=5435 dbname=bird user=bird password=bird"
    )
    p.add_argument("--out", type=Path, default=Path("runs/datalake"))
    p.add_argument("--dbs", default=None, help="Comma-separated db_ids (default: all test dbs)")
    p.add_argument(
        "--arms",
        default=None,
        help="Comma-separated arms (subset of baseline,curated,curated_sme; default all)",
    )
    p.add_argument("--limit-dbs", type=int, default=None, help="Cap the number of dbs")
    p.add_argument("--limit", type=int, default=None, help="Cap test questions PER db")
    p.add_argument("--max-agent-steps", type=int, default=25)
    p.add_argument("--skip-agent", action="store_true", help="Offline smoke (no model)")
    p.add_argument("--no-resume", action="store_true", help="Rebuild corpora even if present")
    p.add_argument("--route-top-k", type=int, default=8, help="Schema shortlist size")
    p.add_argument("--no-llm-pick", action="store_true", help="Keep shortlist (no single-schema LLM pick)")
    p.add_argument("--no-embedder", action="store_true", help="BM25-only routing (no embeddings)")
    p.add_argument(
        "--workers",
        type=int,
        default=None,
        help=(
            "Serve-loop worker threads (overrides [eval] workers in "
            "governed_bi.toml; default 1 = serial). Size to your Postgres "
            "max_connections; each worker holds its own connection + graph."
        ),
    )
    args = p.parse_args(argv)

    arms = tuple(a.strip() for a in args.arms.split(",")) if args.arms else _ARMS
    bad = [a for a in arms if a not in _ARMS]
    if bad:
        p.error(f"--arms must be a subset of {_ARMS}; unknown: {bad}")

    # CLI overrides config; config overrides the code default of 1.
    workers = args.workers if args.workers is not None else load_settings().serve_worker_count()
    workers = resolve_workers(workers)

    bird_dir = args.bird_dir.resolve()
    out_dir = args.out / _utc_ts()
    print(f"run dir: {out_dir}")
    try:
        result = run_datalake(
            bird_dir=bird_dir,
            pg_dsn=args.pg_dsn,
            out_dir=out_dir,
            db_ids=[d.strip() for d in args.dbs.split(",")] if args.dbs else None,
            arms=arms,
            limit_dbs=args.limit_dbs,
            limit=args.limit,
            max_agent_steps=args.max_agent_steps,
            skip_agent=args.skip_agent,
            resume=not args.no_resume,
            route_top_k=args.route_top_k,
            route_llm_pick=not args.no_llm_pick,
            use_embedder=not args.no_embedder,
            serve_workers=workers,
        )
        print(json.dumps(result["arms"], indent=2, ensure_ascii=False))
        print("deltas:", json.dumps(result["deltas"], indent=2))
    finally:
        from ..obs import flush_tracing

        flush_tracing()


if __name__ == "__main__":
    main()
