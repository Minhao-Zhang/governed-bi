"""Does the schema facet want a word-soup query once the schema summary IS a word soup?

``register/facets.py`` removed ``facet_schema`` from ``FACET_EXTRACTS`` on a measurement: the raw
question beat every rewrite of it on a 114-question screen. That was taken against
the **gold** schema summaries — sparse identifier lists — a document form the corpus under test no
longer has, since ``tools/densify_summaries.py`` made those summaries dense term lists and
``facet_schema_query`` variant ``v2`` emits terms rather than a sentence.

Two factors, and the cell this tool was built to fill:

.. code-block:: text

                            raw question      rewritten (term soup)
    gold summaries          measured           measured  (the loss that removed it)
    dense summaries         measured (+6 pp)  measured 2026-08-05 -- null

The claim under test is the **interaction** — the rewriter's sign flipping with the document form
— not either main effect, both already known. It came back null: 342 questions, paired,
interaction -1.17 pp on recall@3 and -0.64 pp on gold-table coverage, every p >= 0.45
(``register/citations.py``, artifact ``runs/ablation/summary-form-1351-20260805.json`` — not in
this tree or in git, ``runs/`` being gitignored; re-measurable by running this tool over both
document forms).
The other four facets are held OFF in every cell so
they contribute a constant to ``route``'s sum and only the schema facet's query form varies;
re-testing in the production configuration is a different run.

One confound: ``densify_summaries`` rewrites table summaries too and ``facet_entity`` reads those.
It is constant across the query-form factor, so the interaction stays clean and only the main
effect of summary form carries it.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time

REPO = pathlib.Path(__file__).resolve().parents[1]

DEFAULT_DATASET = REPO.parent / "BIRD-Data-Obfuscation" / "eval_dataset" / "test_final.jsonl"


def paired(reference: dict[str, bool], arm: dict[str, bool], *, ref_label: str, arm_label: str):
    """The paired test, through ``measure.stats.mcnemar``.

    **This module used to carry its own McNemar** (audit D5), and
    ``tools/check_one_implementation.py`` declares ``mcnemar`` a singleton whose stated reason is
    that "v1 had two McNemars ... which one ran changes whether a ladder step is significant".
    The gate did not see this one because it scanned ``src/governed_bi`` only; it scans the
    singletons in ``tools/`` too now.

    The copy was not merely duplicated, it was weaker in three ways that all point the same
    direction — toward reporting a result as more informative than it is:

    * it **silently intersected** the two unit sets (``set(reference) & set(arm)``) where
      ``stats.mcnemar`` refuses, so a question missing from one arm quietly left the comparison
      instead of stopping it;
    * it returned **no minimum detectable effect**, and printed ``p=1.0`` on zero discordant
      pairs with nothing carrying how uninformative that is — ``stats.mde`` exists for exactly
      that case;
    * it ``round()``ed, which is the thing ``check_measurement_locality`` is about.

    Returns the ``McNemarResult`` so the caller reads named fields rather than a dict.
    """
    # Imported inside the function, as every other ``governed_bi`` import in this file is: the
    # module has to be importable for ``--help`` without the package resolving.
    from governed_bi.measure.population import Population  # noqa: PLC0415
    from governed_bi.measure.stats import mcnemar  # noqa: PLC0415

    return mcnemar(
        Population.of(ref_label, [{"question_id": q, "hit": v} for q, v in reference.items()]),
        Population.of(arm_label, [{"question_id": q, "hit": v} for q, v in arm.items()]),
        "hit",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="query_summary_alignment", description=__doc__)
    # Required, not defaulted: the old defaults named directories under the gitignored
    # `corpora/`, which exist on one machine and nowhere else.
    parser.add_argument("--gold-corpus", required=True, help="baseline corpus to compare")
    parser.add_argument("--dense-corpus", required=True, help="variant corpus to compare")
    parser.add_argument("--dataset", type=pathlib.Path, default=DEFAULT_DATASET)
    parser.add_argument(
        "--per-schema",
        type=int,
        default=6,
        help="questions per schema. 6 gives ~342, which resolves about 3 pp -- the historical "
        "effects here are 1.8-4.4 pp, so 2 per schema (114, ~5.3 pp) cannot see them.",
    )
    parser.add_argument("--top-n", type=int, default=3)
    parser.add_argument(
        "--variant",
        default="v2",
        help="which facet_schema_query variant to send. v2 emits terms; v1 emits a description.",
    )
    parser.add_argument("--out", type=pathlib.Path, default=None)
    args = parser.parse_args(argv)

    from governed_bi import credentials

    credentials.load_into_environ()
    dsn = credentials.secret(*credentials.PG_DSN_NAMES)
    if not dsn:
        print("no database credential reachable", file=sys.stderr)
        return 2

    import os

    from governed_bi.datasource.postgres import PostgresConnector
    from governed_bi.eval.datalake import (
        gold_tables,
        load_questions,
        routing_recall,
        summarise_routing,
        table_coverage,
    )
    from governed_bi.govern.policy import GovernancePolicy
    from governed_bi.model import provider as provider_mod
    from governed_bi.register import prompts as prompts_mod
    from governed_bi.retrieve.vector_cache import vector_cache_from_environment
    from governed_bi.serve import session as session_mod

    model_id = os.environ.get("GOVERNED_BI_UTILITY_MODEL") or os.environ["GOVERNED_BI_MODEL"]
    effort = os.environ.get("GOVERNED_BI_UTILITY_MODEL_EFFORT")
    utility = provider_mod.chat_model(model_id, surface="utility", effort=effort or None)

    embedder = provider_mod.embedder(provider_mod.default_embedding_model())
    cache = vector_cache_from_environment(model=embedder.requested_model)

    def build(corpus_dir: str):
        return session_mod.from_corpus_dir(
            corpus_dir,
            connector=PostgresConnector(dsn),
            policy=GovernancePolicy(guard_rules_enabled={}),
            agent_model=None,
            utility_model=utility,
            embedder=embedder,
            vector_cache=cache,
        )

    sessions = {"gold": build(args.gold_corpus), "dense": build(args.dense_corpus)}
    schemas = sorted({s for s in sessions["gold"].structure.table_schemas.values() if s})
    questions = load_questions(args.dataset, schemas=schemas, per_schema=args.per_schema)
    gold_sql = {str(q["question_id"]): str(q["gold_sql"]) for q in questions}
    print(
        f"{len(questions)} questions over {len(schemas)} schemas, top_n={args.top_n}, "
        f"variant={args.variant}\n"
        f"other four facets: rewriters OFF in every cell (the constant)\n",
        flush=True,
    )

    def covered(rows) -> dict[str, bool]:
        out: dict[str, bool] = {}
        for row in rows:
            needed = gold_tables(gold_sql.get(str(row["question_id"])) or "")
            if not needed:
                continue
            licensed = {str(t).lower() for t in (row.get("licensed") or ())}
            out[str(row["question_id"])] = all(t.lower() in licensed for t in needed)
        return out

    #: The switch is the mapping, not the registry (``register/prompts.py``). An empty mapping means
    #: every facet searches the user's own words; one entry means only the schema facet rewrites.
    SWITCH = {
        "raw": {},
        "rewritten": {"facet_schema": "facet_schema_query"},
    }

    results: dict[str, dict] = {}
    per_question: dict[str, dict[str, bool]] = {}
    for summary_form in ("gold", "dense"):
        for query_form, mapping in SWITCH.items():
            cell = f"{summary_form}/{query_form}"
            # Patched on the module the node imports from, for the duration of this cell only.
            # An experiment flag in `register/` would be a knob nothing sets in production.
            prompts_mod.FACET_QUERY_PROMPTS = mapping  # type: ignore[assignment]
            started = time.time()
            rows = routing_recall(questions, session=sessions[summary_form], top_n=args.top_n)
            per_question[cell] = covered(rows)
            routing = summarise_routing(rows)
            results[cell] = {
                "coverage": table_coverage(rows, gold_sql)["all_gold_tables_licensed"],
                "recall@1": routing["recall@1"],
                "recall@3": routing["recall@3"],
                "recall@5": routing["recall@5"],
                "mean_licensed": round(
                    sum(len(r.get("licensed") or ()) for r in rows) / max(len(rows), 1), 2
                ),
                "corpus_content_hash": sessions[summary_form].corpus_content_hash,
                "wall_s": round(time.time() - started),
            }
            print(f"=== {cell:<18} {json.dumps(results[cell], default=str)}", flush=True)

    print("\n" + "=" * 78)
    print(f"{'coverage':<12}{'raw':>12}{'rewritten':>12}{'effect of rewrite':>20}")
    for summary_form in ("gold", "dense"):
        raw = results[f"{summary_form}/raw"]["coverage"]
        rew = results[f"{summary_form}/rewritten"]["coverage"]
        print(f"{summary_form:<12}{raw:>12.4f}{rew:>12.4f}{rew - raw:>+20.4f}")
    gold_effect = (
        results["gold/rewritten"]["coverage"] - results["gold/raw"]["coverage"]
    )
    dense_effect = (
        results["dense/rewritten"]["coverage"] - results["dense/raw"]["coverage"]
    )
    print(f"\nINTERACTION (dense effect - gold effect): {dense_effect - gold_effect:+.4f}")
    print("A positive interaction is the hypothesis: the rewriter helps once the document is soup.")

    print("\nPaired tests (McNemar exact, gold-table coverage):")
    for ref, arm in (
        ("gold/raw", "gold/rewritten"),
        ("dense/raw", "dense/rewritten"),
        ("gold/raw", "dense/raw"),
        ("dense/raw", "gold/rewritten"),
    ):
        stat = paired(per_question[ref], per_question[arm], ref_label=ref, arm_label=arm)
        # `render()` carries the discordance and the minimum detectable effect with the p-value,
        # so a null result cannot be read as "no difference" when it means "cannot tell".
        print(f"  {arm:<18} vs {ref:<18} {stat.render()}")
        results.setdefault("mcnemar", {})[f"{arm}_vs_{ref}"] = {
            "n_pairs": stat.n_pairs,
            "gained": stat.only_b,
            "lost": stat.only_a,
            "discordance": stat.discordance,
            "p_value": stat.p_value,
            "minimum_detectable": stat.minimum_detectable,
            "is_decisive": stat.is_decisive,
        }

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(
            json.dumps(
                {
                    "n_questions": len(questions),
                    "per_schema": args.per_schema,
                    "top_n": args.top_n,
                    "variant": args.variant,
                    "cells": results,
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
