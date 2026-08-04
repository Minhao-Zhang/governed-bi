"""Run the pooled data-lake eval end to end. Crash-safe, resumable, bounded concurrency.

    uv run --frozen python tools/run_datalake_eval.py --workers 2 --effort xhigh --resume

**Why this is in ``tools/`` and not the scratchpad.** The 1 351-question arm takes hours,
so it will be interrupted, resumed, and re-read by someone who did not start it. A one-shot
script in a temp directory cannot be any of those things.

Three properties the earlier scratchpad driver lacked, each of which cost a run:

* **Rows are appended as they complete.** A driver that writes at the end is one
  interruption away from having measured nothing. The 3-per-schema run took 50 minutes and
  its process was still holding a database connection long after the artifact was written.
* **``--resume`` keeps what was measured and *retries what crashed*.** A crashed row is not
  a measurement, so skipping it would bake a permanent hole into the artifact and compute the
  final score over a denominator that silently included it.
* **Concurrency is bounded and declared.** ``--workers`` maps to
  ``harness.run_arm(workers=...)``, which gives each thread its own graph and its own
  connector. The default is **2**: three workers at ``xhigh`` lost 30 of the first 194
  questions to ``RateLimitError`` against a 500 k TPM ceiling — a 429 raised inside a node is
  caught by the graph wrapper and the turn is marked ``crashed``, so a rate limit is a lost
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
from datetime import datetime, timezone

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
sys.path.insert(0, str(REPO / "src"))

DEFAULT_CORPUS = "corpora/gold-semantic-layer-20260804"
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
    parser.add_argument("--per-schema", type=int, default=None, help="cap questions per schema")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--out", type=pathlib.Path, default=None)
    parser.add_argument("--resume", action="store_true")
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
    from governed_bi.eval.datalake import load_questions, observed_spend
    from governed_bi.eval.harness import run_arm
    from governed_bi.govern.policy import GovernancePolicy
    from governed_bi.serve import session as session_mod

    # `max_retries` is not a nicety. A 429 raised inside a node is caught by the graph's
    # wrapper and the turn is marked `crashed`, so a rate limit becomes a *lost measurement*
    # rather than a slow one: a 3-worker run over this corpus lost 30 of its first 194
    # questions that way (15%). The SDK default is 2.
    kwargs = {
        "model_provider": "openai",
        "use_responses_api": True,
        "max_retries": max(0, int(args.max_retries)),
    }
    if args.effort:
        kwargs["reasoning_effort"] = args.effort
    model = init_chat_model(args.model, **kwargs)

    # One connector for the session and the graph; each worker gets its own below.
    session = session_mod.from_corpus_dir(
        args.corpus_dir,
        connector=PostgresConnector(dsn),
        policy=GovernancePolicy(guard_rules_enabled={}),
        agent_model=model,
        embedder=None,
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

    tag = f"{args.model}_{args.effort or 'default'}_top{args.top_n or 'default'}"
    out_path = args.out or pathlib.Path("runs/eval") / f"live_full_{tag}.jsonl"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # ── resume: keep what was *measured*, retry what crashed ──────────────────────
    #
    # A crashed row is not a measurement. Skipping it on resume would bake a permanent hole
    # into the artifact — 30 rate-limited questions would stay unanswered no matter how many
    # times the run was resumed, and the final EX would be computed over a denominator that
    # silently included them. So the file is rewritten with the crashed rows dropped, and
    # those question ids go back into the queue.
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

    order_sensitive = _order_sensitive(args.dataset)
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

    _report(rows, out_path, args, observed_spend)
    return 0


def _order_sensitive(dataset: pathlib.Path) -> set[str]:
    path = dataset / "order_sensitive_qids.json"
    if not path.exists():
        return set()
    raw = json.loads(path.read_text(encoding="utf-8"))
    return set(raw if isinstance(raw, list) else raw.get("question_ids") or [])


def _report(rows: list[dict], out_path: pathlib.Path, args, observed_spend) -> None:
    """Print the whole file, not just this process's rows — a resumed run is one run."""
    every = [json.loads(line) for line in out_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    n = len(every) or 1
    correct = sum(1 for r in every if r.get("correct"))
    attempted = [r for r in every if r.get("outcome") != "clarification"]
    print(f"\nrows in {out_path}: {len(every)}")
    print(f"EX                = {correct}/{len(every)} = {correct / n:.3f}")
    print(
        f"EX over attempted = {sum(1 for r in attempted if r.get('correct'))}/{len(attempted)} "
        f"= {sum(1 for r in attempted if r.get('correct')) / max(1, len(attempted)):.3f}"
    )
    print("outcomes:", dict(collections.Counter(str(r.get("outcome")) for r in every)))
    crashed = [r for r in every if r.get("outcome") == "crashed"]
    if crashed:
        print("crashes:", dict(collections.Counter(str(r.get("error_type")) for r in crashed)))

    gold = _gold_db_by_qid(args.dataset)
    reach = [
        r
        for r in every
        if any(str(t).startswith(f"{gold.get(r['question_id'], chr(0))}.") for t in (r.get("licensed") or []))
    ]
    ok = [r for r in reach if r.get("correct")]
    print(f"gold schema reachable = {len(reach)}/{len(every)} = {len(reach) / n:.3f}")
    print(f"EX among reachable    = {len(ok)}/{len(reach)} = {len(ok) / max(1, len(reach)):.3f}")
    clar_reach = sum(1 for r in reach if r.get("outcome") == "clarification")
    unreach = [r for r in every if r not in reach]
    print(
        f"clarification: {clar_reach}/{len(reach)} when reachable, "
        f"{sum(1 for r in unreach if r.get('outcome') == 'clarification')}/{len(unreach)} when not"
    )
    spend = observed_spend(every, model=args.model, asof=datetime.now(timezone.utc).date())
    print("spend:", json.dumps(spend, indent=2, default=str))


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
