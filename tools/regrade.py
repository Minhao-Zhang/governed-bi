"""Re-score a finished eval artifact with the **current** grader. Database only, no model.

    uv run --frozen python tools/regrade.py runs/eval/live_full_....jsonl --out runs/eval/regrade/x.jsonl

Replayable because EX is graded on executed *result sets*, not SQL text: re-execute the
prediction and the gold and compare again, with no model call. A grader comparing SQL strings
would make every grader fix cost a full re-run.

Every statement runs through ``PostgresConnector``, the connector the harness uses: a read-only
session, the register's ``statement_timeout_ms`` and the same row cap. The statements are
model-written, and a regrade on a different connection would measure the connection as well as
the grader.

Each regraded row carries two verdicts on the *same* re-executed rows: ``correct`` from the
current grader and ``correct_digest_rule`` from the BIRD digest comparison the artifact was
graded with. The report pairs recorded against digest rule (re-execution drift, the control) and
digest rule against current (the grader change), so one pass separates the two causes.

Writes ``<artifact>.regraded.jsonl`` unless ``--out`` is given, and refuses an existing output
unless ``--overwrite``. The input is never overwritten. A lost database connection aborts the run
and writes nothing, rather than grading the rest as missing predictions.

Never prints the DSN.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
from collections import Counter
from typing import Any, Callable

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

DEFAULT_DATASET = REPO.parent / "BIRD-Data-Obfuscation" / "eval_dataset"

#: What a regraded row's ``grader`` field names. Bumped when ``eval/grade.py``'s verdict changes.
GRADER = "rows-v3"


class RegradeAborted(RuntimeError):
    """The database went away mid-run. Nothing was written."""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", type=pathlib.Path)
    parser.add_argument("--dataset", type=pathlib.Path, default=DEFAULT_DATASET)
    parser.add_argument("--out", type=pathlib.Path, default=None)
    parser.add_argument(
        "--overwrite", action="store_true", help="replace an existing output file"
    )
    args = parser.parse_args(argv)

    out_path = args.out or args.artifact.with_suffix(".regraded.jsonl")
    if out_path.resolve() == args.artifact.resolve():
        print("--out is the input artifact; refusing to overwrite it", file=sys.stderr)
        return 2
    if out_path.exists() and not args.overwrite:
        print(f"{out_path} exists; pass --overwrite to replace it", file=sys.stderr)
        return 2

    from governed_bi import credentials

    credentials.load_into_environ()
    dsn = credentials.secret(*credentials.PG_DSN_NAMES)
    if not dsn:
        print("no database credential reachable", file=sys.stderr)
        return 2

    from governed_bi.datasource.postgres import PostgresConnector
    from governed_bi.eval.datalake import dataset_qid_lists
    from governed_bi.eval.provenance import git_provenance
    from governed_bi.register.knobs import knob_default

    gold_sql: dict[str, str] = {}
    for line in (args.dataset / "test_final.jsonl").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            row = json.loads(line)
            if row.get("sql_rename"):
                gold_sql[str(row["question_id"])] = str(row["sql_rename"])
    # One reader for this file, in `eval/datalake.py`.
    order_sensitive = dataset_qid_lists(args.dataset)["order_sensitive"]

    rows = [
        json.loads(line)
        for line in args.artifact.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    timeout_ms = int(knob_default("statement_timeout_ms"))
    max_rows = int(knob_default("max_rows"))
    stamp = {
        "grader": GRADER,
        "statement_timeout_ms": timeout_ms,
        "max_rows": max_rows,
        **git_provenance(REPO),
    }

    with PostgresConnector(dsn, max_rows=max_rows, statement_timeout_ms=timeout_ms) as connector:
        try:
            regraded, flips = regrade_rows(
                rows,
                execute=lambda sql: connector.execute(sql)[:2],
                gold_sql=gold_sql,
                order_sensitive=order_sensitive,
                stamp=stamp,
            )
        except RegradeAborted as err:
            print(f"aborted, nothing written: {err}", file=sys.stderr)
            return 3

    # Written only after every row is judged, so an aborted run leaves no partial artifact.
    tmp = out_path.with_name(out_path.name + ".partial")
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text("".join(json.dumps(r, default=str) + "\n" for r in regraded), encoding="utf-8")
    os.replace(tmp, out_path)

    print(f"rows: {len(rows)}   grader {GRADER}   statement_timeout_ms {timeout_ms}")
    for kind, n in flips.most_common():
        print(f"  {n:>5}  {kind}")
    print()
    print("re-execution drift (recorded vs digest rule on re-executed rows):")
    print(_regrade_report(rows, _as_digest_rule(regraded), labels=("recorded", "digest rule")))
    print()
    print("grader change (digest rule vs current grader, same rows):")
    print(
        _regrade_report(
            _as_digest_rule(regraded), regraded, labels=("digest rule", "current grader")
        )
    )
    print(f"wrote {out_path}")
    return 0


def regrade_rows(
    rows: list[dict[str, Any]],
    *,
    execute: Callable[[str], tuple[Any, Any]],
    gold_sql: dict[str, str],
    order_sensitive: set[str],
    stamp: dict[str, Any],
) -> tuple[list[dict[str, Any]], Counter]:
    """Every row re-executed and graded twice. ``execute(sql) -> (columns, rows)``.

    A statement fault (``QueryError``, including a statement timeout) is a missing prediction
    or a missing gold, exactly as the harness grades it. A ``ConnectionError`` is retried once
    and then raises :class:`RegradeAborted`: grading the rest of a run as missing predictions
    because the database went away is the defect the connector's own docstring describes.
    """
    from governed_bi.datasource.errors import ConnectionError as DbConnectionError
    from governed_bi.datasource.errors import QueryError
    from governed_bi.eval.grade import grade_turn

    def run(sql: str) -> tuple[Any, Any]:
        for attempt in (1, 2):
            try:
                return execute(sql)
            except QueryError:
                return None, None
            except DbConnectionError as err:
                if attempt == 2:
                    raise RegradeAborted(f"database connection lost ({type(err).__name__})") from err
        raise AssertionError("unreachable")

    out: list[dict[str, Any]] = []
    flips: Counter = Counter()
    gold_cache: dict[str, tuple[Any, Any]] = {}
    for original in rows:
        row = dict(original)
        qid = str(row.get("question_id"))
        was = row.get("correct")
        gold = gold_sql.get(qid)
        pred = row.get("generated_sql")

        if not gold or not pred or row.get("outcome") == "clarification":
            # Nothing to re-grade: a paused turn produced no statement, a question with no
            # gold has no reference. Carried through rather than silently recounted as wrong.
            row["correct_digest_rule"] = was
            flips["unchanged (nothing to grade)"] += 1
            out.append(row)
            continue

        pcols, prows = run(str(pred))
        if qid not in gold_cache:
            gold_cache[qid] = run(gold)
        gcols, grows = gold_cache[qid]

        verdict = grade_turn(
            outcome=str(row.get("outcome")),
            pred_columns=pcols,
            pred_rows=prows,
            gold_columns=gcols,
            gold_rows=grows,
            order_sensitive=qid in order_sensitive,
        )
        # Not coerced: a row the regrade cannot judge stays unmeasured rather than wrong.
        now = verdict["correct"]
        row["correct"] = now
        row["correct_digest_rule"] = _digest_rule(verdict)
        row["gold_fingerprint"] = verdict.get("gold_fingerprint")
        row["pred_fingerprint"] = verdict.get("pred_fingerprint")
        row["grade_detail"] = verdict.get("detail")
        row["regraded"] = True
        row["regrade"] = {**stamp, "correct_before": was}
        flips[
            "now unmeasured" if now is None and was is not None
            else "unchanged" if now == was
            else "wrong -> correct" if now
            else "correct -> wrong"
        ] += 1
        out.append(row)
    return out, flips


def _digest_rule(verdict: dict[str, Any]) -> bool | None:
    """The artifact's original rule on the re-executed rows: equal BIRD digests.

    ``None`` when the gold could not run (nothing to compare), ``False`` when only the
    prediction could not, the same three values as ``grade_turn``.
    """
    if verdict.get("correct") is None:
        return None
    gold_fp, pred_fp = verdict.get("gold_fingerprint"), verdict.get("pred_fingerprint")
    if gold_fp is None or pred_fp is None:
        return False
    return pred_fp == gold_fp


def _as_digest_rule(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{**r, "correct": r.get("correct_digest_rule")} for r in rows]


def _regrade_report(
    before_rows: list[dict],
    after_rows: list[dict],
    *,
    labels: tuple[str, str] = ("before", "after"),
) -> str:
    """Both EX values and the paired test between them, through ``measure/``.

    Unmeasured rows are outside both rates rather than counted as wrong, and the flips are the
    McNemar table: ``wrong -> correct`` is ``only_b``, ``correct -> wrong`` is ``only_a``. A
    regrade is the most tightly paired comparison this repository runs, so the delta is reported
    with its test.

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
    # Refused, not reconciled: a regrade writes every row it read, so a mismatch is a broken run
    # and not a comparison to be salvaged.
    if read != set(by_id):
        lost = sorted(read - set(by_id))[:5]
        extra = sorted(set(by_id) - read)[:5]
        return (
            f"not comparable: the regrade read {len(read)} row(s) and wrote {len(by_id)}. "
            f"missing from the output: {lost or 'none'}; not in the input: {extra or 'none'}. "
            "Neither rate is reported, because a paired comparison over a set that moved is not "
            "paired."
        )

    first, second = labels
    before = population(first, before_rows)
    # Same unit order as `before`: `mcnemar` pairs on the unit key and refuses a mismatched set.
    after = population(second, [by_id[str(r.get("question_id"))] for r in before_rows])

    width = max(len(first), len(second))
    lines = [
        f"EX {first:<{width}}: {headline_ex(before).render()}",
        f"EX {second:<{width}}: {headline_ex(after).render()}",
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
