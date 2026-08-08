"""Harvest question → gold-SQL exemplars from the train split.

Deliberately not an agent job. Rewriting harvested SQL with a model costs money and invents
queries; the value here is that the SQL is verbatim and provably ran.

``summary`` is the question — ADR 0005 §1.2 says a few-shot's summary *is* the question, and it
is what the semantic channel matches an incoming question against. ``body`` repeats it with the
SQL, because the model never sees a summary.

Two filters the previous harvest did not have:

* **Drop a gold query that reads no table.** ``SELECT ... FROM (VALUES (...))`` is a
  transpilation artifact — the Postgres query embeds the SQLite result rather than recomputing
  it — and 9.9% of the train split is shaped that way. As an exemplar it teaches nothing except
  how to fabricate a constant.
* **Cap the asset.** Those artifacts are also what produced 15 assets over 80 KB in the corpus
  being replaced, one of them 5.1 MB; together they were half its bytes.

    uv run python scripts/corpus_rebuild/03_few_shots.py --out ../BIRD-corpus
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

import sqlglot
from sqlglot import expressions as exp

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _common as C  # noqa: E402

#: Matches ``check_corpus_conformance``'s few-shot cap; a bigger one is a materialised result.
MAX_BYTES = 4_000


def reads_a_table(sql: str) -> bool:
    try:
        tree = sqlglot.parse_one(sql, dialect="postgres")
    except Exception:
        return False
    return any(t.text("db") for t in tree.find_all(exp.Table))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=C.DEFAULT_CORPUS)
    ap.add_argument("--schemas", nargs="*", default=None)
    args = ap.parse_args(argv)

    wanted = set(args.schemas) if args.schemas else set(C.evaluated_schemas())
    dropped = Counter()
    kept: dict[str, int] = Counter()

    for row in C.train_rows():
        schema = row.get("db_id")
        if schema not in wanted:
            continue
        sql = (row.get("sql_rename") or "").strip()
        question = " ".join((row.get("question") or "").split())
        if not sql or not question:
            dropped["incomplete"] += 1
            continue
        if not reads_a_table(sql):
            dropped["gold reads no table"] += 1
            continue

        index = kept[schema]
        body = f"Question: {question}\nSQL:\n{sql}"
        if len((question + body).encode("utf-8")) > MAX_BYTES:
            dropped["over the byte cap"] += 1
            continue

        C.write_asset(
            args.out, schema, "few-shots", f"fs_{schema}_{index:04d}",
            {
                "asset_type": "few_shot",
                "id": f"fs_{schema}_{index:04d}",
                "schema": schema,
                "sql": sql,
                "summary": question,
                "body": body,
                "audit": C.provenance(
                    "gold", "harvest-1", ["train_final.jsonl"],
                    f"train question {row.get('question_id')}",
                ),
            },
        )
        kept[schema] += 1

    print(
        f"{sum(kept.values())} few-shots into {args.out} across {len(kept)} schemas; "
        f"dropped {dict(dropped)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
