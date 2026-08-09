"""Sample real values per column, so the writer describes the data rather than the name.

ADR 0005 §1.2 puts the value domain in a column's ``body`` — code table, units, format. BIRD's
own ``value_description`` covers 27.4% of documented columns; the other three quarters have to
come from the data.

Writes ``_build/samples.jsonl``: up to five distinct values, plus min and max for anything
ordered. Read-only, against ``pg_rename_decoy`` — the same instance the eval grades on, so the
values are the ones a query would actually return.

Columns named in the trap manifests are sampled too. Their values *look* plausible, which is
the whole difficulty, and a writer who cannot see that will describe them as if they were real.

One statement per table rather than per column: 5,947 round trips over a remote instance is
minutes of waiting for data that fits in one pass.

    uv run python scripts/corpus_rebuild/06_samples.py
"""

from __future__ import annotations

import argparse
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _common as C  # noqa: E402

#: Rows read per table. Distinct values are taken from these rather than with DISTINCT per
#: column: one scan, and a column whose first 500 rows are all identical is itself a fact worth
#: seeing. It is a sample, and the staged row says so.
SCAN_ROWS = 500
KEEP = 5

TABLES_SQL = """
    select table_schema, table_name
      from information_schema.tables
     where table_type = 'BASE TABLE' and table_schema = any(%s)
     order by table_schema, table_name
"""


def _sort_key(values: list[Any]) -> Any:
    """Sort numerically when every value is a number, lexically otherwise.

    ``sorted(values, key=str)`` put ``"9935"`` above ``"48196"`` and ``"897"`` above ``"16658"``.
    Writers copied those into bodies as observed ranges, and 87 column bodies across 13 schemas
    ended up asserting a range that excludes the very sample values printed beside it. The
    audit that found it is at docs/corpus-audit-2026-08-09.md.

    Postgres hands back ``Decimal``/``int``/``float`` for numeric columns, so the check is on the
    Python type rather than on a regex over the text: a zip code stored as text stays lexical,
    which is right -- ``01001`` is not the number 1001.
    """
    if all(isinstance(v, (int, float, Decimal)) and not isinstance(v, bool) for v in values):
        return lambda v: (0, float(v), "")
    return lambda v: (1, 0.0, str(v))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=C.BUILD / "samples.jsonl")
    ap.add_argument("--schemas", nargs="*", default=None)
    args = ap.parse_args(argv)

    import psycopg

    wanted = sorted(args.schemas) if args.schemas else C.evaluated_schemas()
    rows: list[dict[str, object]] = []
    failed: list[str] = []

    with psycopg.connect(C.dsn(), connect_timeout=15) as conn:
        with conn.cursor() as cur:
            cur.execute(TABLES_SQL, (wanted,))
            tables = cur.fetchall()

        for schema, table in tables:
            with conn.cursor() as cur:
                try:
                    cur.execute(f'select * from "{schema}"."{table}" limit {SCAN_ROWS}')
                    names = [d.name for d in cur.description or []]
                    scanned = cur.fetchall()
                except Exception as err:
                    # Report; never skip in silence. A table with no samples is a table the
                    # writer describes from its name alone, and that is how the corpus being
                    # replaced ended up with 5,947 summaries that only restate identifiers.
                    conn.rollback()
                    failed.append(f"{schema}.{table}: {type(err).__name__}: {err}")
                    continue

            for index, column in enumerate(names):
                values = [r[index] for r in scanned if r[index] is not None]
                distinct: list[str] = []
                for value in values:
                    text = str(value)
                    if text not in distinct:
                        distinct.append(text)
                    if len(distinct) >= KEEP:
                        break
                ordered = sorted(values, key=_sort_key(values)) if values else []
                rows.append(
                    {
                        "db": schema,
                        "table": table,
                        "column": column,
                        "scanned_rows": len(scanned),
                        "null_rows": len(scanned) - len(values),
                        "distinct_in_sample": len({str(v) for v in values}),
                        "sample_values": [v[:120] for v in distinct],
                        "min": str(ordered[0])[:120] if ordered else None,
                        "max": str(ordered[-1])[:120] if ordered else None,
                    }
                )

    written = C.write_jsonl(args.out, rows)
    print(f"{written} column samples from {len(tables)} tables into {args.out} "
          f"(first {SCAN_ROWS} rows per table, {KEEP} distinct values kept)")
    if failed:
        print(f"  {len(failed)} tables could not be sampled:", file=sys.stderr)
        for line in failed[:10]:
            print(f"    {line}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
