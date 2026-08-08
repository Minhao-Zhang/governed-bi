"""Split the staged material into one packet per schema, so a writer reads one file.

Without this every agent greps three ~1 MB JSONL files for its own rows, 57 times over. The
packet also makes the writer's input set explicit and auditable: what is in the file is what it
was given.

Includes the trap manifests' verdict as a bare boolean per column. Which columns are unreliable
is fair input (the database under test is the decoy instance and a steward would know), but the
*operator*, the *source column* and the word itself are not passed through — naming what a
column resembles is what makes it rank for that column's questions.

    uv run python scripts/corpus_rebuild/07_packets.py
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _common as C  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=C.BUILD / "packets")
    args = ap.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)

    docs: dict[str, list[dict]] = defaultdict(list)
    for row in C.read_jsonl(C.BUILD / "bird_docs.jsonl"):
        docs[row["db"]].append(row)

    samples: dict[str, list[dict]] = defaultdict(list)
    for row in C.read_jsonl(C.BUILD / "samples.jsonl"):
        samples[row["db"]].append(row)

    evidence: dict[str, list[dict]] = defaultdict(list)
    for row in C.read_jsonl(C.BUILD / "evidence_clauses.jsonl"):
        evidence[row["db"]].append(row)

    unreliable: dict[str, set[tuple[str, str]]] = defaultdict(set)
    traps = json.loads((C.EVAL_DATASET / "trap_manifest.json").read_text(encoding="utf-8"))
    for trap in traps:
        if trap.get("names"):
            unreliable[trap["db"]].add((trap["table"], trap["names"]["rename"]))
    unreliable_keys: dict[str, set[str]] = defaultdict(set)
    for trap in traps:
        if trap.get("names") and trap.get("is_key"):
            unreliable_keys[trap["db"]].add(trap["names"]["rename"])

    # A clone table's `names.rename` is `{"table": ..., "columns": [...]}`, not a string, and
    # every column of a clone is unreliable whether or not the column manifest lists it.
    unreliable_tables: dict[str, set[str]] = defaultdict(set)
    table_traps = json.loads((C.EVAL_DATASET / "trap_table_manifest.json").read_text(encoding="utf-8"))
    for trap in table_traps:
        db = trap.get("db")
        renamed = (trap.get("names") or {}).get("rename") or {}
        name = renamed.get("table")
        if not (db and name):
            continue
        unreliable_tables[db].add(name)
        for column in renamed.get("columns") or []:
            unreliable[db].add((name, column))

    written = 0
    for schema in C.evaluated_schemas():
        # A column is unreliable if the manifest names it, or if its whole table is a clone.
        cols = {name for _, name in unreliable.get(schema, set())}
        packet = {
            "schema": schema,
            "unreliable_tables": sorted(unreliable_tables.get(schema, set())),
            "unreliable_columns": sorted(cols),
            "unreliable_join_keys": sorted(unreliable_keys.get(schema, set())),
            "bird_documentation": docs.get(schema, []),
            "value_samples": samples.get(schema, []),
            "evidence_clauses": evidence.get(schema, []),
        }
        path = args.out / f"{schema}.json"
        path.write_text(
            json.dumps(packet, ensure_ascii=False, indent=1, sort_keys=True),
            encoding="utf-8",
            newline="\n",
        )
        written += 1

    total = sum((args.out / f"{s}.json").stat().st_size for s in C.evaluated_schemas())
    print(f"{written} packets into {args.out}, {total/1024/1024:.1f} MB total")
    print(f"  unreliable columns named: {sum(len(v) for v in unreliable.values())}")
    print(f"  unreliable tables named:  {sum(len(v) for v in unreliable_tables.values())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
