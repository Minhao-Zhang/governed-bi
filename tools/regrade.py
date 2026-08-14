"""Re-score a finished eval artifact with the **current** grader. Database only, no model.

    uv run --frozen python tools/regrade.py runs/eval/live_full_....jsonl

Replayable because EX is graded on executed *result sets*, not SQL text: re-execute the
prediction and the gold and compare again, with no model call. A grader comparing SQL strings
would make every grader fix cost a full re-run.

Writes ``<artifact>.regraded.jsonl`` and reports how many rows flipped in each direction. The
input is never overwritten — "the number changed" is a claim that has to stay inspectable.

Never prints the DSN.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from collections import Counter

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

DEFAULT_DATASET = REPO.parent / "BIRD-Data-Obfuscation" / "eval_dataset"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", type=pathlib.Path)
    parser.add_argument("--dataset", type=pathlib.Path, default=DEFAULT_DATASET)
    parser.add_argument("--out", type=pathlib.Path, default=None)
    args = parser.parse_args(argv)

    from governed_bi import credentials

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
                # Nothing to re-grade: a paused turn produced no statement, a question with no
                # gold has no reference. Carried through rather than silently recounted as wrong.
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
            # Not coerced: a regrade that cannot judge a row leaves it unmeasured rather than
            # wrong, the same rule as `grade_turn`. `flips` below reads truthiness, so an
            # unmeasured row lands in "correct -> wrong" only if it *was* correct.
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

    regraded = [
        json.loads(line)
        for line in out_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    print(f"rows: {len(rows)}")
    for kind, n in flips.most_common():
        print(f"  {n:>5}  {kind}")
    print()
    print(_regrade_report(rows, regraded))
    print(f"wrote {out_path}")
    return 0


def _regrade_report(before_rows: list[dict], after_rows: list[dict]) -> str:
    """Both EX values and the paired test between them, through ``measure/``.

    **Three things were wrong here and all pointed the same way** (audit E1). ``after`` was
    ``sum(1 for r in regraded if r.get("correct"))``, so a row this tool could not judge counted
    as *wrong* — against ``eval/grade.py``'s explicit "callers must propagate the ``None`` rather
    than coerce it", and against the comment fifteen lines above, which says exactly that about the
    row while the headline coerced it anyway. The denominator was every row, unmeasured included.
    And two rates were printed with no paired test, while this function's own ``flips`` counter
    **is** the McNemar table: ``wrong -> correct`` is ``only_b``, ``correct -> wrong`` is
    ``only_a``. A regrade is the most tightly paired comparison this repository ever runs — same
    questions, same run, one grader change — so a delta without a p-value or a minimum detectable
    effect discarded the one thing that made it interpretable.

    ``headline_ex`` and ``mcnemar`` rather than arithmetic here: ``Population`` refuses to count a
    row whose outcome field is absent, and ``Measured`` renders unmeasured as unmeasured instead of
    as a number.
    """
    from governed_bi.eval.report import headline_ex  # noqa: PLC0415
    from governed_bi.measure.population import Population  # noqa: PLC0415
    from governed_bi.measure.stats import mcnemar  # noqa: PLC0415

    def population(label: str, rows: list[dict]) -> Population:
        return Population.of(
            label,
            [
                {"question_id": str(r.get("question_id")), "correct": r.get("correct")}
                for r in rows
            ],
        )

    by_id = {str(r.get("question_id")): r for r in after_rows}
    read = {str(r.get("question_id")) for r in before_rows}
    # **Refused, not reconciled.** The first version filled a missing `after` row from the
    # corresponding `before` row, which made a row the regrade failed to write read as
    # *unchanged* — and ignored an extra row on the other side entirely. Both are the shape this
    # whole audit is about: a discrepancy resolved into a plausible number. A regrade writes every
    # row it read, so a mismatch is a broken run and not a comparison to be salvaged.
    if read != set(by_id):
        lost = sorted(read - set(by_id))[:5]
        extra = sorted(set(by_id) - read)[:5]
        return (
            f"not comparable: the regrade read {len(read)} row(s) and wrote {len(by_id)}. "
            f"missing from the output: {lost or 'none'}; not in the input: {extra or 'none'}. "
            "Neither rate is reported, because a paired comparison over a set that moved is not "
            "paired."
        )

    before = population("before", before_rows)
    # Same unit order as `before`: `mcnemar` pairs on the unit key and refuses a mismatched set
    # rather than intersecting it, which is the property the rival copy in
    # `tools/query_summary_alignment.py` did not have.
    after = population("after", [by_id[str(r.get("question_id"))] for r in before_rows])

    lines = [
        f"EX before: {headline_ex(before).render()}",
        f"EX after : {headline_ex(after).render()}",
    ]
    unmeasured = sum(1 for r in after_rows if r.get("correct") is None)
    if unmeasured:
        lines.append(
            f"unmeasured: {unmeasured} row(s) the regrade could not judge. They are not wrong; "
            "they are outside both rates above."
        )
    lines.append(f"paired    : {mcnemar(before, after, 'correct').render()}")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
