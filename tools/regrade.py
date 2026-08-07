"""Re-score a finished eval artifact with the **current** grader. Database only, no model.

    uv run --frozen python tools/regrade.py runs/eval/live_full_....jsonl

**Why this can exist at all.** EX is graded on executed *result sets*, not on SQL text, so a
change to the grader is replayable: re-execute the prediction and the gold, compare again, and
the model is never called. A grader that compared SQL strings would make every grader fix cost
a full re-run.

It writes ``<artifact>.regraded.jsonl`` and **reports how many rows flipped in each
direction**. It does not overwrite the input: a re-scored artifact and an original that
disagree is exactly the situation where you want both, and "the number changed" is a claim
that has to be inspectable rather than asserted.

Never prints the DSN.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from collections import Counter

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
sys.path.insert(0, str(REPO / "src"))

DEFAULT_DATASET = REPO.parent / "BIRD-Data-Obfuscation" / "eval_dataset"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", type=pathlib.Path)
    parser.add_argument("--dataset", type=pathlib.Path, default=DEFAULT_DATASET)
    parser.add_argument("--out", type=pathlib.Path, default=None)
    args = parser.parse_args(argv)

    import credentials

    credentials.load_into_environ()
    dsn = credentials.secret(*credentials.PG_DSN_NAMES)
    if not dsn:
        print("no database credential reachable", file=sys.stderr)
        return 2

    import psycopg

    from governed_bi.eval.datalake import dataset_qid_lists
    from governed_bi.eval.grade import grade_turn

    gold_sql: dict[str, str] = {}
    order_sensitive: set[str] = set()
    for line in (args.dataset / "test_final.jsonl").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            row = json.loads(line)
            if row.get("sql_rename"):
                gold_sql[str(row["question_id"])] = str(row["sql_rename"])
    # One reader for this file, in `eval/datalake.py`. Both tools had their own copy and
    # both asked for a key the file has never carried.
    order_sensitive = dataset_qid_lists(args.dataset)["order_sensitive"]

    rows = [
        json.loads(line)
        for line in args.artifact.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    out_path = args.out or args.artifact.with_suffix(".regraded.jsonl")

    flips = Counter()
    cache: dict[str, tuple] = {}

    def run(cur, sql: str):
        cur.execute(sql)
        return [d.name for d in (cur.description or ())], [list(r) for r in cur.fetchall()]

    with psycopg.connect(dsn) as conn, conn.cursor() as cur, out_path.open(
        "w", encoding="utf-8"
    ) as handle:
        for row in rows:
            qid = str(row.get("question_id"))
            was = bool(row.get("correct"))
            gold = gold_sql.get(qid)
            pred = row.get("generated_sql")

            if not gold or not pred or row.get("outcome") == "clarification":
                # Nothing to re-grade: a paused turn produced no statement, and a question
                # with no gold has no reference. Carried through unchanged rather than
                # silently recounted as wrong.
                flips["unchanged (nothing to grade)"] += 1
                handle.write(json.dumps(row, default=str) + "\n")
                continue

            conn.rollback()
            try:
                pcols, prows = run(cur, str(pred))
            except Exception:
                pcols, prows = None, None
            if qid in cache:
                gcols, grows = cache[qid]
            else:
                conn.rollback()
                try:
                    gcols, grows = run(cur, gold)
                except Exception:
                    gcols, grows = None, None
                cache[qid] = (gcols, grows)

            verdict = grade_turn(
                outcome=str(row.get("outcome")),
                pred_columns=pcols,
                pred_rows=prows,
                gold_columns=gcols,
                gold_rows=grows,
                order_sensitive=qid in order_sensitive,
            )
            # Not coerced: a regrade that cannot judge a row must leave it unmeasured rather than
            # record it as wrong, the same rule as `grade_turn`. `flips` below reads truthiness,
            # so an unmeasured outcome lands in "correct -> wrong" only if it *was* correct, and
            # `grade_detail` names why.
            now = verdict["correct"]
            row["correct"] = now
            row["gold_fingerprint"] = verdict.get("gold_fingerprint")
            row["pred_fingerprint"] = verdict.get("pred_fingerprint")
            row["grade_detail"] = verdict.get("detail")
            row["regraded"] = True
            flips[
                "wrong -> correct" if now and not was
                else "correct -> wrong" if was and not now
                else "unchanged"
            ] += 1
            handle.write(json.dumps(row, default=str) + "\n")

    total = len(rows) or 1
    regraded = [
        json.loads(line)
        for line in out_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    after = sum(1 for r in regraded if r.get("correct"))
    before = after - flips.get("wrong -> correct", 0) + flips.get("correct -> wrong", 0)
    print(f"rows: {len(rows)}")
    for kind, n in flips.most_common():
        print(f"  {n:>5}  {kind}")
    # Both numbers, always. "EX went up" is a claim about a delta, and printing only the new
    # value makes the reader take the delta on trust.
    print(f"\nEX before: {before}/{total} = {before / total:.3f}")
    print(f"EX after : {after}/{total} = {after / total:.3f}")
    print(f"wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
