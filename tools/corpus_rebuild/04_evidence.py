"""Stage BIRD's ``evidence`` as classified clauses. Writes to ``_build/``, not to the corpus.

``evidence`` is the SME gloss the benchmark hands over per question — the business phrasing, the
metric formula, the value vocabulary. 92.7% of train questions carry one, and 97% of those name
at least one real obfuscated identifier, so binding is mechanical.

**This script authors nothing.** Terms and metrics are written by the agent in phase 3. A clause
copied verbatim into a ``summary`` is benchmark-shaped text rather than what a person
maintaining a warehouse would write, and the test split's phrasings are drawn from the same
distribution as the train split's. Several clauses usually describe one concept; merging them
into one term whose ``synonyms`` carry every phrasing is the job, and it is not a regex's.

Scale: 8,775 distinct clauses over 57 schemas. The corpus being replaced holds 1,393 term and
metric assets against 6,048 harvestable ones — a 23% yield, 10–16% on the worst schemas.

    uv run python tools/corpus_rebuild/04_evidence.py
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _common as C  # noqa: E402

REFERS = re.compile(r"\brefers?\s+to\b", re.I)
AGG = re.compile(r"\b(sum|count|avg|max|min|total|divide|subtract)\s*\(|\bdivide\b|%", re.I)
EQ = re.compile(r"=\s*'[^']*'|=\s*\"[^\"]*\"|=\s*\d")
GLOSS = re.compile(r"^['\"].+?['\"]\s+(is|are)\s+(the\s+)?\w+", re.I)
IDENT = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]{2,}\b")
LITERAL = re.compile(r"'([^']{1,60})'|\"([^\"]{1,60})\"")


def shape(clause: str) -> str:
    """What the clause is offering. ``metric`` and ``term_*`` are the ones worth writing."""
    if REFERS.search(clause) and AGG.search(clause):
        return "metric"
    if REFERS.search(clause) and EQ.search(clause):
        return "term_value"
    if REFERS.search(clause):
        return "term_column"
    if GLOSS.match(clause):
        return "value_gloss"
    return "other"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=C.BUILD / "evidence_clauses.jsonl")
    args = ap.parse_args(argv)

    vocab = {db: set(cols.values()) for db, cols in C.rename_map().items()}
    seen: set[tuple[str, str]] = set()
    rows: list[dict[str, object]] = []
    shapes: Counter[str] = Counter()
    unbound = 0

    for row in C.train_rows():
        db = row.get("db_id")
        if db not in vocab:
            continue
        for clause in C.clauses(row.get("evidence_rename") or ""):
            if (db, clause) in seen:
                continue
            seen.add((db, clause))
            identifiers = sorted(set(IDENT.findall(clause)) & vocab[db])
            literals = sorted({a or b for a, b in LITERAL.findall(clause)})
            kind = shape(clause)
            shapes[kind] += 1
            if not identifiers:
                unbound += 1
            rows.append(
                {
                    "db": db,
                    "clause": clause,
                    "shape": kind,
                    "identifiers": identifiers,
                    "literals": literals,
                }
            )

    rows.sort(key=lambda r: (r["db"], r["clause"]))
    written = C.write_jsonl(args.out, rows)
    harvestable = shapes["metric"] + shapes["term_value"] + shapes["term_column"]
    print(f"{written} distinct clauses into {args.out}")
    print(f"  shapes: {dict(shapes.most_common())}")
    print(f"  term/metric material: {harvestable}")
    print(f"  clauses naming no real identifier: {unbound} ({unbound/max(written,1):.0%})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
