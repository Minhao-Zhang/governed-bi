"""E2: does the LLM schema picker ever SEE the tables the answer needs?

Offline, no chat model. Pure CPU over a built corpus plus the BIRD gold SQL, except
for the optional ``--embedder`` variant (one batched embedding pass, cents).

``retrieval/schema_router.py`` caps the picker's per-candidate summary at
``SCHEMA_PICK_MAX_TABLES = 15`` and fills those 15 by sorting on ``physical_name``
— **alphabetically**. On a 73-table schema that is a coin flip dressed as evidence.
This script measures the coin flip:

    for every test question, take its GOLD schema, render the 15 tables the picker
    would see, and ask whether the tables the gold SQL actually reads are among them.

Then it re-ranks the same 15 slots by question relevance (BM25 over each table's
``asset_document`` — the same text the schema-level index is built from) and asks
again. The delta is the ceiling on what fixing R1 can buy, measured before any model
is called.

Every variant is reported side by side in one pass; there is no flag to choose one.

``alpha``
    Today: ``sorted(tables, key=physical_name)[:15]``.
``rel``
    Scoring tables first (BM25 within the schema, descending), then the rest in
    alphabetical order. A schema whose tables score nothing renders identically to
    ``alpha``, so this is a refinement of today's order, not a replacement for it.
``rel_guard``
    ``rel``, but only for schemas that carry curated language (see
    ``--desc-coverage-min``). A schema with no descriptions can only match on its
    physical identifiers, and under obfuscation those are pinyin — the ranking then
    promotes whichever table happens to have an English-looking column token and
    *evicts* the gold table. Measured on ``mondial_geo``, which has 0/42 table and
    0/275 column descriptions.
``rel_desconly``
    BM25 over the curated prose only (descriptions + grain), identifiers excluded.
    The counter-hypothesis to ``rel_guard``: if identifier noise is the problem,
    dropping identifiers should fix it everywhere rather than schema by schema.
``rel_hybrid``
    Prose first, identifiers only to order the tables prose did not score.
``rel_emb`` (``--embedder``)
    Cosine over per-table vectors — the same ones the ``tblmax`` shortlist channel
    in ``scripts/routing_fusion.py`` max-pools. If it wins, R1 and the shortlist
    share one index.

Usage::

    uv run python scripts/pick_evidence_probe.py \\
        --corpus runs/datalake/.../corpus_curated \\
        --rows   runs/datalake/.../generations.curated.jsonl \\
        --embedder text-embedding-3-large \\
        --out    runs/ablation/e2-pick-evidence.json
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from governed_bi.corpus.loader import load_corpus
from governed_bi.retrieval.rvgd import (
    BM25Index,
    _sql_table_ids,
    asset_document,
    phys_name_to_table_id,
)
from governed_bi.retrieval.schema_router import (
    SCHEMA_PICK_MAX_TABLES,
    _analyst_tables,
    _schema_pick_summary,
)

VARIANTS = ("alpha", "rel", "rel_guard", "rel_desconly", "rel_hybrid")
#: Appended when ``--embedder`` is given: cosine over the SAME per-table vectors the
#: ``tblmax`` shortlist channel uses (``scripts/routing_fusion.py``). If it wins, R1
#: and the shortlist can share one table index; if it does not, R1 stays free.
EMB_VARIANT = "rel_emb"


def _load_questions(bird_dir: Path, split: str) -> list[dict[str, Any]]:
    path = bird_dir / "eval_dataset" / f"{split}_final.jsonl"
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _load_rows(path: Path | None) -> dict[str, dict[str, Any]]:
    """Recorded per-question rows from a ladder arm, keyed by question id."""
    if path is None:
        return {}
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            out[r["question_id"]] = r
    return out


def prose_document(table) -> str:
    """A table's curated NATURAL LANGUAGE only — no physical identifiers.

    ``asset_document`` deliberately concatenates identifiers and prose because the
    schema-level index wants both. For ranking tables *inside* one schema the two
    behave differently under obfuscation, so ``rel_desconly`` needs them separable.
    """
    parts = [table.description or "", table.grain or ""]
    parts.extend(c.description or "" for c in table.columns)
    return " ".join(p for p in parts if p)


class SchemaTables:
    """Per-schema table lists plus per-schema BM25 indexes over table documents.

    IDF is computed **within the schema**, not corpus-wide, because the job here is
    to discriminate 73 sibling tables from each other. A term every table in
    ``works_cycles`` carries ("business", "entity") is worthless for that choice even
    if it is globally rare, and a corpus-wide index would score it as gold.
    """

    def __init__(self, corpus) -> None:
        self.by_schema: dict[str, list] = {}
        for t in _analyst_tables(corpus).values():
            self.by_schema.setdefault(t.schema, []).append(t)
        for tables in self.by_schema.values():
            tables.sort(key=lambda a: a.physical_name)
        self.index: dict[str, BM25Index] = {
            s: BM25Index.from_documents({t.id: asset_document(t) for t in tables})
            for s, tables in self.by_schema.items()
        }
        self.prose_index: dict[str, BM25Index] = {
            s: BM25Index.from_documents({t.id: prose_document(t) for t in tables})
            for s, tables in self.by_schema.items()
        }
        #: Fraction of a schema's tables carrying a curated table description.
        self.desc_coverage: dict[str, float] = {
            s: sum(1 for t in ts if (t.description or "").strip()) / len(ts)
            for s, ts in self.by_schema.items()
        }

    def _fill(self, schema: str, ranked_ids: list[str], n: int) -> list[str]:
        """Ranked ids first, then the schema's remaining tables alphabetically."""
        seen = set(ranked_ids)
        out = list(ranked_ids)
        out.extend(t.id for t in self.by_schema[schema] if t.id not in seen)
        return out[:n]

    def add_embeddings(self, embedder, questions: list[dict[str, Any]]) -> None:
        """Embed every table once and every question once (numpy cosine, offline)."""
        import numpy as np

        def norm(m):
            k = np.linalg.norm(m, axis=1, keepdims=True)
            k[k == 0.0] = 1.0
            return m / k

        ids = [t.id for ts in self.by_schema.values() for t in ts]
        texts = [asset_document(t) for ts in self.by_schema.values() for t in ts]
        tmat = norm(np.array(embedder.embed(texts), dtype=np.float32))
        qtexts = [q["question"] for q in questions]
        qvecs: list[Any] = []
        for i in range(0, len(qtexts), 256):
            qvecs.extend(embedder.embed(qtexts[i : i + 256]))
        qmat = norm(np.array(qvecs, dtype=np.float32))
        sim = qmat @ tmat.T
        col = {tid: j for j, tid in enumerate(ids)}
        self._emb_rank: dict[str, dict[str, list[str]]] = {}
        for r, q in enumerate(questions):
            per: dict[str, list[str]] = {}
            for s, ts in self.by_schema.items():
                order = sorted(ts, key=lambda t: (-float(sim[r, col[t.id]]), t.physical_name))
                per[s] = [t.id for t in order]
            self._emb_rank[q["question_id"]] = per

    def shown(
        self,
        variant: str,
        schema: str,
        question: str,
        n: int,
        *,
        cov_min: float,
        qid: str | None = None,
    ) -> list[str]:
        if variant == EMB_VARIANT:
            return self._emb_rank[qid][schema][:n]
        if variant == "alpha":
            return [t.id for t in self.by_schema[schema][:n]]
        if variant == "rel":
            return self._fill(schema, [i for i, _ in self.index[schema].rank(question)], n)
        if variant == "rel_guard":
            if self.desc_coverage[schema] < cov_min:
                return [t.id for t in self.by_schema[schema][:n]]
            return self._fill(schema, [i for i, _ in self.index[schema].rank(question)], n)
        if variant == "rel_desconly":
            return self._fill(
                schema, [i for i, _ in self.prose_index[schema].rank(question)], n
            )
        if variant == "rel_hybrid":
            # Curated prose decides first; identifiers only order the tables the prose
            # channel did not score at all. Three tiers, each strictly weaker than the
            # one above: prose match > identifier match > declaration/alphabetical.
            prose = [i for i, _ in self.prose_index[schema].rank(question)]
            seen = set(prose)
            ident = [i for i, _ in self.index[schema].rank(question) if i not in seen]
            return self._fill(schema, prose + ident, n)
        raise ValueError(variant)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--corpus", type=Path, required=True)
    p.add_argument("--rows", type=Path, default=None, help="generations.<arm>.jsonl")
    p.add_argument("--bird-dir", type=Path, default=Path("../BIRD-Data-Obfuscation"))
    p.add_argument("--split", default="test")
    p.add_argument("--max-tables", type=int, default=SCHEMA_PICK_MAX_TABLES)
    p.add_argument(
        "--desc-coverage-min",
        type=float,
        default=0.5,
        help="rel_guard: minimum fraction of a schema's tables with a description",
    )
    p.add_argument(
        "--embedder",
        default=None,
        help="e.g. text-embedding-3-large; adds the rel_emb variant (costs pennies)",
    )
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--out", type=Path, default=None)
    args = p.parse_args()

    corpus = load_corpus(args.corpus)
    st = SchemaTables(corpus)
    phys = phys_name_to_table_id(corpus)
    questions = _load_questions(args.bird_dir, args.split)
    if args.limit:
        questions = questions[: args.limit]
    rows = _load_rows(args.rows)

    variants = list(VARIANTS)
    if args.embedder:
        from dataclasses import replace as _replace

        from governed_bi.config import load_settings
        from governed_bi.llm.langchain_client import LangChainEmbedder

        base = load_settings().models
        print(f"embedding {sum(len(v) for v in st.by_schema.values())} tables + "
              f"{len(questions)} questions with {args.embedder} ...", flush=True)
        st.add_embeddings(
            LangChainEmbedder.from_config(_replace(base, embedding_model=args.embedder)),
            questions,
        )
        variants.append(EMB_VARIANT)

    n_tables = {s: len(ts) for s, ts in st.by_schema.items()}
    wide = {s for s, n in n_tables.items() if n > args.max_tables}

    stats: Counter[str] = Counter()
    per_schema: dict[str, Counter[str]] = {}
    misroute_examples: list[dict[str, Any]] = []
    no_gold_tables = 0

    for q in questions:
        gold_schema = q["db_id"]
        if gold_schema not in st.by_schema:
            continue
        visible = {t.id for t in st.by_schema[gold_schema]}
        gold_ids = {tid for tid in _sql_table_ids(q["sql_rename"], phys) if tid in visible}
        if not gold_ids:
            # Almost all of these are the frozen-gold questions whose SQL is a literal
            # VALUES constant and reads no table at all — nothing to be visible.
            no_gold_tables += 1
            continue
        stats["n"] += 1
        is_wide = gold_schema in wide
        stats["n_wide"] += int(is_wide)

        row = rows.get(q["question_id"])
        shortlisted = bool(row) and gold_schema in (row.get("shortlisted_schemas") or [])
        misrouted = shortlisted and not bool(row.get("pick_hit"))
        rank1 = misrouted and row.get("gold_schema_rank") == 1
        stats["n_shortlisted"] += int(shortlisted)
        stats["n_misroute"] += int(misrouted)
        stats["misroute_rank1"] += int(rank1)

        seen_any: dict[str, bool] = {}
        for v in variants:
            shown = set(
                st.shown(
                    v,
                    gold_schema,
                    q["question"],
                    args.max_tables,
                    cov_min=args.desc_coverage_min,
                    qid=q["question_id"],
                )
            )
            ok_all, ok_any = gold_ids <= shown, bool(gold_ids & shown)
            seen_any[v] = ok_any
            stats[f"{v}|all"] += int(ok_all)
            stats[f"{v}|any"] += int(ok_any)
            if is_wide:
                stats[f"wide|{v}|all"] += int(ok_all)
                stats[f"wide|{v}|any"] += int(ok_any)
                ps = per_schema.setdefault(gold_schema, Counter())
                ps[f"{v}|all"] += int(ok_all)
            if misrouted:
                stats[f"mis|{v}|all"] += int(ok_all)
                stats[f"mis|{v}|any"] += int(ok_any)
            if rank1:
                stats[f"r1|{v}|all"] += int(ok_all)
        if is_wide:
            per_schema[gold_schema]["n"] += 1

        if misrouted and len(misroute_examples) < 60:
            picked = row.get("schema_pick")
            misroute_examples.append(
                {
                    "qid": q["question_id"],
                    "question": q["question"][:120],
                    "gold": gold_schema,
                    "gold_n_tables": n_tables[gold_schema],
                    "picked": picked,
                    "picked_n_tables": n_tables.get(picked),
                    "picked_shown_whole": n_tables.get(picked, 0) <= args.max_tables,
                    "gold_shown_whole": n_tables[gold_schema] <= args.max_tables,
                    "gold_rank": row.get("gold_schema_rank"),
                    "gold_evidence_alpha": seen_any["alpha"],
                    "gold_evidence_rel": seen_any["rel"],
                }
            )

    def pct(key: str, denom: int) -> str:
        return f"{stats[key]/denom:>11.3f}" if denom else f"{'-':>11}"

    print(f"corpus     : {args.corpus}")
    print(f"questions  : {stats['n']} usable ({no_gold_tables} dropped: gold SQL reads no table)")
    print(f"wide gold  : {stats['n_wide']} questions whose gold schema has > {args.max_tables} tables")
    print(f"wide schemas: {len(wide)} of {len(n_tables)}  {sorted(wide)}")
    print()
    header = f"{'population':<30}" + "".join(f"{v:>13}" for v in variants)
    print("ALL gold tables visible in the picker summary")
    print(header)
    for label, prefix, denom in (
        ("all questions", "", stats["n"]),
        (f"gold schema > {args.max_tables} tables", "wide|", stats["n_wide"]),
        (f"misrouted (n={stats['n_misroute']})", "mis|", stats["n_misroute"]),
        (f"  ...gold rank 1 (n={stats['misroute_rank1']})", "r1|", stats["misroute_rank1"]),
    ):
        row_s = "".join(pct(f"{prefix}{v}|all", denom) for v in variants)
        print(f"{label:<30}{row_s}")
    print()
    print("ANY gold table visible")
    print(header)
    for label, prefix, denom in (
        ("all questions", "", stats["n"]),
        (f"gold schema > {args.max_tables} tables", "wide|", stats["n_wide"]),
        (f"misrouted (n={stats['n_misroute']})", "mis|", stats["n_misroute"]),
    ):
        row_s = "".join(pct(f"{prefix}{v}|any", denom) for v in variants)
        print(f"{label:<30}{row_s}")

    print("\nper wide schema (its own gold questions), ALL-visible rate:")
    print(f"    {'schema':<26}{'tbl':>5}{'q':>5}{'desc%':>7}" + "".join(f"{v:>13}" for v in variants))
    for s in sorted(per_schema, key=lambda s: -per_schema[s]["n"]):
        c = per_schema[s]
        cells = "".join(f"{c[f'{v}|all']/c['n']:>13.3f}" for v in variants)
        print(f"    {s:<26}{n_tables[s]:>5}{c['n']:>5}{st.desc_coverage[s]:>7.2f}{cells}")

    payload = {
        "corpus": str(args.corpus),
        "max_tables": args.max_tables,
        "desc_coverage_min": args.desc_coverage_min,
        "variants": variants,
        "n_questions": stats["n"],
        "n_dropped_no_gold_table": no_gold_tables,
        "wide_schemas": sorted(wide),
        "n_tables_per_schema": n_tables,
        "desc_coverage": st.desc_coverage,
        "stats": dict(stats),
        "per_wide_schema": {s: dict(c) for s, c in per_schema.items()},
        "misroute_examples": misroute_examples,
    }
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"\nwrote {args.out}")
    return 0


def render_example(
    corpus_path: str, schema: str, question: str, *, max_columns: int = 12, variant: str = "rel"
) -> str:
    """Side-by-side of what the picker sees today vs. re-ranked (for the design doc)."""
    corpus = load_corpus(corpus_path)
    st = SchemaTables(corpus)
    ids = st.shown(variant, schema, question, SCHEMA_PICK_MAX_TABLES, cov_min=0.5)
    by_id = {t.id: t for t in st.by_schema[schema]}
    today = _schema_pick_summary(corpus, schema, max_columns=max_columns)
    after = "\n".join(
        [f"schema: {schema}"]
        + [f"  - {by_id[i].physical_name}" for i in ids]
        + [f"  … ({len(st.by_schema[schema]) - len(ids)} more tables)"]
    )
    return f"--- TODAY ---\n{today}\n\n--- {variant.upper()} ---\n{after}"


if __name__ == "__main__":
    raise SystemExit(main())
