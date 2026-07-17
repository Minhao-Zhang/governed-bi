"""Offline retrieval-quality harness: table recall@k, no LLM.

RVGD quality was previously unmeasured. BIRD gold SQL names the exact tables and
columns each question needs, so we can score retrieval directly and cheaply:
parse the gold SQL, and check whether ``retrieve()`` (and the licensed
join-neighborhood the agent actually gets) surfaced those tables.

Two numbers per corpus:

- **recall@k (retrieved)** — did ``retrieval.table_ids`` (the fused top-k plus
  deterministic grounding) contain every gold table?
- **recall@k (licensed)** — did the licensed set the analyst is actually allowed
  to use (retrieval + Steiner join-plan + FK neighborhood) contain them? This is
  the number that bounds achievable execution accuracy: a gold table outside the
  licensed set can never appear in a passing query.

Everything is deterministic, so this gives a clean before/after for a ranking or
grounding change with zero model cost. Gold items come from BIRD via
``bird_loader`` (authoritative) or from the committed ``BEER_FACTORY_EVAL`` set
(self-contained, runs today).
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from pathlib import Path

import sqlglot
from sqlglot import exp

from ..corpus import load_corpus
from ..corpus.schemas import TableAsset
from ..graph import build_graph, plan_joins
from ..analyst.governance import _licensed_table_ids
from ..retrieval import retrieve

__all__ = [
    "QuestionRecall",
    "RetrievalEvalReport",
    "gold_table_ids",
    "evaluate_retrieval",
]


def _physical_to_table_id(corpus) -> dict[str, str]:
    """Map each table's physical name (lower-cased) to its asset id."""
    out: dict[str, str] = {}
    for a in corpus.assets:
        if isinstance(a, TableAsset):
            out[a.physical_name.lower()] = a.id
    return out


def gold_table_ids(corpus, sql: str, *, dialect: str = "sqlite") -> frozenset[str]:
    """The set of table asset ids a gold SQL statement references.

    Parses ``sql`` and maps every base-table name to a ``TableAsset`` id by
    physical name (case-insensitive). CTE / derived names never match a real
    table's physical name, so they drop out naturally. Returns an empty set if
    the SQL does not parse or references no known table.
    """
    try:
        tree = sqlglot.parse_one(sql, read=dialect)
    except Exception:
        return frozenset()
    if tree is None:
        return frozenset()
    phys = _physical_to_table_id(corpus)
    ids: set[str] = set()
    for t in tree.find_all(exp.Table):
        tid = phys.get(t.name.lower())
        if tid is not None:
            ids.add(tid)
    return frozenset(ids)


@dataclass(frozen=True)
class QuestionRecall:
    """Per-question recall record (all ids are table asset ids)."""

    question: str
    gold: frozenset[str]
    retrieved: frozenset[str]
    licensed: frozenset[str]

    @property
    def missing_retrieved(self) -> frozenset[str]:
        return self.gold - self.retrieved

    @property
    def missing_licensed(self) -> frozenset[str]:
        return self.gold - self.licensed

    @property
    def hit_retrieved(self) -> bool:
        """Every gold table was surfaced by retrieval."""
        return self.gold <= self.retrieved

    @property
    def hit_licensed(self) -> bool:
        """Every gold table is inside the licensed scope."""
        return self.gold <= self.licensed

    @property
    def frac_retrieved(self) -> float:
        return len(self.gold & self.retrieved) / len(self.gold) if self.gold else 1.0

    @property
    def frac_licensed(self) -> float:
        return len(self.gold & self.licensed) / len(self.gold) if self.gold else 1.0


@dataclass(frozen=True)
class RetrievalEvalReport:
    top_k: int
    per_question: list[QuestionRecall] = field(default_factory=list)
    skipped: int = 0  # gold items whose SQL named no known table (unparseable / cross-db)

    @property
    def n(self) -> int:
        return len(self.per_question)

    def _mean(self, attr: str) -> float:
        if not self.per_question:
            return 0.0
        return sum(getattr(q, attr) for q in self.per_question) / len(self.per_question)

    @property
    def hit_rate_retrieved(self) -> float:
        """Fraction of questions where ALL gold tables were retrieved (recall@k)."""
        return self._mean("hit_retrieved")

    @property
    def hit_rate_licensed(self) -> float:
        return self._mean("hit_licensed")

    @property
    def mean_recall_retrieved(self) -> float:
        """Mean per-question fraction of gold tables retrieved."""
        return self._mean("frac_retrieved")

    @property
    def mean_recall_licensed(self) -> float:
        return self._mean("frac_licensed")

    def format(self, *, show_misses: bool = True) -> str:
        lines = [
            f"retrieval recall @ top_k={self.top_k}  (n={self.n}, skipped={self.skipped})",
            f"  full-hit rate   retrieved={self.hit_rate_retrieved:.3f}   "
            f"licensed={self.hit_rate_licensed:.3f}",
            f"  mean recall     retrieved={self.mean_recall_retrieved:.3f}   "
            f"licensed={self.mean_recall_licensed:.3f}",
        ]
        if show_misses:
            misses = [q for q in self.per_question if not q.hit_licensed]
            if misses:
                lines.append(f"  {len(misses)} question(s) miss a gold table even after licensing:")
                for q in misses:
                    lines.append(f"    - {q.question!r} missing {sorted(q.missing_licensed)}")
        return "\n".join(lines)


def evaluate_retrieval(
    corpus,
    gold_items,
    *,
    top_k: int = 8,
    embedder=None,
    dialect: str = "sqlite",
) -> RetrievalEvalReport:
    """Score ``retrieve()`` against gold SQL over ``gold_items``.

    ``gold_items`` is any iterable of objects with ``.question`` and ``.sql``
    (``EvalItem`` from either dataset module works). ``corpus`` should be the
    ``for_analyst()`` view — the same one serve retrieves over. Items whose gold
    SQL names no table known to the corpus are skipped (cross-db / unparseable).
    """
    graph = build_graph(corpus)
    records: list[QuestionRecall] = []
    skipped = 0
    for item in gold_items:
        gold = gold_table_ids(corpus, item.sql, dialect=dialect)
        if not gold:
            skipped += 1
            continue
        result = retrieve(corpus, item.question, top_k=top_k, embedder=embedder)
        retrieved = frozenset(result.table_ids)
        try:
            join_ids = plan_joins(graph, set(result.table_ids)).join_ids
        except ValueError:
            join_ids = []
        licensed = frozenset(_licensed_table_ids(corpus, graph, result, join_ids))
        records.append(
            QuestionRecall(question=item.question, gold=gold, retrieved=retrieved, licensed=licensed)
        )
    return RetrievalEvalReport(top_k=top_k, per_question=records, skipped=skipped)


def _load_gold_items(args):
    """Gold items from BIRD (``--dataset-dir``) or the committed beer_factory set."""
    if args.dataset_dir:
        from .bird_loader import load_bird_items

        return load_bird_items(
            args.dataset_dir, args.schema, split=args.split, gold_sql_field=args.gold_sql_field
        )
    from .dataset import BEER_FACTORY_EVAL

    return BEER_FACTORY_EVAL


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m governed_bi.eval.retrieval_eval",
        description="Offline retrieval recall@k over gold SQL (no LLM).",
    )
    parser.add_argument("--corpus-root", default="corpus", help="corpus root (default: corpus)")
    parser.add_argument("--schema", default="beer_factory", help="db_id / schema subtree to load")
    parser.add_argument(
        "--dataset-dir",
        default=None,
        help="BIRD-Obfuscation checkout with <split>_final.jsonl; omit to use the "
        "committed BEER_FACTORY_EVAL set",
    )
    parser.add_argument("--split", default="test")
    parser.add_argument("--gold-sql-field", default="sql_sqlite")
    parser.add_argument("--top-k", type=int, default=8)
    parser.add_argument(
        "--embedder",
        choices=("none", "hashing"),
        default="none",
        help="add the vector channel (hashing = free/offline); default lexical-only",
    )
    args = parser.parse_args(argv)

    corpus = load_corpus(Path(args.corpus_root), schema=args.schema).for_analyst()
    gold_items = _load_gold_items(args)
    embedder = None
    if args.embedder == "hashing":
        from ..llm import HashingEmbedder

        embedder = HashingEmbedder()
    report = evaluate_retrieval(corpus, gold_items, top_k=args.top_k, embedder=embedder)
    print(report.format())
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
