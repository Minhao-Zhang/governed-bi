"""Shortlist recall for the schema router — with the facet rewriters and without them.

**The measurement this repository claimed to have and does not.** ``governed_bi.toml`` cites
recall@10 = 0.953 and recall@3 = 0.852 "measured on the curated corpus over all 1351 test
questions (``scripts/routing_ablation.py``)". That script does not exist in the tree. So the
number every routing decision is argued from is not reproducible, which is the exact shape of
the stale claim ``tools/check_citations.py`` exists to catch one directory over.

**Why the two arms.** ``eval.datalake.routing_recall`` needs no *agent* model, but the five facet
query rewriters use the **utility** model, and with none configured they fall back to the raw
question. So the same function measures two different systems depending on what the session
carries, and nobody had separated them:

* ``--no-rewrite`` — the corpus and the router alone, on the user's own words. Free.
* ``--rewrite``   — five short rewrites per question, which is what production does. ~150 tokens
  a call, so a 100-question arm is ~75k utility tokens.

Run both on one question set and the difference is the rewriter's contribution to routing, which
is the number the prompt work has been arguing about without it.

``rank`` is reported, not just ``hit``: "the router never scored the gold schema" and "it ranked
4th" are different failures, and collapsing them is how v1 published a documented failure bucket
at a perfect score.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))

DEFAULT_CORPUS = "corpora/gold-semantic-layer-20260804"
DEFAULT_DATASET = REPO.parent / "BIRD-Data-Obfuscation" / "eval_dataset"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="routing_recall", description=__doc__)
    parser.add_argument("--corpus-dir", default=DEFAULT_CORPUS)
    parser.add_argument("--dataset", type=pathlib.Path, default=DEFAULT_DATASET)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--per-schema",
        type=int,
        default=2,
        help="questions per schema. A flat --limit is weighted by whichever schema BIRD "
        "happened to ask most about, so a per-schema effect reads as an overall one.",
    )
    parser.add_argument("--top-n", type=int, default=3, help="shortlist size under test")
    parser.add_argument(
        "--rewrite",
        action="store_true",
        help="configure the utility model, so the five facet rewriters run as they do in "
        "production. Costs ~150 tokens x 5 per question.",
    )
    parser.add_argument("--embed", action="store_true", default=True)
    parser.add_argument("--no-embed", dest="embed", action="store_false")
    parser.add_argument("--out", type=pathlib.Path, default=None)
    args = parser.parse_args(argv)

    import credentials

    credentials.load_into_environ()
    dsn = credentials.secret(*credentials.PG_DSN_NAMES)
    if not dsn:
        print("no database credential reachable", file=sys.stderr)
        return 2

    from governed_bi.datasource.postgres import PostgresConnector
    from governed_bi.eval.datalake import (
        load_questions,
        routing_recall,
        summarise_routing,
        table_coverage,
    )
    from governed_bi.govern.policy import GovernancePolicy
    from governed_bi.serve import session as session_mod

    embedder = None
    vector_cache = None
    if args.embed:
        from governed_bi.model import OpenAIEmbedder
        from governed_bi.retrieve.vector_cache import vector_cache_from_environment

        embedder = OpenAIEmbedder()
        vector_cache = vector_cache_from_environment(model=embedder.requested_model)

    utility = None
    if args.rewrite:
        import os

        from langchain.chat_models import init_chat_model

        model_id = os.environ.get("GOVERNED_BI_UTILITY_MODEL") or os.environ["GOVERNED_BI_MODEL"]
        kwargs = {"model_provider": "openai"}
        effort = os.environ.get("GOVERNED_BI_UTILITY_MODEL_EFFORT")
        if effort:
            kwargs["reasoning_effort"] = effort
        utility = init_chat_model(model_id, **kwargs)
        print(f"rewriters ON: {model_id} effort={effort or '(default)'}")
    else:
        print("rewriters OFF: facets search the raw question")

    # `agent_model=None` on purpose — `routing_recall` documents that the stub answer path costs
    # nothing while facets, routing, retrieval, resolve and connect all run for real.
    session = session_mod.from_corpus_dir(
        args.corpus_dir,
        connector=PostgresConnector(dsn),
        policy=GovernancePolicy(guard_rules_enabled={}),
        agent_model=None,
        utility_model=utility,
        embedder=embedder,
        vector_cache=vector_cache,
    )
    schemas = sorted({s for s in session.structure.table_schemas.values() if s})
    questions = load_questions(
        # `--dataset` is the *directory*, as `run_datalake_eval.py` takes it; the question
        # file inside it is `test_final.jsonl`. Passing the directory raised PermissionError,
        # which on Windows is what opening a directory looks like.
        args.dataset / "test_final.jsonl",
        schemas=schemas,
        limit=args.limit,
        per_schema=args.per_schema,
    )
    print(f"{len(questions)} questions over {len(schemas)} schemas, top_n={args.top_n}\n")

    started = time.time()
    rows = routing_recall(questions, session=session, top_n=args.top_n)
    took = time.time() - started
    summary = summarise_routing(rows)

    # **Table coverage is the number to lead with, and this tool did not report it.**
    # `docs/plans/retrieval-ceiling-2026-08-04.md` corrects an earlier document for concluding
    # from schema `recall@k`: "those numbers are right; they measure the wrong stage". A turn can
    # route to the right schema and still be unable to answer, because the per-type budget
    # licenses at most 8 ranked tables -- so coverage bounds EX and recall does not.
    coverage = table_coverage(rows, {str(q["question_id"]): str(q["gold_sql"]) for q in questions})
    mean_licensed = sum(len(r.get("licensed") or ()) for r in rows) / max(len(rows), 1)

    print(json.dumps({"routing": summary, "coverage": coverage}, indent=2, default=str))
    print(f"\nmean tables licensed: {mean_licensed:.1f}")
    print(f"{took:.0f}s for {len(rows)} questions")

    missed = [r for r in rows if not r["hit"]]
    print(f"\n{len(missed)} misses; the gold schema's rank where it was scored at all:")
    for row in missed[:15]:
        print(
            f"  {row['db_id']:<24} rank={row['rank']!s:<6} "
            f"selected={row['selected']}"
        )

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(
            json.dumps(
                {
                    "arm": "rewrite" if args.rewrite else "no_rewrite",
                    "corpus_dir": args.corpus_dir,
                    "top_n": args.top_n,
                    "embed": args.embed,
                    "n_questions": len(rows),
                    "summary": summary,
                    "coverage": coverage,
                    "mean_licensed": round(mean_licensed, 1),
                    "rows": rows,
                },
                indent=2,
                default=str,
            ),
            encoding="utf-8",
        )
        print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
