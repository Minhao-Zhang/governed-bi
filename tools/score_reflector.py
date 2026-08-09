"""Score the reflector against gold on runs that already happened.

    uv run --frozen python tools/score_reflector.py runs/eval/live_full_*.jsonl --dry-run
    uv run --frozen python tools/score_reflector.py runs/eval/live_full_*.jsonl --limit 100

The one question this answers: when the reflector says "this is wrong", how often is EX actually
0? If that is about the base rate, the reflector cannot tell right from wrong here, and a retry
loop on top of it re-rolls a draw after seeing it — what ``n_re_served``'s gate exists to catch.
One utility-model call per already-graded row answers that far cheaper than a paid arm.

It calls ``reflect_on`` from ``serve/nodes/reflect.py``, not a copy of its prompt: an offline
score is evidence about the live judge only if it *is* the live judge. The judge never sees gold —
``correct`` and ``gold_sql`` are read after the verdict comes back, by this file.

``correct`` is three-valued; ``None`` rows are excluded rather than counted wrong, since
collapsing them with ``bool()`` would inflate the base rate and flatter the reflector at once.

An eval row does not carry the result table, the eviction record, ``lexical_coverage`` or the
attempt ledger. The first is rebuilt by re-executing the statement (``--no-execute`` turns that
off); the rest are absent, so the header prints which signals were available and this is a lower
bound.

Never prints the DSN or the API key.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import sys
from collections import Counter

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
sys.path.insert(0, str(REPO / "src"))

DEFAULT_DATASET = REPO.parent / "BIRD-Data-Obfuscation" / "eval_dataset"

#: Rows the reflector has nothing to judge, with the reason each is dropped. Counted and
#: reported rather than silently filtered: a population that shrank by 80% is itself a finding.
EXCLUSIONS = {
    "not_answered": "outcome is not `answered`: refusals and crashes are trivially EX=0 and "
                    "would inflate precision on the `wrong` class for free",
    "no_sql": "no generated_sql: nothing to judge",
    "ungraded": "correct is null — the grader could not judge this row, which is our "
                "instrument failing and not the model being wrong",
    "no_question": "the dataset has no question text for this question_id",
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifacts", nargs="+", type=pathlib.Path,
                        help="eval row JSONL files, as written by tools/run_datalake_eval.py")
    parser.add_argument("--dataset", type=pathlib.Path, default=DEFAULT_DATASET)
    parser.add_argument("--model", default="gpt-5.6-luna", help="the judge")
    parser.add_argument("--effort", default="", help="reasoning effort; omit for the default")
    parser.add_argument("--limit", type=int, default=0, help="score at most N rows (0 = all)")
    parser.add_argument("--dry-run", action="store_true",
                        help="build the population and one prompt, call no model, spend nothing")
    parser.add_argument("--no-execute", action="store_true",
                        help="skip re-running the statements; the judge then sees no result table")
    parser.add_argument("--out", type=pathlib.Path, default=None,
                        help="write one JSON line per scored row")
    args = parser.parse_args(argv)

    rows, dropped = _population(args.artifacts, args.dataset)
    _report_population(len(rows), dropped)
    if not rows:
        print(
            "\nNo row carries all of (outcome=answered, generated_sql, a True/False grade, a "
            "question). There is nothing to score — refusing to print an empty table, which "
            "reads as a reflector that scored zero rather than one that was never run.",
            file=sys.stderr,
        )
        return 1
    if args.limit:
        rows = rows[: args.limit]
        print(f"scoring the first {len(rows)} of them (--limit)")

    result_tables: dict[str, dict] = {}
    if not args.no_execute:
        # A dry run must require nothing, so a missing database is only a warning there. It
        # stops a real run: a judge shown no result table is a different instrument.
        rebuilt = _result_tables(rows, required=not args.dry_run)
        if rebuilt is None:
            return 2
        result_tables = rebuilt
    states = [_state_for(row, result_tables.get(str(row["question_id"]))) for row in rows]
    _report_signals(states)

    if args.dry_run:
        from governed_bi.serve.nodes.reflect import reflect_brief, reflect_signals

        sample = states[0]
        print("\n-- one brief, verbatim, so the prompt can be read before it is paid for --\n")
        print(reflect_brief(sample, reflect_signals(sample)))
        print(f"\n--dry-run: {len(states)} rows built, no model called, nothing spent.")
        return 0

    import credentials

    credentials.load_into_environ()
    if not credentials.have(*credentials.OPENAI_KEY_NAMES):
        print("no model credential reachable", file=sys.stderr)
        return 2

    verdicts = _judge_all(states, model=args.model, effort=args.effort)
    scored = [
        {
            "question_id": str(row["question_id"]),
            "arm": row.get("arm"),
            "correct": row["correct"],
            "verdict": verdict.get("verdict"),
            "why_unmeasured": verdict.get("why_unmeasured"),
            "reason": verdict.get("reason"),
        }
        for row, verdict in zip(rows, verdicts)
    ]
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with args.out.open("w", encoding="utf-8") as handle:
            for entry in scored:
                handle.write(json.dumps(entry, default=str) + "\n")
        print(f"\nper-row verdicts: {args.out}")
    _report_score(scored)
    return 0


# ── the population ───────────────────────────────────────────────────────────


def _population(
    artifacts: list[pathlib.Path], dataset: pathlib.Path
) -> tuple[list[dict], Counter]:
    """Scorable rows, plus a count of what was dropped and why."""
    questions = _questions(dataset)
    dropped: Counter = Counter()
    seen: dict[tuple[str, str], dict] = {}
    for path in artifacts:
        if not path.exists():
            print(f"no such artifact: {path}", file=sys.stderr)
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                dropped["unparseable_line"] += 1
                continue
            qid = str(row.get("question_id"))
            reason = _excluded(row, questions)
            if reason:
                dropped[reason] += 1
                continue
            row["question"] = questions[qid]
            # Last write wins per (arm, question), which is what `--resume` produces: a
            # requeued row is appended after the one it replaces.
            seen[(str(row.get("arm")), qid)] = row
    return list(seen.values()), dropped


def _excluded(row: dict, questions: dict[str, str]) -> str | None:
    if row.get("outcome") != "answered":
        return "not_answered"
    if not row.get("generated_sql"):
        return "no_sql"
    if row.get("correct") not in (True, False):
        return "ungraded"
    if str(row.get("question_id")) not in questions:
        return "no_question"
    return None


def _questions(dataset: pathlib.Path) -> dict[str, str]:
    path = dataset / "test_final.jsonl"
    if not path.exists():
        print(
            f"no question file at {path}. The reflector is given the question, so without it "
            "there is no prompt to build — pass --dataset.",
            file=sys.stderr,
        )
        return {}
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        if row.get("question"):
            out[str(row["question_id"])] = str(row["question"])
    return out


def _report_population(kept: int, dropped: Counter) -> None:
    print(f"scorable rows: {kept}")
    for reason, count in sorted(dropped.items()):
        why = EXCLUSIONS.get(reason, "")
        print(f"  dropped {count:>5}  {reason}" + (f" — {why}" if why else ""))


# ── what the judge is shown ─────────────────────────────────────────────────


def _result_tables(rows: list[dict], *, required: bool) -> dict[str, dict] | None:
    """Re-execute each statement to rebuild the result table the artifact does not keep.

    ADR 0006 §11 keeps result rows out of the durable record, and the judge at serve time does
    see them, so scoring without a table measures a different instrument. A statement that no
    longer executes yields no table rather than dropping the row — SQL that will not run is
    itself something a reflector should catch.
    """
    import credentials

    credentials.load_into_environ()
    dsn = credentials.secret(*credentials.PG_DSN_NAMES)
    if not dsn:
        message = (
            "no database credential reachable, so the statements cannot be re-executed and "
            "the judge sees no result table"
        )
        if not required:
            print(f"  {message} (--dry-run: continuing without one)")
            return {}
        print(
            f"{message}. Pass --no-execute to score without one, knowing it measures a "
            "weaker reflector than the graph runs.",
            file=sys.stderr,
        )
        return None

    import psycopg

    out: dict[str, dict] = {}
    failed = 0
    with psycopg.connect(dsn) as conn:
        for row in rows:
            with conn.cursor() as cur:
                try:
                    cur.execute(str(row["generated_sql"]))
                    columns = [d.name for d in (cur.description or ())]
                    fetched = [list(r) for r in cur.fetchall()]
                except Exception:  # noqa: BLE001 — see the docstring; a dead statement is a fact
                    conn.rollback()
                    failed += 1
                    continue
            out[str(row["question_id"])] = {
                "columns": columns,
                "rows": fetched,
                "row_count": len(fetched),
                "truncated": False,
            }
    if failed:
        print(f"  {failed} statement(s) no longer execute; those rows get no result table")
    return out


def _state_for(row: dict, result_table: dict | None) -> dict:
    """A turn-state-shaped mapping, carrying only what ``reflect_signals`` reads.

    Built fresh rather than by mutating the row, so ``correct``, ``gold_sql`` and
    ``gold_fingerprint`` cannot travel into the prompt.
    """
    return {
        "question": row["question"],
        "generated_sql": row.get("generated_sql"),
        "result_table": result_table,
        "licensed": list(row.get("licensed") or ()),
        "turn_index": 1,
    }


def _report_signals(states: list[dict]) -> None:
    """Say which grounded signals the artifact could supply. Absence changes what was measured."""
    with_table = sum(1 for s in states if s.get("result_table"))
    with_licensed = sum(1 for s in states if s.get("licensed"))
    print(
        f"signals available: result_table on {with_table}/{len(states)}, "
        f"licensed on {with_licensed}/{len(states)}; "
        "evicted / lexical_coverage / attempt ledger are NOT on an eval row, so this scores a "
        "reflector shown less than the graph's would be"
    )


def _judge_all(states: list[dict], *, model: str, effort: str) -> list[dict]:
    from governed_bi.model import provider as provider_mod
    from governed_bi.serve.nodes.reflect import reflect_on

    # The judge runs on the agent surface's gateway: it is scoring the same reflector the
    # agent runs, so a judge on a different provider grades one arm with another's model.
    # tools=True because `reflect_on` binds a structured-output tool.
    judge = provider_mod.chat_model(
        model, surface="agent", effort=effort or None, max_retries=8, tools=True
    )

    async def run() -> list[dict]:
        out = []
        for index, state in enumerate(states, start=1):
            verdict, _spent = await reflect_on(judge, state)
            out.append(verdict)
            if index % 20 == 0 or index == len(states):
                print(f"  judged {index}/{len(states)}", flush=True)
        return out

    return asyncio.run(run())


# ── the score ────────────────────────────────────────────────────────────────


def _report_score(scored: list[dict]) -> None:
    """The confusion matrix, then the two rates that decide whether a loop is worth building."""
    labels = ["wrong", "unsure", "answered", "(unmeasured)"]
    matrix = {label: {"ex0": 0, "ex1": 0} for label in labels}
    for entry in scored:
        label = entry["verdict"] if entry["verdict"] in labels else "(unmeasured)"
        matrix[label]["ex0" if entry["correct"] is False else "ex1"] += 1

    n = len(scored)
    n_ex0 = sum(1 for e in scored if e["correct"] is False)
    print("\n-- verdict vs EX ---------------------------------------------")
    print(f"{'verdict':>14}  {'EX=0':>6}  {'EX=1':>6}  {'total':>6}")
    for label in labels:
        cell = matrix[label]
        total = cell["ex0"] + cell["ex1"]
        print(f"{label:>14}  {cell['ex0']:>6}  {cell['ex1']:>6}  {total:>6}")
    print(f"{'all':>14}  {n_ex0:>6}  {n - n_ex0:>6}  {n:>6}")

    print("\n-- the `wrong` class -----------------------------------------")
    _rates("wrong", matrix["wrong"], n_ex0=n_ex0, n=n)
    flagged = {
        "ex0": matrix["wrong"]["ex0"] + matrix["unsure"]["ex0"],
        "ex1": matrix["wrong"]["ex1"] + matrix["unsure"]["ex1"],
    }
    print("\n-- `wrong` or `unsure`, which is what a retry loop would act on --")
    _rates("wrong|unsure", flagged, n_ex0=n_ex0, n=n)

    print(
        "\nRead precision against the base rate on the line above it. A reflector whose "
        "precision equals the base rate has told you nothing: flagging rows at random would "
        "do as well, and a loop driven by it re-rolls draws it cannot distinguish."
    )


def _rates(name: str, cell: dict, *, n_ex0: int, n: int) -> None:
    """Base rate, precision and recall on the flagged class, as counts *and* rates.

    An undefined rate prints as undefined, never ``0.0`` or ``1.0``: a reflector that flagged
    nothing has no precision, and 1.0 there would read as the best possible instrument.
    """
    flagged = cell["ex0"] + cell["ex1"]
    base = n_ex0 / n if n else 0.0
    rows = [("base rate  P[EX=0]", f"{n_ex0}/{n}", round(base, 3), "")]
    if flagged:
        precision = cell["ex0"] / flagged
        rows.append((f"precision  P[EX=0 | {name}]", f"{cell['ex0']}/{flagged}",
                     round(precision, 3), f"lift {round(precision - base, 3)}"))
    if n_ex0:
        rows.append((f"recall     P[{name} | EX=0]", f"{cell['ex0']}/{n_ex0}",
                     round(cell["ex0"] / n_ex0, 3), ""))
    width = max(len(label) for label, *_ in rows)
    for label, counts, rate, extra in rows:
        print(f"  {label:<{width}}  {counts:>9} = {rate:<6} {extra}")
    if not flagged:
        print(f"  the reflector called nothing {name}: precision is undefined, not 1.0")
    if not n_ex0:
        print("  no EX=0 row in the population: recall is undefined, not 0.0")


if __name__ == "__main__":
    raise SystemExit(main())
