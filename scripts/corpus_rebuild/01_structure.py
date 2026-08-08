"""Scaffold every schema, table and column from the live obfuscated database.

Structure only. Every ``summary`` is ``TODO <identifier>`` and every ``body`` is empty, so
``check_corpus_conformance`` fails the whole tree until an agent has written it (V2, V4, V6).
That is the point: the corpus being replaced is what happens when the floor already looks
finished.

The identifier rides along in the sentinel so the floor **loads** while failing conformance.
A bare ``TODO`` is rejected by the model itself (summary must contain the asset's identifier),
which drowns V14 -- the rule that catches a structurally broken file -- in noise from every
unwritten asset. Unfinished (V2) and broken (V14) have to be different signals.

Ids come from ``governed_bi.corpus.identity``. Inline columns carry **no** ``id``, ``schema`` or
``parent_table`` — ``corpus/store.py::_split_inline_columns`` derives all three and treats a
file that supplies one as a problem rather than an override.

    uv run python scripts/corpus_rebuild/01_structure.py --out ../BIRD-corpus
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _common as C  # noqa: E402

from governed_bi.corpus.identity import slug, table_id  # noqa: E402

COLUMNS_SQL = """
    select c.table_schema, c.table_name, c.column_name, c.data_type,
           c.is_nullable, c.ordinal_position
      from information_schema.columns c
      join information_schema.tables t
        on t.table_schema = c.table_schema and t.table_name = c.table_name
     where t.table_type = 'BASE TABLE' and c.table_schema = any(%s)
     order by c.table_schema, c.table_name, c.ordinal_position
"""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=C.DEFAULT_CORPUS)
    ap.add_argument("--schemas", nargs="*", default=None, help="subset, for a single-schema run")
    args = ap.parse_args(argv)

    import psycopg

    wanted = sorted(args.schemas) if args.schemas else C.evaluated_schemas()
    with psycopg.connect(C.dsn(), connect_timeout=15) as conn, conn.cursor() as cur:
        cur.execute(COLUMNS_SQL, (wanted,))
        rows = cur.fetchall()

    by_table: dict[tuple[str, str], list[tuple[str, str, str]]] = defaultdict(list)
    for schema, table, column, physical_type, nullable, _pos in rows:
        by_table[(schema, table)].append((column, physical_type, nullable))

    found = {s for s, _ in by_table}
    if missing := sorted(set(wanted) - found):
        # Refuse rather than scaffold a partial tree: a schema silently absent here becomes a
        # schema the router never sees, and the shortfall surfaces as a retrieval miss much later.
        print(f"these schemas are not in the database: {missing}", file=sys.stderr)
        return 2

    tables_per_schema: dict[str, list[str]] = defaultdict(list)
    written = 0
    for (schema, table), columns in sorted(by_table.items()):
        tid = table_id(schema, table)
        tables_per_schema[schema].append(table)
        C.write_asset(
            args.out, schema, "tables", f"tbl_{schema}_{slug(table)}",
            {
                "asset_type": "table",
                "id": tid,
                "schema": schema,
                "physical_name": table,
                "summary": f"{C.SENTINEL} {table}",
                "body": "",
                "columns": [
                    {
                        "physical_name": name,
                        "summary": f"{C.SENTINEL} {name}",
                        "body": "",
                        "physical_type": physical_type,
                        "logical_type": C.logical_type(physical_type),
                        "nullable": nullable == "YES",
                    }
                    for name, physical_type, nullable in columns
                ],
                "audit": C.provenance("seed", "scaffold-1", ["information_schema"]),
            },
        )
        written += 1

    for schema, tables in sorted(tables_per_schema.items()):
        C.write_asset(
            args.out, schema, "", schema,
            {
                "asset_type": "schema",
                "id": schema,
                "name": schema,
                "summary": f"{C.SENTINEL} {schema}",
                "body": "",
                "rules": [],
                "audit": C.provenance("seed", "scaffold-1", ["information_schema"]),
            },
        )

    print(
        f"scaffolded {written} tables and {len(tables_per_schema)} schemas into {args.out}, "
        f"{len(rows)} columns inline. Every summary is the sentinel, so the tree fails "
        f"check_corpus_conformance until it is written."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
