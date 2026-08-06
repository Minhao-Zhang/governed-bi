"""Offline: mine fail-then-pass turns out of the logged trace, write drafts, print what was found.

Admin-run, not wired into any live serve path — mirrors UtkuAI v1's Round I miner
(``scripts/mine_structured_check_drafts.py``) and the same reasoning applies: mining is cheap
and safe to run anytime, writing is gated behind draft status, and only
``POST /corpus/drafts/{id}/approve`` (or a human editing the YAML) makes a mined note live.

With ``--enhancer-model``, every mined candidate is also compared (curator/enhancer.py)
against existing certified ``few_shot`` assets in the same schema before writing, so a mined
mistake that just restates one already in the corpus is skipped rather than duplicated, and one
that contradicts an existing fact is written but flagged (``audit.extra.conflict_with``) instead
of silently accepted. Omit it to write every mined candidate unconditionally, as Phase 3 did.

Usage::

    uv run python scripts/mine_mistakes_v2.py --corpus-dir corpus/ --schema beer_factory
    uv run python scripts/mine_mistakes_v2.py --corpus-dir corpus/ --schema beer_factory \
        --enhancer-model gpt-4o-mini
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any


def _enhancer_model(name: str) -> Any:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
    import credentials

    credentials.load_into_environ()
    from langchain.chat_models import init_chat_model

    return init_chat_model(name, model_provider="openai", use_responses_api=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus-dir", required=True, type=Path, help="where drafts are written")
    parser.add_argument("--schema", required=True, help="attribute mined drafts to this schema")
    parser.add_argument("--limit", type=int, default=200, help="how many recent logged turns to scan")
    parser.add_argument("--model", default=None, help="recorded as the draft's authoring model")
    parser.add_argument(
        "--enhancer-model", default=None,
        help="run each candidate through Enhancer dedup/conflict against this chat model "
        "before writing (e.g. gpt-4o-mini); omit to write unconditionally",
    )
    args = parser.parse_args(argv)

    from governed_bi.api.trace_store import get_turn, list_turns
    from governed_bi.corpus.drafts import submit_draft
    from governed_bi.corpus.store import load
    from governed_bi.curator.enhancer import apply as enhancer_apply
    from governed_bi.curator.mistake_memory import mine_mistake_from_execution

    enhancer_model = _enhancer_model(args.enhancer_model) if args.enhancer_model else None

    turns = list_turns(limit=args.limit)
    mined = 0
    skipped = 0
    scanned = 0
    for row in turns:
        scanned += 1
        turn_id = row.get("turn_id")
        question = row.get("question")
        if not question or not turn_id:
            continue
        # list_turns() returns SUMMARY_FIELDS only; the full record (execution.attempts)
        # is on the detail row.
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

        if enhancer_model is None:
            path = submit_draft(args.corpus_dir, draft, model=args.model)
            mined += 1
            print(f"mined {draft.id} -> {path}")
            continue

        # Re-loaded per candidate: each successful write changes what "existing" means for
        # the next one, and a stale in-memory list would let two near-duplicate mistakes
        # mined in the same run both slip through as "novel".
        existing, _ = load(args.corpus_dir, schemas=[args.schema])
        existing_few_shots = [
            a for a in existing
            if a.asset_type.value == "few_shot"
            and getattr(a.audit, "provenance", None) is not None
            and a.audit.provenance.status.value == "certified"
        ]
        path, decision = enhancer_apply(
            enhancer_model, args.corpus_dir, draft, existing=existing_few_shots, write_model=args.model,
        )
        if path is None:
            skipped += 1
            print(f"skipped {draft.id}: duplicate of {decision.duplicate_of}")
        else:
            mined += 1
            tag = f" (conflicts with {decision.conflict_with})" if decision.conflict_with else ""
            print(f"mined {draft.id} -> {path}{tag}")

    print(f"scanned {scanned} turn(s), mined {mined} draft(s), skipped {skipped} duplicate(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
