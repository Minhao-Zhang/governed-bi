"""Shortlist recall for the schema router — with the facet rewriters and without them.

``governed_bi.toml`` cites recall@10 = 0.953 and recall@3 = 0.852 "measured on the curated corpus
over all 1351 test questions (``scripts/routing_ablation.py``)", and that script does not exist in
the tree — so the number every routing decision is argued from is not reproducible.

Two arms, because ``eval.datalake.routing_recall`` needs no *agent* model but the five facet query
rewriters use the **utility** model, falling back to the raw question when none is configured. The
same function therefore measures two different systems depending on what the session carries:

* ``--no-rewrite`` — the corpus and the router alone, on the user's own words. Free.
* ``--rewrite``   — five short rewrites per question, which is what production does. ~150 tokens
  a call, so a 100-question arm is ~75k utility tokens.

Run both on one question set and the difference is the rewriter's contribution to routing.

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
    parser.add_argument(
        "--baseline",
        default=None,
        help="a second corpus to measure in the SAME process, with the same embedder and the "
        "same questions, reporting the delta. A number compared against one from another "
        "session is not a comparison -- the embedder, the question sample and the vector "
        "cache all have to be held fixed, and only one process can promise that.",
    )
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--per-schema",
        type=int,
        default=None,
        help="questions per schema. Default is ALL of them: every one of the 1 351 test "
        "questions falls inside the 57 covered schemas, and this run costs no model call, so "
        "the full set takes ~12 min and $0. It defaulted to 2, which is 114 questions and a "
        "95%% interval near +/-9 pp -- wide enough to hide every effect this tool is used to "
        "detect, for no saving. Pass a small value for a smoke test, not for a result. A flat "
        "--limit is weighted by whichever schema BIRD asked most about, so a per-schema "
        "effect reads as an overall one.",
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

    from governed_bi.model import provider as provider_mod

    embedder = None
    vector_cache = None
    if args.embed:
        from governed_bi.retrieve.vector_cache import vector_cache_from_environment

        # The embedder's gateway is exactly what this tool measures the effect of: the
        # semantic channel is half of routing, so an arm on a different embedding provider
        # is a different recall number, not a detail. Printed for the same reason.
        embed_provider = provider_mod.provider_for("embedding")
        embedder = provider_mod.embedder(provider_mod.default_embedding_model(embed_provider))
        vector_cache = vector_cache_from_environment(model=embedder.requested_model)
        print(f"embedder: {embedder.requested_model} on {embed_provider}")

    utility = None
    if args.rewrite:
        import os

        model_id = os.environ.get("GOVERNED_BI_UTILITY_MODEL") or os.environ["GOVERNED_BI_MODEL"]
        effort = os.environ.get("GOVERNED_BI_UTILITY_MODEL_EFFORT")
        chosen = provider_mod.provider_for("utility")
        utility = provider_mod.chat_model(
            model_id, surface="utility", provider=chosen, effort=effort or None
        )
        print(f"rewriters ON: {model_id} on {chosen} effort={effort or '(default)'}")
    else:
        print("rewriters OFF: facets search the raw question")

    def build(corpus_dir: str):
        # `agent_model=None` on purpose — `routing_recall` documents that the stub answer path
        # costs nothing while facets, routing, retrieval, resolve and connect all run for real.
        return session_mod.from_corpus_dir(
            corpus_dir,
            connector=PostgresConnector(dsn),
            policy=GovernancePolicy(guard_rules_enabled={}),
            agent_model=None,
            utility_model=utility,
            embedder=embedder,
            vector_cache=vector_cache,
        )

    # The *first* corpus decides the question set, and both arms answer the same questions.
    # Letting each derive its own compares two scores over two populations the moment one variant
    # covers a schema the other does not — what `measure/population.py` exists to refuse.
    primary = build(args.corpus_dir)
    schemas = sorted({s for s in primary.structure.table_schemas.values() if s})
    questions = load_questions(
        # `--dataset` is the *directory*, as `run_datalake_eval.py` takes it. Passing it whole
        # raised PermissionError, which on Windows is what opening a directory looks like.
        args.dataset / "test_final.jsonl",
        schemas=schemas,
        limit=args.limit,
        per_schema=args.per_schema,
    )
    gold_sql = {str(q["question_id"]): str(q["gold_sql"]) for q in questions}
    print(f"{len(questions)} questions over {len(schemas)} schemas, top_n={args.top_n}\n")

    def measure(label: str, session) -> dict:
        started = time.time()
        rows = routing_recall(questions, session=session, top_n=args.top_n)
        took = time.time() - started
        # Table coverage is the number to lead with: schema recall@k can look fine while a turn
        # still cannot answer, because the per-type budget licenses at most 8 ranked tables.
        out = {
            # The content digest, not the directory name: a variant iterated in place keeps its
            # path and changes its meaning, so two artifacts would claim the same treatment while
            # describing different corpora (v1's `corpus_content_hash == "unknown"` defect).
            "corpus_dir": str(session.corpus_root),
            "corpus_content_hash": session.corpus_content_hash,
            "n_questions": len(rows),
            "routing": summarise_routing(rows),
            "coverage": table_coverage(rows, gold_sql),
            "mean_licensed": round(
                sum(len(r.get("licensed") or ()) for r in rows) / max(len(rows), 1), 1
            ),
            "wall_s": round(took),
            "rows": rows,
        }
        print(f"=== {label}  {out['corpus_content_hash'][:12]}  ({out['wall_s']}s)")
        print(json.dumps({k: v for k, v in out.items() if k != "rows"}, indent=2, default=str))
        return out

    arms = {"treatment": measure("treatment", primary)}
    if args.baseline:
        arms["baseline"] = measure("baseline", build(args.baseline))
        if arms["baseline"]["corpus_content_hash"] == arms["treatment"]["corpus_content_hash"]:
            print(
                "\n!! both arms digest to the same corpus: this is one arm measured twice, "
                "and any delta below is noise in the router's tie-breaking, not an effect",
                file=sys.stderr,
            )
        t, b = arms["treatment"], arms["baseline"]
        print("\n--- delta (treatment - baseline) ---")
        for key in ("recall@1", "recall@3", "recall@5", "recall@10", "reached_gold"):
            print(f"  {key:<16} {t['routing'][key] - b['routing'][key]:+.4f}")
        gap = (
            t["coverage"]["all_gold_tables_licensed"] - b["coverage"]["all_gold_tables_licensed"]
        )
        print(
            f"  {'coverage':<16} {gap:+.4f}   "
            f"({b['coverage']['all_gold_tables_licensed']:.3f} -> "
            f"{t['coverage']['all_gold_tables_licensed']:.3f})   <- the number that bounds EX"
        )
        print(f"  {'mean_licensed':<16} {t['mean_licensed'] - b['mean_licensed']:+.1f}")

    missed = [r for r in arms["treatment"]["rows"] if not r["hit"]]
    print(f"\n{len(missed)} misses; the gold schema's rank where it was scored at all:")
    for row in missed[:15]:
        print(f"  {row['db_id']:<24} rank={row['rank']!s:<6} selected={row['selected']}")

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(
            json.dumps(
                {
                    "arm": "rewrite" if args.rewrite else "no_rewrite",
                    "top_n": args.top_n,
                    "embed": args.embed,
                    "per_schema": args.per_schema,
                    "arms": arms,
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
