"""Which schemas are written and which are still the floor. The orchestrator's source of truth.

Counts unwritten assets directly (V2's condition, cheaply) rather than shelling out to the
validator 57 times: a schema is done when nothing still carries the sentinel and every asset
has a body.

    uv run python scripts/corpus_rebuild/progress.py
    uv run python scripts/corpus_rebuild/progress.py --pending   # names only, for dispatch
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _common as C  # noqa: E402


def audit(root: Path, schema: str) -> tuple[int, int, int, int]:
    """``(assets, still sentinel, no body, terms+metrics)`` for one schema.

    An unparseable file counts as one unfinished asset rather than raising. This runs *during*
    the rebuild, against a tree being written asset by asset, and it is what dispatch reads:
    one half-written YAML file used to abort the whole run with a ``ScannerError`` and report
    nothing about the other 56 schemas. ``check_corpus_conformance.load_assets`` guards the same
    read for the same reason — a tool that must answer on a half-written tree cannot assume the
    tree parses. Counting it unfinished is also the right answer: the schema is not done.
    """
    total = sentinel = bodyless = extra = 0
    for path in sorted((root / schema).rglob("*.yaml")):
        try:
            doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 — malformed, mid-write, or unreadable: all "not done"
            total += 1
            sentinel += 1
            continue
        if not isinstance(doc, dict):
            continue
        kind = doc.get("asset_type")
        if kind in ("term", "metric"):
            extra += 1
        items = [doc, *(doc.get("columns") or [] if kind == "table" else [])]
        for item in items:
            if not isinstance(item, dict):
                continue
            total += 1
            summary = str(item.get("summary") or "")
            if summary.startswith(C.SENTINEL):
                sentinel += 1
            if not str(item.get("body") or "").strip():
                bodyless += 1
    return total, sentinel, bodyless, extra


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--corpus-dir", type=Path, default=C.DEFAULT_CORPUS)
    ap.add_argument("--pending", action="store_true", help="print unfinished schema names only")
    args = ap.parse_args(argv)

    rows = []
    for schema in C.evaluated_schemas():
        if not (args.corpus_dir / schema).is_dir():
            rows.append((schema, 0, 0, 0, 0))
            continue
        rows.append((schema, *audit(args.corpus_dir, schema)))

    done = [r for r in rows if r[1] and not r[2] and not r[3]]
    pending = [r for r in rows if r not in done]

    if args.pending:
        print(" ".join(r[0] for r in pending))
        return 0

    print(f"{'schema':<28}{'assets':>8}{'sentinel':>10}{'no body':>9}{'term+metric':>13}")
    for schema, total, sentinel, bodyless, extra in rows:
        mark = "ok " if (total and not sentinel and not bodyless) else "   "
        print(f"{mark}{schema:<25}{total:>8}{sentinel:>10}{bodyless:>9}{extra:>13}")
    t = sum(r[1] for r in rows)
    s = sum(r[2] for r in rows)
    b = sum(r[3] for r in rows)
    e = sum(r[4] for r in rows)
    print(f"\n{len(done)}/{len(rows)} schemas written; {t} assets, {s} still sentinel, "
          f"{b} without a body, {e} terms and metrics created")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
