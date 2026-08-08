"""Run the pooled data-lake eval end to end. Crash-safe, resumable, bounded concurrency.

    uv run --frozen python tools/run_datalake_eval.py --workers 2 --effort xhigh --resume

In ``tools/`` rather than a scratchpad because the 1 351-question arm takes hours: it will be
interrupted, resumed, and re-read by someone who did not start it.

Three properties the earlier scratchpad driver lacked, each of which cost a run:

* **Rows are appended as they complete.** A driver that writes at the end is one interruption
  away from having measured nothing.
* **``--resume`` keeps what was measured and *retries what crashed*.** A crashed row is not a
  measurement, so skipping it bakes a permanent hole into the artifact and computes the final
  score over a denominator that silently included it.
* **Concurrency is bounded and declared.** ``--workers`` maps to ``harness.run_arm(workers=...)``,
  which gives each thread its own graph and connector. Default 2: three workers at ``xhigh``
  lost 30 of the first 194 questions to ``RateLimitError`` against a 500 k TPM ceiling, and a
  429 raised inside a node is caught by the graph wrapper and marked ``crashed`` — a lost
  measurement rather than a slow one. ``--max-retries`` (default 8) is the other half.

Never prints the DSN or the API key.
"""

from __future__ import annotations

import argparse
import collections
import json
import pathlib
import sys
import threading
import time

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
sys.path.insert(0, str(REPO / "src"))

#: The corpus, in its own repository as of 2026-08-07 (D13). Derived from this file's location,
#: like ``DEFAULT_DATASET``: a relative sibling path resolves against the process's working
#: directory, and the corpus is what ``corpus_content_hash`` identifies.
DEFAULT_CORPUS = REPO.parent / "BIRD-corpus"
DEFAULT_DATASET = REPO.parent / "BIRD-Data-Obfuscation" / "eval_dataset"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus-dir", default=DEFAULT_CORPUS)
    parser.add_argument("--dataset", type=pathlib.Path, default=DEFAULT_DATASET)
    parser.add_argument("--model", default="gpt-5.6-luna")
    parser.add_argument(
        "--effort",
        default="xhigh",
        help="reasoning effort (none/low/medium/high/xhigh); omit with --effort ''",
    )
    parser.add_argument("--top-n", type=int, default=None, help="override route_top_n")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument(
        "--max-retries",
        type=int,
        default=8,
        help="provider retries per call; 429s are retryable and the SDK default of 2 is not "
        "enough at any concurrency",
    )
    parser.add_argument(
        "--embed",
        action="store_true",
        help="build the index with an embedder. Costs ~420k embedding tokens (about $0.01) "
        "and raises the gold-table-coverage ceiling 6-9pp. Off by default so the lexical "
        "arm stays the reproducible baseline.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=240.0,
        help="per-request timeout in seconds. Without one a worker can block forever: a "
        "4-worker run stalled completely for 6+ minutes with 44 live threads and no rows, "
        "because every worker was inside a request that never returned or a backoff that "
        "never ended. A timeout turns that into a retry.",
    )
    parser.add_argument("--per-schema", type=int, default=None, help="cap questions per schema")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--out", type=pathlib.Path, default=None)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument(
        "--force-fresh",
        action="store_true",
        help="start over even though --resume found no artifact but sibling artifacts exist. "
        "Without it that situation aborts, because it is almost always a changed --tag input "
        "rather than a genuine first run.",
    )
    args = parser.parse_args(argv)

    import credentials

    credentials.load_into_environ()
    if not credentials.have(*credentials.OPENAI_KEY_NAMES):
        print("no model credential reachable", file=sys.stderr)
        return 2
    dsn = credentials.secret(*credentials.PG_DSN_NAMES)
    if not dsn:
        print("no database credential reachable", file=sys.stderr)
        return 2

    from langchain.chat_models import init_chat_model

    from governed_bi.datasource.postgres import PostgresConnector
    from governed_bi.eval.arms import live_arm
    from governed_bi.eval.datalake import (
        attach_gold_fingerprints,
        attach_quality_flags,
        dataset_leakage_qids,
        dataset_qid_lists,
        load_questions,
        observed_tokens,
        table_coverage,
    )
    from governed_bi.eval.harness import run_arm
    from governed_bi.govern.policy import GovernancePolicy
    from governed_bi.serve import session as session_mod

    # `max_retries` is not a nicety: a 429 inside a node is marked `crashed`, so a rate limit is
    # a lost measurement. A 3-worker run lost 30 of its first 194 (15%). The SDK default is 2.
    kwargs = {
        "model_provider": "openai",
        "use_responses_api": True,
        "max_retries": max(0, int(args.max_retries)),
        # Bounded, because unbounded is how a run stalls rather than fails. See --timeout.
        "timeout": float(args.timeout),
    }
    if args.effort:
        kwargs["reasoning_effort"] = args.effort
    model = init_chat_model(args.model, **kwargs)

    embedder = None
    vector_cache = None
    if args.embed:
        from governed_bi.model import OpenAIEmbedder
        from governed_bi.retrieve.vector_cache import vector_cache_from_environment

        embedder = OpenAIEmbedder()
        # The persisted store, shared with the server. Without it this driver re-embedded all
        # 13,968 pooled summaries on every invocation — paid tokens, before the first question.
        vector_cache = vector_cache_from_environment(model=embedder.requested_model)

    # One connector for the session and the graph; each worker gets its own below.
    session = session_mod.from_corpus_dir(
        args.corpus_dir,
        connector=PostgresConnector(dsn),
        policy=GovernancePolicy(guard_rules_enabled={}),
        agent_model=model,
        embedder=embedder,
        vector_cache=vector_cache,
    )
    if session.fatal_problems:
        print(f"corpus has {len(session.fatal_problems)} fatal problem(s); refusing", file=sys.stderr)
        for problem in session.fatal_problems:
            print(f"  {problem}", file=sys.stderr)
        return 3
    schemas = sorted({s for s in session.structure.table_schemas.values() if s})

    questions = load_questions(
        args.dataset / "test_final.jsonl",
        schemas=schemas,
        limit=args.limit,
        per_schema=args.per_schema,
    )
    if questions:
        questions[0].pop("_skipped_uncovered", None)

    # The retrieval channel is in the tag because it is an arm, not a detail: lexical and
    # embedded runs have different ceilings (0.444 vs 0.503 at top_n=3).
    tag = (
        f"{args.model}_{args.effort or 'default'}_top{args.top_n or 'default'}"
        f"_{'embed' if args.embed else 'lexical'}"
    )
    out_path = args.out or pathlib.Path("runs/eval") / f"live_full_{tag}.jsonl"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # ── resume: keep what was *measured*, retry what crashed ──────────────────────
    #
    # The file is rewritten with crashed rows dropped and their question ids requeued, because a
    # crashed row is not a measurement and skipping it leaves a permanent hole in the artifact.
    # A resume that finds nothing is usually a renamed artifact, not a first run: adding the
    # retrieval channel to the tag once orphaned a 515-row artifact and restarted a 1 351-question
    # run from scratch. Refusing here costs one flag and saves a multi-hour run.
    if args.resume and not out_path.exists():
        siblings = sorted(
            path
            for path in out_path.parent.glob(f"live_full_{args.model}_*.jsonl")
            if path != out_path
        )
        if siblings and not args.force_fresh:
            print(
                f"--resume found no artifact at {out_path}, but these exist:",
                file=sys.stderr,
            )
            for path in siblings:
                n_rows = sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
                print(f"    {path}  ({n_rows} rows)", file=sys.stderr)
            print(
                "A changed tag input (--effort, --top-n, --embed) renames the artifact. Rename "
                "or merge the one you meant, or pass --force-fresh to start over.",
                file=sys.stderr,
            )
            return 4

    done: set[str] = set()
    retrying = 0
    if args.resume and out_path.exists():
        kept_lines: list[str] = []
        for line in out_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:  # noqa: BLE001 — a truncated tail is one lost row, not a stop
                continue
            if str(row.get("outcome")) == "crashed":
                retrying += 1
                continue
            kept_lines.append(line)
            done.add(str(row.get("question_id")))
        if retrying:
            body = "".join(f"{line}\n" for line in kept_lines)
            out_path.write_text(body, encoding="utf-8")
        questions = [q for q in questions if q["question_id"] not in done]

    qid_lists = dataset_qid_lists(args.dataset)
    order_sensitive = qid_lists["order_sensitive"]

    # ── what the dataset already knows, and the harness used to ignore ────────────
    #
    # Both files ship with the dataset and had no reader. `attach_gold_fingerprints` supplies the
    # published digest, without which every oracle-arm row is `correct=None`. `attach_quality_flags`
    # marks the questions the dataset warns about, so the headline can be recomputed under a
    # different exclusion policy without paying for the run twice. Both counts are printed: a
    # wiring that silently attaches nothing looks exactly like one that was never called.
    fingerprints = attach_gold_fingerprints(
        questions, args.dataset, dsn_key="rename_decoy", order_sensitive=order_sensitive
    )
    flags = attach_quality_flags(
        questions,
        leakage=dataset_leakage_qids(args.dataset),
        order_sensitive=order_sensitive,
        exec_failed=qid_lists["exec_failed"],
    )
    print(
        "gold digests: "
        + ", ".join(f"{k}={v}" for k, v in fingerprints.items() if v)
        + "\nflagged by the dataset: "
        + (", ".join(f"{k}={v}" for k, v in flags.items() if v) or "none"),
        flush=True,
    )

    if args.top_n is not None:
        for question in questions:
            question["knobs_resolved"] = {
                **session.knobs_resolved,
                "route_top_n": args.top_n,
            }

    total = len(questions)
    print(
        f"model={args.model} effort={args.effort or '(default)'} workers={args.workers} "
        f"top_n={args.top_n or '(register default)'}\n"
        f"corpus={args.corpus_dir} ({len(session.assets_by_id)} assets, {len(schemas)} schemas, "
        f"{len(session.degradations)} degradations)\n"
        f"questions={total}"
        + (f" (resumed, {len(done)} measured" if done else "")
        + (f", {retrying} crashed rows requeued" if retrying else "")
        + (")" if done else ""),
        flush=True,
    )
    if not total:
        print("nothing to do", flush=True)
        return 0

    handle = out_path.open("a", encoding="utf-8")
    lock = threading.Lock()
    started = time.time()
    seen = {"n": 0}

    def append(_index: int, row: dict) -> None:
        with lock:
            handle.write(json.dumps(row, default=str) + "\n")
            handle.flush()
            seen["n"] += 1
            n = seen["n"]
            if n % 10 == 0 or n == total:
                rate = (time.time() - started) / n
                print(
                    f"  {n}/{total}  {rate:.1f}s/question  "
                    f"eta {(total - n) * rate / 60:.0f}min",
                    flush=True,
                )

    try:
        rows = run_arm(
            questions,
            live_arm(session, name=f"live_{tag}"),
            order_sensitive_qids=frozenset(order_sensitive),
            session=session,
            run_id=f"live-{tag}",
            workers=args.workers,
            connector_factory=lambda: PostgresConnector(dsn),
            on_row=append,
        )
    finally:
        handle.close()

    _report(rows, out_path, args, observed_tokens, table_coverage)
    return 0




def _report(rows: list[dict], out_path: pathlib.Path, args, observed_tokens, table_coverage) -> None:
    """Print the whole file, not just this process's rows — a resumed run is one run."""
    every = [json.loads(line) for line in out_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    print(f"\nrows in {out_path}: {len(every)}")

    # `correct` has three values, and reading it with `r.get("correct")` counted a row the grader
    # could not judge as a wrong answer. Split first; every rate below is over graded rows only.
    graded = [r for r in every if r.get("correct") is not None]
    unmeasured = [r for r in every if r.get("correct") is None]
    if unmeasured:
        print(
            f"UNMEASURED        = {len(unmeasured)} row(s) the grader could not judge, "
            "excluded from every EX below (not counted as wrong): "
            + str(dict(collections.Counter(str(r.get("grade_detail")) for r in unmeasured)))
        )

    def ex(rows: list[dict], label: str, note: str = "") -> None:
        ok = sum(1 for r in rows if r.get("correct"))
        print(f"{label:<18}= {ok}/{len(rows)} = {ok / max(1, len(rows)):.3f}{note}")

    ex(graded, "EX")
    ex([r for r in graded if r.get("outcome") != "clarification"], "EX over attempted")

    # The population, stated rather than quietly changed. The dataset says "Exclude both from
    # cross-variant EX": `order_sensitive` golds return a different-but-valid result on the decoy
    # instances, and `exec_failed` golds are degenerate BIRD (>200k rows / 60s timeout) that score
    # `missing_gold` against any engine. Leakage is its third warning. All printed, never applied
    # silently — dropping rows shrinks a denominator with nothing in the artifact saying so.
    from governed_bi.eval.datalake import dataset_leakage_qids, dataset_qid_lists

    lists = dataset_qid_lists(args.dataset)
    leaked = dataset_leakage_qids(args.dataset)
    excluded = lists["order_sensitive"] | lists["exec_failed"] | leaked
    present = {str(r.get("question_id")) for r in graded}
    stable = [r for r in graded if str(r.get("question_id")) not in excluded]
    if len(stable) != len(graded):
        ex(
            stable,
            "EX over clean",
            f"   (excludes {len(graded) - len(stable)}: "
            f"{len(lists['order_sensitive'] & present)} order-sensitive, "
            f"{len(lists['exec_failed'] & present)} exec-failed gold, "
            f"{len(leaked & present)} split-leaked)",
        )
    print("outcomes:", dict(collections.Counter(str(r.get("outcome")) for r in every)))
    crashed = [r for r in every if r.get("outcome") == "crashed"]
    if crashed:
        print("crashes:", dict(collections.Counter(str(r.get("error_type")) for r in crashed)))

    # The EX ceiling first, because it decides how to read everything below it: a question whose
    # gold tables were never licensed could not have been answered by any model.
    cov = table_coverage(every, _gold_sql_by_qid(args.dataset))
    print(
        f"all gold tables licensed = {cov['all_gold_tables_licensed']:.3f}  "
        f"(some {cov['some_licensed']:.3f}, none {cov['none_licensed']:.3f}, "
        f"unparsed gold {cov['gold_sql_unparsed']})"
    )

    # The funnel, before the flat rates below. Each stage is conditional on the one above, so a
    # drop is attributable: `all_gold_tables_licensed` over correctly-routed questions is a
    # table-selection number; over every question it blends two failures wanting opposite work.
    from governed_bi.eval.datalake import retrieval_funnel

    funnel = retrieval_funnel(every, _gold_sql_by_qid(args.dataset), _gold_db_by_qid(args.dataset))

    def _rate(cell: dict) -> str:
        """A rate, or the word for its absence. Never ``0.0000`` for an empty population."""
        return "unmeasured" if cell["rate"] is None else f"{cell['rate']:.4f}"

    print("\nfunnel (each stage given the one above):")
    for stage, cell in funnel["conditional"].items():
        print(f"  {stage:<28}{_rate(cell):>12}   {cell['n']}/{cell['of']}")
    e2e = funnel["end_to_end"]
    print(f"  {'end to end':<28}{_rate(e2e):>12}   {e2e['n']}/{e2e['of']}")
    print(f"  counts: {json.dumps(funnel['counts'])}")

    # The gates, on the path that actually produces numbers. `eval/report.py`'s only caller was
    # `eval/__main__.py` — SQLite-only, unable to run the live datalake arm — so this driver,
    # `routing_recall.py` and `query_summary_alignment.py` produced every quoted figure without
    # reaching a single quotability gate. Printed rather than enforced: a driver that refused to
    # report a run would lose the run.
    from governed_bi.eval.report import evaluate_arm
    from governed_bi.measure.population import Population

    verdicts = evaluate_arm(Population.of(f"live_{args.model}", every))
    print("\nquotability gates (single-arm; cross-arm distinctness needs a second arm):")
    for verdict in verdicts:
        print(f"  {verdict.render()}")
    blocking = [v for v in verdicts if v.verdict.value != "pass"]
    print(
        "  ALL GATES PASS -- these numbers are quotable as a single arm"
        if not blocking
        else f"  {len(blocking)} gate(s) did not pass; a check that did not happen is not a "
        "check that passed"
    )

    gold = _gold_db_by_qid(args.dataset)
    reach = [
        r
        for r in every
        if any(str(t).startswith(f"{gold.get(r['question_id'], chr(0))}.") for t in (r.get("licensed") or []))
    ]
    # Reachability is over every row (it is a routing fact, true or false regardless of grading);
    # the EX beneath it is over the graded ones only.
    reach_graded = [r for r in reach if r.get("correct") is not None]
    ok = [r for r in reach_graded if r["correct"]]
    print(f"gold schema reachable = {len(reach)}/{len(every)} = {len(reach) / max(1, len(every)):.3f}")
    print(
        f"EX among reachable    = {len(ok)}/{len(reach_graded)} = "
        f"{len(ok) / max(1, len(reach_graded)):.3f}"
    )
    clar_reach = sum(1 for r in reach if r.get("outcome") == "clarification")
    unreach = [r for r in every if r not in reach]
    print(
        f"clarification: {clar_reach}/{len(reach)} when reachable, "
        f"{sum(1 for r in unreach if r.get('outcome') == 'clarification')}/{len(unreach)} when not"
    )
    print("tokens:", json.dumps(observed_tokens(every), indent=2, default=str))


def _gold_sql_by_qid(dataset: pathlib.Path) -> dict[str, str]:
    """``question_id -> sql_rename``. The statement written against the obfuscated schemas,
    which is what this database is; ``sql_base`` and ``sql_sqlite`` do not execute here."""
    out: dict[str, str] = {}
    for line in (dataset / "test_final.jsonl").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            row = json.loads(line)
            if row.get("sql_rename"):
                out[str(row["question_id"])] = str(row["sql_rename"])
    return out


def _gold_db_by_qid(dataset: pathlib.Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in (dataset / "test_final.jsonl").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            row = json.loads(line)
            out[str(row["question_id"])] = str(row["db_id"])
    return out


if __name__ == "__main__":
    raise SystemExit(main())
