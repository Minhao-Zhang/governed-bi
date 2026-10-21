"""Accuracy, coverage, the dev and holdout halves, and run-to-run disagreement.

Reads an artifact off disk. No database, no model, no corpus. A resumed run is one
run, so every rate is over the whole file.

The two halves are ``docs/data/db-split.csv``. A question whose database is not in
that file is counted as unassigned and left out of both halves.
"""

from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

_SPLIT = pathlib.Path(__file__).resolve().parent.parent / "docs" / "data" / "db-split.csv"


def load_halves(path: pathlib.Path = _SPLIT) -> dict[str, str]:
    """``{db_id: dev|holdout}`` from the committed split."""
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines()[1:]:
        if not line.strip() or "," not in line:
            continue
        db, _, half = line.partition(",")
        out[db.strip()] = half.strip()
    return out


def execution_accuracy(rows: list[dict]) -> tuple[int, int]:
    """``(correct, graded)``. A row the grader could not judge is not a wrong answer."""
    graded = [row for row in rows if row.get("correct") is not None]
    ok = sum(1 for row in graded if row.get("correct") is True)
    return ok, len(graded)


def disagreement(left: list[dict], right: list[dict]) -> tuple[int, int]:
    """``(differing, compared)`` on ``correct``, matched by question id.

    Unmeasured (``None``) differs from a grade. Two unmeasured rows agree.
    """
    other = {str(row.get("question_id")): row for row in right}
    compared = differ = 0
    for row in left:
        match = other.get(str(row.get("question_id")))
        if match is None:
            continue
        compared += 1
        if _grade(row) != _grade(match):
            differ += 1
    return differ, compared


def _grade(row: dict) -> bool | None:
    value = row.get("correct")
    return None if value is None else bool(value)


def _rate(ok: int, total: int) -> str:
    if not total:
        return "unmeasured"
    return f"{ok}/{total} = {ok / total:.3f}"


def _rows_of(path: pathlib.Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _gold(dataset: pathlib.Path) -> tuple[dict[str, str], dict[str, str]]:
    sql: dict[str, str] = {}
    db: dict[str, str] = {}
    questions = dataset / "test_final.jsonl"
    for line in questions.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        qid = str(row["question_id"])
        db[qid] = str(row["db_id"])
        if row.get("sql_rename"):
            sql[qid] = str(row["sql_rename"])
    return sql, db


def _by_half(rows: list[dict], db_of: dict[str, str], halves: dict[str, str]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {"dev": [], "holdout": [], "unassigned": []}
    for row in rows:
        half = halves.get(db_of.get(str(row.get("question_id")), ""))
        grouped[half if half in ("dev", "holdout") else "unassigned"].append(row)
    return grouped


def print_report(
    out_path: pathlib.Path,
    args,
    table_coverage,
    *,
    against: pathlib.Path | None = None,
) -> None:
    """Print the whole file, not just this process's rows."""
    every = _rows_of(out_path)
    print(f"\nrows in {out_path}: {len(every)}")
    gold_sql, gold_db = _gold(args.dataset)
    halves = load_halves()
    grouped = _by_half(every, gold_db, halves)
    _print_slice("EX", every)
    _print_coverage("coverage", every, gold_sql, table_coverage)
    for half in ("dev", "holdout"):
        _print_slice(f"{half} EX", grouped[half])
        _print_coverage(f"{half} coverage", grouped[half], gold_sql, table_coverage)
    if grouped["unassigned"]:
        print(f"unassigned         = {len(grouped['unassigned'])} question(s) not in the split")
    if against is not None:
        differ, compared = disagreement(every, _rows_of(against))
        print(f"disagreement       = {_rate(differ, compared)}")


def _print_slice(label: str, rows: list[dict]) -> None:
    ok, graded = execution_accuracy(rows)
    print(f"{label:<18}= {_rate(ok, graded)}")


def _print_coverage(label: str, rows: list[dict], gold_sql: dict[str, str], table_coverage) -> None:
    if not rows:
        print(f"{label:<18}= unmeasured")
        return
    covered = table_coverage(rows, gold_sql)
    rate = covered["all_gold_tables_licensed"]
    print(f"{label:<18}= {rate:.3f}   all gold tables licensed")


def main(argv: list[str] | None = None) -> int:
    import argparse

    from governed_bi.eval.datalake import table_coverage

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", type=pathlib.Path)
    parser.add_argument("--dataset", type=pathlib.Path, required=True)
    parser.add_argument("--against", type=pathlib.Path, default=None)
    args = parser.parse_args(argv)
    print_report(args.artifact, args, table_coverage, against=args.against)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
