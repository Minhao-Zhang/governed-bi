"""CLI: ``python -m governed_bi.eval --oracle-only ...``."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from governed_bi.datasource.sqlite import SqliteConnector
from governed_bi.eval.arms import oracle_arm, scripted_arm, stub_arm
from governed_bi.eval.harness import run_comparison
from governed_bi.eval.report import summarise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Governed-BI eval harness (G1)")
    parser.add_argument("--oracle-only", action="store_true", help="Gold SQL ceiling only")
    parser.add_argument("--arms", default="oracle", help="Comma list: oracle,stub,scripted")
    parser.add_argument("--db", type=Path, required=True, help="SQLite database path")
    parser.add_argument(
        "--questions",
        type=Path,
        required=True,
        help=(
            "JSONL with question_id, question, gold_sql. Add gold_fingerprint (or "
            "gold_columns + gold_rows) for the oracle arm to measure anything: without an "
            "independent gold its EX is unmeasured, because the only comparison left is the "
            "executed gold against itself"
        ),
    )
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--pair", default=None, help="armA,armB for McNemar + context gate")
    args = parser.parse_args(argv)

    questions = _load_questions(args.questions, limit=args.limit)
    connector = SqliteConnector(args.db)

    gold_by_qid = {str(q["question_id"]): str(q["gold_sql"]) for q in questions}
    name_to_factory = {
        "oracle": lambda: oracle_arm(connector=connector),
        "stub": lambda: stub_arm(connector=connector),
        "scripted": lambda: scripted_arm(
            gold_sql_by_qid=gold_by_qid, connector=connector
        ),
    }
    if args.oracle_only:
        arm_names = ["oracle"]
    else:
        arm_names = [a.strip() for a in args.arms.split(",") if a.strip()]

    arms = [name_to_factory[n]() for n in arm_names]
    results = run_comparison(questions, arms)
    pair = tuple(args.pair.split(",", 1)) if args.pair else None
    if pair and (pair[0] not in results or pair[1] not in results):
        pair = None
    summary = summarise(results, pair=pair)  # type: ignore[arg-type]
    json.dump(summary, sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")
    return 0


def _load_questions(path: Path, *, limit: int | None) -> list[dict]:
    rows: list[dict] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
            if limit is not None and len(rows) >= limit:
                break
    return rows


if __name__ == "__main__":
    raise SystemExit(main())
