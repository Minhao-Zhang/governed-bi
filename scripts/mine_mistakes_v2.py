"""Offline: mine fail-then-pass turns out of the logged trace, write drafts, print what was found.

Admin-run, not wired into any live serve path — mirrors UtkuAI v1's Round I miner
(``scripts/mine_structured_check_drafts.py``) and the same reasoning applies: mining is cheap
and safe to run anytime, writing is gated behind draft status, and only
``POST /corpus/drafts/{id}/approve`` (or a human editing the YAML) makes a mined note live.

Usage::

    uv run python scripts/mine_mistakes_v2.py --corpus-dir corpus/ --schema beer_factory
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus-dir", required=True, type=Path, help="where drafts are written")
    parser.add_argument("--schema", required=True, help="attribute mined drafts to this schema")
    parser.add_argument("--limit", type=int, default=200, help="how many recent logged turns to scan")
    parser.add_argument("--model", default=None, help="recorded as the draft's authoring model")
    args = parser.parse_args(argv)

    from governed_bi.api.trace_store import list_turns
    from governed_bi.corpus.drafts import submit_draft
    from governed_bi.curator.mistake_memory import mine_mistake_from_execution

    turns = list_turns(limit=args.limit)
    mined = 0
    scanned = 0
    for row in turns:
        scanned += 1
        turn_id = row.get("turn_id")
        question = row.get("question")
        if not question or not turn_id:
            continue
        # list_turns() returns SUMMARY_FIELDS only; the full record (execution.attempts)
        # is on the detail row.
        from governed_bi.api.trace_store import get_turn

        entry = get_turn(str(turn_id))
        if entry is None:
            continue
        record = entry.get("record") or {}
        execution = record.get("execution") or {}
        schemas = record.get("schemas") or [args.schema]
        if args.schema not in schemas:
            continue
        draft = mine_mistake_from_execution(str(question), args.schema, execution)
        if draft is None:
            continue
        path = submit_draft(args.corpus_dir, draft, model=args.model)
        mined += 1
        print(f"mined {draft.id} -> {path}")

    print(f"scanned {scanned} turn(s), mined {mined} draft(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
