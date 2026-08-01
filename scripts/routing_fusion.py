"""E3: should the schema shortlist fuse BM25 with the embedder, and can the picker
be given a confidence signal?

Offline. One embedding pass per channel, cached to disk; every fusion weight and
every gate threshold after that is free. No chat model.

Two questions, one artifact:

**Fusion.** ``shortlist_schemas``'s docstring refuses to fuse on the strength of a
probe that measured BM25 recall@3 = 0.35. On the curated corpus E1 measured 0.844.
The two channels now differ by 0.8pp at @3 and BM25 *wins* at @1, which is the shape
RRF exists for. This script ranks every test question on each channel, caches the
per-question ``(schema, score)`` lists, and then sweeps RRF over them.

A third channel is included because the first two share a defect: the embedding
channel embeds one document per SCHEMA, and ``works_cycles``'s document is 73 tables
concatenated. A question about sales orders is being matched against a vector that
averages payroll, purchasing and geography. ``tbl_max`` embeds each TABLE and scores
a schema by its best table — max-pooling instead of mean-by-concatenation. It costs
the same tokens (a schema document *is* its tables' documents joined) and it reuses
the vectors R1 needs anyway.

**Gate.** The run's rows record ``gold_schema_rank`` and ``pick_hit``, so once this
script recomputes the ranking *scores* the counterfactual "keep rank 1 whenever its
margin over rank 2 exceeds t, and skip the LLM" is a table lookup. It reports the
questions such a gate would save and the ones it would break, per threshold — the
gate is only defensible if saved > broken with margin.

Usage::

    uv run python scripts/routing_fusion.py \\
        --corpus runs/datalake/.../corpus_curated \\
        --rows   runs/datalake/.../generations.curated.jsonl \\
        --cache  runs/ablation/e3-rankings.json \\
        --out    runs/ablation/e3-fusion.json
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
from governed_bi.retrieval.embedding import fuse_rankings
from governed_bi.retrieval.rvgd import BM25Index, asset_document
from governed_bi.retrieval.schema_router import _analyst_tables, schema_documents

TOP_KS = (1, 3, 5, 10, 20)


def _load_questions(bird_dir: Path, split: str) -> list[dict[str, Any]]:
    path = bird_dir / "eval_dataset" / f"{split}_final.jsonl"
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _load_rows(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None:
        return {}
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            out[r["question_id"]] = r
    return out


# --------------------------------------------------------------------------- #
# Channels
# --------------------------------------------------------------------------- #


def _normalize(mat: "Any") -> "Any":
    import numpy as np

    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms[norms == 0.0] = 1.0
    return mat / norms


def build_rankings(
    corpus, questions: list[dict[str, Any]], embedder_names: list[str]
) -> dict[str, dict[str, list[tuple[str, float]]]]:
    """``channel -> question_id -> [(schema, score), ...]`` descending.

    Cosines are computed with numpy rather than :func:`governed_bi.llm.cosine`. The
    ``tbl_max`` channel scores 656 table vectors per question against 3072 dims; in
    the pure-Python loop that is ~2M float ops per question and the sweep would take
    longer than the ladder it is meant to pre-empt. The values are the same to float
    tolerance, and the sweep is a measurement, not the serve path.
    """
    import numpy as np

    schemas = sorted(schema_documents(corpus))
    docs = schema_documents(corpus)
    tables = list(_analyst_tables(corpus).values())
    table_docs = [asset_document(t) for t in tables]
    table_schema_idx = np.array([schemas.index(t.schema) for t in tables])

    out: dict[str, dict[str, list[tuple[str, float]]]] = {}

    print("-- bm25 (schema documents) --", flush=True)
    t0 = time.time()
    bm25 = BM25Index.from_documents(docs)
    out["bm25"] = {q["question_id"]: bm25.rank(q["question"]) for q in questions}
    print(f"   {time.time()-t0:.0f}s", flush=True)

    print("-- bm25_tbl_max (per-table documents, max-pooled to schema) --", flush=True)
    t0 = time.time()
    tbl_bm25 = BM25Index.from_documents(
        {f"{i}": d for i, d in enumerate(table_docs)}
    )
    tbl_ranks: dict[str, list[tuple[str, float]]] = {}
    for q in questions:
        best: dict[str, float] = {}
        for tid, score in tbl_bm25.rank(q["question"]):
            s = tables[int(tid)].schema
            if score > best.get(s, 0.0):
                best[s] = score
        tbl_ranks[q["question_id"]] = sorted(best.items(), key=lambda p: (-p[1], p[0]))
    out["bm25_tbl_max"] = tbl_ranks
    print(f"   {time.time()-t0:.0f}s", flush=True)

    base = load_settings().models
    from governed_bi.llm.langchain_client import LangChainEmbedder

    for name in embedder_names:
        emb = LangChainEmbedder.from_config(replace(base, embedding_model=name))
        short = name.replace("text-embedding-3-", "")

        print(f"-- {name}: schema docs ({len(schemas)}) --", flush=True)
        t0 = time.time()
        schema_mat = _normalize(np.array(emb.embed([docs[s] for s in schemas]), dtype=np.float32))
        print(f"   {time.time()-t0:.0f}s", flush=True)

        print(f"-- {name}: table docs ({len(tables)}) --", flush=True)
        t0 = time.time()
        table_mat = _normalize(np.array(emb.embed(table_docs), dtype=np.float32))
        print(f"   {time.time()-t0:.0f}s", flush=True)

        print(f"-- {name}: {len(questions)} questions --", flush=True)
        t0 = time.time()
        qvecs = []
        B = 256
        texts = [q["question"] for q in questions]
        for i in range(0, len(texts), B):
            qvecs.extend(emb.embed(texts[i : i + B]))
            print(f"   {min(i+B, len(texts))}/{len(texts)}  ({time.time()-t0:.0f}s)", flush=True)
        qmat = _normalize(np.array(qvecs, dtype=np.float32))

        sim_schema = qmat @ schema_mat.T           # (Q, S)
        sim_table = qmat @ table_mat.T             # (Q, T)
        # Max-pool table similarities into their schema.
        pooled = np.full((len(questions), len(schemas)), -1.0, dtype=np.float32)
        for col in range(len(tables)):
            s = table_schema_idx[col]
            np.maximum(pooled[:, s], sim_table[:, col], out=pooled[:, s])

        for label, mat in ((f"emb_{short}", sim_schema), (f"tblmax_{short}", pooled)):
            per_q: dict[str, list[tuple[str, float]]] = {}
            for row, q in enumerate(questions):
                pairs = [
                    (schemas[j], float(mat[row, j]))
                    for j in range(len(schemas))
                    if mat[row, j] > 0.0
                ]
                pairs.sort(key=lambda p: (-p[1], p[0]))
                per_q[q["question_id"]] = pairs
            out[label] = per_q
    return out


# --------------------------------------------------------------------------- #
# Evaluation
# --------------------------------------------------------------------------- #


def recall_table(
    ranked: dict[str, list[tuple[str, float]]], gold: dict[str, str]
) -> dict[str, float]:
    n = len(gold)
    hits = {k: 0 for k in TOP_KS}
    for qid, g in gold.items():
        names = [s for s, _ in ranked.get(qid, ())]
        r = names.index(g) + 1 if g in names else None
        for k in TOP_KS:
            if r is not None and r <= k:
                hits[k] += 1
    return {str(k): hits[k] / n for k in TOP_KS}


def fuse(
    channels: list[dict[str, list[tuple[str, float]]]],
    qids: list[str],
    weights: list[float],
    k: int,
) -> dict[str, list[tuple[str, float]]]:
    return {
        qid: fuse_rankings(*[c.get(qid, []) for c in channels], k=k, weights=weights)
        for qid in qids
    }


def gate_analysis(
    ranked: dict[str, list[tuple[str, float]]],
    rows: dict[str, dict[str, Any]],
    gold: dict[str, str],
    thresholds: list[float],
) -> list[dict[str, Any]]:
    """Counterfactual for "keep rank 1 without asking the LLM when it is confident".

    Confidence is the **relative** margin ``(s1 - s2) / s1`` of the recomputed
    ranking, not the raw gap: cosine magnitudes are not comparable across questions
    (a long question has a different self-similarity floor), and an absolute gap
    threshold would therefore fire question-dependently for no reason anyone can
    state.

    ``saved`` = the LLM overrode a correct rank 1 and the gate would have stopped it.
    ``broken`` = the LLM corrected a wrong rank 1 and the gate would have prevented
    that. Net = saved - broken, in pick-accuracy points.
    """
    out = []
    for t in thresholds:
        c: Counter[str] = Counter()
        for qid, g in gold.items():
            row = rows.get(qid)
            if row is None:
                continue
            pairs = ranked.get(qid, [])
            if len(pairs) < 2:
                continue
            s1, s2 = pairs[0][1], pairs[1][1]
            margin = (s1 - s2) / s1 if s1 > 0 else 0.0
            fires = margin >= t
            rank1_right = pairs[0][0] == g
            pick_right = bool(row.get("pick_hit"))
            c["n"] += 1
            c["fires"] += int(fires)
            if fires:
                if rank1_right and not pick_right:
                    c["saved"] += 1
                elif not rank1_right and pick_right:
                    c["broken"] += 1
                elif rank1_right and pick_right:
                    c["agree_right"] += 1
                else:
                    c["agree_wrong"] += 1
        out.append(
            {
                "threshold": t,
                "n": c["n"],
                "coverage": c["fires"] / c["n"] if c["n"] else 0.0,
                "saved": c["saved"],
                "broken": c["broken"],
                "net": c["saved"] - c["broken"],
                "net_pp": 100.0 * (c["saved"] - c["broken"]) / c["n"] if c["n"] else 0.0,
                "gate_precision": (
                    (c["saved"] + c["agree_right"]) / c["fires"] if c["fires"] else 0.0
                ),
                "llm_calls_avoided": c["fires"],
            }
        )
    return out


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--corpus", type=Path, required=True)
    p.add_argument("--rows", type=Path, default=None)
    p.add_argument("--bird-dir", type=Path, default=Path("../BIRD-Data-Obfuscation"))
    p.add_argument("--split", default="test")
    p.add_argument("--embedders", default="text-embedding-3-large")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--cache", type=Path, default=None, help="reuse/write raw rankings")
    p.add_argument("--out", type=Path, default=None)
    args = p.parse_args()

    corpus = load_corpus(args.corpus)
    questions = _load_questions(args.bird_dir, args.split)
    if args.limit:
        questions = questions[: args.limit]
    qids = [q["question_id"] for q in questions]
    gold = {q["question_id"]: q["db_id"] for q in questions}
    rows = _load_rows(args.rows)

    names = [e.strip() for e in args.embedders.split(",") if e.strip()]
    if args.cache and args.cache.exists():
        print(f"reusing rankings from {args.cache}")
        raw = json.loads(args.cache.read_text(encoding="utf-8"))
        ranked = {
            ch: {qid: [(s, float(v)) for s, v in pairs] for qid, pairs in per_q.items()}
            for ch, per_q in raw["rankings"].items()
        }
    else:
        ranked = build_rankings(corpus, questions, names)
        if args.cache:
            args.cache.parent.mkdir(parents=True, exist_ok=True)
            args.cache.write_text(
                json.dumps({"corpus": str(args.corpus), "rankings": ranked}),
                encoding="utf-8",
            )
            print(f"wrote {args.cache}")

    # ---- fidelity check against the recorded run -------------------------- #
    if rows:
        # Exact top-10 ORDER is the wrong fidelity test: positions 6-10 are separated
        # by ~1e-4 of cosine and a batched embed and a single embed_one disagree there
        # at float tolerance. What the gate analysis actually consumes is the identity
        # of rank 1 and rank 2 and the gold's rank, so those are what is checked.
        agree_order = agree_set = agree_rank = agree_top1 = tot = 0
        for qid in qids:
            row = rows.get(qid)
            if not row or not row.get("shortlisted_schemas"):
                continue
            tot += 1
            names = [s for s, _ in ranked["emb_large"].get(qid, [])]
            recomputed, got = names[:10], list(row["shortlisted_schemas"])
            agree_order += int(recomputed == got)
            agree_set += int(set(recomputed) == set(got))
            agree_top1 += int(bool(recomputed) and bool(got) and recomputed[0] == got[0])
            g = gold[qid]
            mine = names.index(g) + 1 if g in names[:10] else None
            agree_rank += int(row.get("gold_schema_rank") == mine)
        d = max(tot, 1)
        print(
            f"\nfidelity vs the recorded run (emb_large, n={tot}): "
            f"gold rank {agree_rank/d:.3f} | rank-1 identity {agree_top1/d:.3f} | "
            f"top-10 set {agree_set/d:.3f} | top-10 exact order {agree_order/d:.3f}"
        )

    # ---- single channels --------------------------------------------------- #
    results: list[dict[str, Any]] = []
    for ch in sorted(ranked):
        results.append({"label": ch, "recall_at": recall_table(ranked[ch], gold)})

    # ---- fusions ----------------------------------------------------------- #
    combos: list[tuple[str, list[str], list[float], int]] = []
    lex, sem = "bm25", "emb_large"
    if lex in ranked and sem in ranked:
        for w in (0.5, 1.0, 2.0):
            combos.append((f"rrf({lex},{sem}) w_lex={w}", [lex, sem], [w, 1.0], 60))
        combos.append((f"rrf({lex},{sem}) k=10", [lex, sem], [1.0, 1.0], 10))
        combos.append((f"rrf({lex},{sem}) k=20", [lex, sem], [1.0, 1.0], 20))
    if "tblmax_large" in ranked:
        combos.append(("rrf(bm25,tblmax_large)", ["bm25", "tblmax_large"], [1.0, 1.0], 60))
        combos.append(
            ("rrf(emb_large,tblmax_large)", ["emb_large", "tblmax_large"], [1.0, 1.0], 60)
        )
        combos.append(
            (
                "rrf(bm25,emb_large,tblmax_large)",
                ["bm25", "emb_large", "tblmax_large"],
                [1.0, 1.0, 1.0],
                60,
            )
        )
    if "bm25_tbl_max" in ranked:
        combos.append(
            (
                "rrf(bm25_tbl_max,emb_large)",
                ["bm25_tbl_max", "emb_large"],
                [1.0, 1.0],
                60,
            )
        )
        combos.append(
            (
                "rrf(bm25,bm25_tbl_max,emb_large,tblmax_large)",
                ["bm25", "bm25_tbl_max", "emb_large", "tblmax_large"],
                [1.0, 1.0, 1.0, 1.0],
                60,
            )
        )
    fused_store: dict[str, dict[str, list[tuple[str, float]]]] = {}
    for label, chs, ws, k in combos:
        if not all(c in ranked for c in chs):
            continue
        f = fuse([ranked[c] for c in chs], qids, ws, k)
        fused_store[label] = f
        results.append({"label": label, "recall_at": recall_table(f, gold)})

    print(f"\n{'='*84}")
    print(f"{'channel':<44}" + "".join(f"{'@'+str(k):>8}" for k in TOP_KS))
    for r in results:
        print(f"{r['label']:<44}" + "".join(f"{r['recall_at'][str(k)]:>8.3f}" for k in TOP_KS))

    # ---- gate -------------------------------------------------------------- #
    gates: dict[str, Any] = {}
    if rows:
        thresholds = [0.0, 0.02, 0.05, 0.08, 0.10, 0.15, 0.20, 0.30]
        gate_channels = [
            ("emb_large", ranked.get("emb_large")),
            ("tblmax_large", ranked.get("tblmax_large")),
            ("rrf(emb_large,tblmax_large)", fused_store.get("rrf(emb_large,tblmax_large)")),
            ("bm25", ranked.get("bm25")),
        ]
        for label, r in gate_channels:
            if r is None:
                continue
            g = gate_analysis(r, rows, gold, thresholds)
            gates[label] = g
            print(f"\nconfidence gate on {label} (relative margin (s1-s2)/s1):")
            print(
                f"    {'t':>6}{'coverage':>10}{'saved':>8}{'broken':>8}{'net':>6}"
                f"{'net pp':>9}{'gate prec':>11}"
            )
            for e in g:
                print(
                    f"    {e['threshold']:>6.2f}{e['coverage']:>10.3f}{e['saved']:>8}"
                    f"{e['broken']:>8}{e['net']:>6}{e['net_pp']:>9.2f}"
                    f"{e['gate_precision']:>11.3f}"
                )

        # Hedging: how much of the residual sits at rank 2?
        c: Counter[str] = Counter()
        for qid, gsch in gold.items():
            row = rows.get(qid)
            if row is None:
                continue
            names_ = [s for s, _ in ranked["emb_large"].get(qid, [])]
            r = names_.index(gsch) + 1 if gsch in names_ else None
            c["n"] += 1
            c["pick_hit"] += int(bool(row.get("pick_hit")))
            if r == 1:
                c["rank1"] += 1
            if r is not None and r <= 2:
                c["rank<=2"] += 1
            if r is not None and r <= 3:
                c["rank<=3"] += 1
            if not row.get("pick_hit") and r is not None and r <= 2:
                c["miss_but_top2"] += 1
        print(
            f"\nhedging headroom: pick_hit={c['pick_hit']/c['n']:.3f}  "
            f"rank1={c['rank1']/c['n']:.3f}  rank<=2={c['rank<=2']/c['n']:.3f}  "
            f"rank<=3={c['rank<=3']/c['n']:.3f}  "
            f"missed picks whose gold is in top-2: {c['miss_but_top2']}"
        )
        gates["hedging"] = dict(c)

    payload = {
        "corpus": str(args.corpus),
        "n_questions": len(questions),
        "top_ks": list(TOP_KS),
        "results": results,
        "gates": gates,
    }
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
