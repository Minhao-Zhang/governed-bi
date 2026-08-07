"""Scratch helper: split train_final.jsonl into per-schema JSON for writer agents.

Authors must not read test_final.jsonl. This keeps train grounding lean.
"""

from __future__ import annotations

import json
import pathlib
from collections import defaultdict

REPO = pathlib.Path(__file__).resolve().parents[1]
SRC = REPO.parent / "BIRD-Data-Obfuscation" / "eval_dataset" / "train_final.jsonl"
OUT = REPO / "runs" / "ablation" / "train-by-schema"
CAP = 40


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    by: dict[str, list[dict]] = defaultdict(list)
    with SRC.open(encoding="utf-8") as f:
        for line in f:
            row = json.loads(line)
            db = row.get("db_id")
            if not db:
                continue
            by[db].append(
                {
                    "question": row.get("question"),
                    "evidence": row.get("evidence"),
                    "sql_rename": row.get("sql_rename") or row.get("SQL"),
                }
            )
    for db, rows in by.items():
        (OUT / f"{db}.json").write_text(
            json.dumps(rows[:CAP], ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
    print(
        f"schemas={len(by)} total_q={sum(len(v) for v in by.values())} "
        f"wrote={len(list(OUT.glob('*.json')))}"
    )


if __name__ == "__main__":
    main()
