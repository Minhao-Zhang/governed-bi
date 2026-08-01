"""E1: offline schema-shortlist ablation. No chat model, no SQL, no grading.

Answers one question cheaply: **how much of the routing loss is the retrieval
channel, and does a bigger embedder move it?**

Why this exists as its own script. In the 20260731 ladder ``routing_recall`` and
``schema_pick_accuracy`` are numerically identical on every arm, because with
``llm_pick`` on the router collapses to a single schema before the metric is taken
(``agent.py``: ``routed = frozenset([picked])``). So the published number is pick
accuracy and the retrieval channel is unmeasured. Recomputed from the raw rows, the
curated arm splits as:

    gold in shortlisted_schemas   95.2%   <- retrieval
    schema_pick == gold           87.3%   <- the LLM pick
    gold shortlisted, pick wrong   7.8%   (106 questions, 3 still answered correctly)

That is a ~50x cheaper thing to iterate on than a ladder: one embedding call per
question against a per-corpus vector set, versus ~37k chat tokens per question.

The whole sweep costs a few hundred thousand embedding tokens and runs in minutes.
Its job is to be allowed to say "the embedder change is not worth it" before anyone
spends 3.7 hours on a serve run.

Usage::

    uv run python scripts/routing_ablation.py \\
        --corpus runs/datalake/.../corpus_curated \\
        --out runs/ablation/e1-shortlist.json
"""

from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from dataclasses import replace
from pathlib import Path
from typing import Any

from governed_bi.config import load_settings
from governed_bi.corpus.loader import load_corpus
from governed_bi.llm.langchain_client import LangChainEmbedder
from governed_bi.retrieval.schema_router import (
    embed_schema_documents,
    schema_documents,
    shortlist_schemas,
)

#: Ranks at which recall is reported. The router is configured at 10 by the eval
#: driver and at 3 by the shipped product default, so both must appear or the
#: sweep cannot say what a deployment would get.
TOP_KS = (1, 3, 5, 10, 20)


def _load_questions(bird_dir: Path, split: str) -> list[tuple[str, str, str]]:
    """``(question_id, db_id, question)`` for the split, in file order."""
    path = bird_dir / "eval_dataset" / f"{split}_final.jsonl"
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            rows.append((r["question_id"], r["db_id"], r["question"]))
    return rows


def _rank_of_gold(shortlist: list[str], gold: str) -> int | None:
    return shortlist.index(gold) + 1 if gold in shortlist else None


def _sweep(
    corpus: Any,
    questions: list[tuple[str, str, str]],
    embedder: Any,
    label: str,
) -> dict[str, Any]:
    """One channel configuration, all of TOP_KS in a single pass.

    Ranking is computed once at ``max(TOP_KS)`` and every smaller k is read off the
    gold's rank in that list. Slicing a ranked list is exactly what ``top_k`` does
    (``schema_router``: ``out = [s for s, _ in ranked][:top_k]``), so this is the
    same answer for a fifth of the calls — and, more importantly, the same question
    embedding for all five, which keeps the comparison across k exact rather than
    subject to per-call nondeterminism.
    """
    vectors = embed_schema_documents(corpus, embedder) if embedder is not None else None
    if embedder is not None and not vectors:
        raise RuntimeError(f"{label}: embedder produced no schema vectors")

    t0 = time.time()
    ranks: list[int | None] = []
    channels: Counter[str] = Counter()
    per_db: dict[str, list[int | None]] = {}
    k_max = max(TOP_KS)

    for i, (_qid, gold, question) in enumerate(questions):
        channel: dict[str, Any] = {}
        shortlist = shortlist_schemas(
            corpus,
            question,
            top_k=k_max,
            embedder=embedder,
            schema_vectors=vectors,
            channel_out=channel,
        )
        r = _rank_of_gold(shortlist, gold)
        ranks.append(r)
        channels[str(channel.get("schema_route_channel"))] += 1
        per_db.setdefault(gold, []).append(r)
        if (i + 1) % 200 == 0:
            print(f"    {label}: {i+1}/{len(questions)}  ({time.time()-t0:.0f}s)", flush=True)

    n = len(ranks)
    recall = {
        str(k): sum(1 for r in ranks if r is not None and r <= k) / n for k in TOP_KS
    }
    # Per-schema recall at the driver's configured k, so a systematically invisible
    # schema is nameable rather than averaged away.
    worst = sorted(
        (
            (sum(1 for r in rs if r is not None and r <= 10) / len(rs), db, len(rs))
            for db, rs in per_db.items()
        )
    )[:8]
    return {
        "label": label,
        "n_questions": n,
        "recall_at": recall,
        "never_shortlisted_at_20": sum(1 for r in ranks if r is None),
        "channel_counts": dict(channels),
        "elapsed_sec": round(time.time() - t0, 1),
        "worst_schemas_at_10": [
            {"db_id": db, "recall": round(rc, 3), "n": nq} for rc, db, nq in worst
        ],
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--corpus", type=Path, required=True, help="a built corpus root")
    p.add_argument("--bird-dir", type=Path, default=Path("../BIRD-Data-Obfuscation"))
    p.add_argument("--split", default="test")
    p.add_argument("--limit", type=int, default=None, help="first N questions (smoke)")
    p.add_argument(
        "--embedders",
        default="text-embedding-3-small,text-embedding-3-large",
        help="comma list; the BM25-only arm is always included as the floor",
    )
    p.add_argument("--out", type=Path, default=None)
    args = p.parse_args()

    corpus = load_corpus(args.corpus)
    docs = schema_documents(corpus)
    questions = _load_questions(args.bird_dir, args.split)
    if args.limit:
        questions = questions[: args.limit]

    print(f"corpus  : {args.corpus}  ({len(docs)} schemas)")
    print(f"questions: {len(questions)} ({args.split} split)")
    gold_schemas = {g for _q, g, _t in questions}
    missing = sorted(gold_schemas - set(docs))
    if missing:
        # Not a warning to scroll past: a gold schema absent from the corpus is
        # unreachable at every k, so it silently caps recall and every number below
        # would understate the channel while looking like a retrieval failure.
        print(f"WARNING: {len(missing)} gold schemas absent from this corpus: {missing}")

    base = load_settings().models
    results = []

    print("\n-- BM25 only (the floor: what routing does with no embedder) --")
    results.append(_sweep(corpus, questions, None, "bm25_only"))

    for name in [e.strip() for e in args.embedders.split(",") if e.strip()]:
        print(f"\n-- {name} --")
        emb = LangChainEmbedder.from_config(replace(base, embedding_model=name))
        results.append(_sweep(corpus, questions, emb, name))

    print(f"\n{'='*72}")
    print(f"{'channel':<26}" + "".join(f"{'@'+str(k):>8}" for k in TOP_KS))
    for r in results:
        row = "".join(f"{r['recall_at'][str(k)]:>8.3f}" for k in TOP_KS)
        print(f"{r['label']:<26}{row}")

    print(f"\n{'='*72}")
    print("Worst schemas at k=10 (embedder arm), i.e. where retrieval actually fails:")
    for w in results[-1]["worst_schemas_at_10"]:
        print(f"    {w['db_id']:<28} recall {w['recall']:.3f}  (n={w['n']})")

    payload = {
        "corpus": str(args.corpus),
        "split": args.split,
        "n_questions": len(questions),
        "n_schemas_in_corpus": len(docs),
        "gold_schemas_absent_from_corpus": missing,
        "top_ks": list(TOP_KS),
        "results": results,
    }
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
