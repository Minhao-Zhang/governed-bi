"""Offline retrieval-quality harness: table AND column recall, no LLM.

RVGD quality was previously unmeasured. BIRD gold SQL names the exact tables and
columns each question needs, so we can score retrieval directly and cheaply:
parse the gold SQL, and check whether ``retrieve()`` (and the licensed
join-neighborhood the agent actually gets) surfaced those tables and columns.

**Table level** — two numbers per corpus:

- **recall@k (retrieved)** — did ``retrieval.table_ids`` (the fused top-k plus
  deterministic grounding) contain every gold table?
- **recall@k (licensed)** — did the licensed set the analyst is actually allowed
  to use (retrieval + Steiner join-plan + FK neighborhood) contain them? This is
  the number that bounds achievable execution accuracy: a gold table outside the
  licensed set can never appear in a passing query.

**Column level** (the ``wrong_projection`` / ``wrong_filter_column`` failure mode,
63% + 30% of the ``sql_generate`` error bucket) — three numbers:

1. **corpus column coverage** — does the column gold references exist in the
   corpus at all? A miss here is a *curation* gap, not a retrieval one.
2. **licensed column recall** — after routing + licensing, is the gold column
   inside a table the analyst can see? With no per-table column cap this
   factorises into coverage × table licensing, because a licensed table is
   rendered whole; the cap is what makes it independent.
3. **width curve** — re-run the real per-table column selection
   (``analyst.context._select_columns``, via ``assemble_context``) at a range of
   ``max_table_columns`` budgets and count the gold columns it would drop. This
   is the number that makes ``Settings.analyst_max_table_columns`` decidable:
   it reads directly as "capping at N columns drops gold columns on M questions".

Column resolution is scope-aware (``sqlglot.optimizer.scope``): a qualified ref
resolves through its alias, a bare ref resolves when the scope has exactly one
base table or when exactly one in-scope table carries that column. Anything else
— a genuinely ambiguous bare name, a ref into a CTE/derived output, a ``SELECT *``
— is **counted in its own bucket and reported**, never guessed at and never
silently dropped.

Everything is deterministic, so this gives a clean before/after for a ranking or
grounding change with zero model cost. Gold items come from a run's
``questions.jsonl`` side-car (``--run-dir``, authoritative for that run), from BIRD
via ``bird_loader`` (``--dataset-dir``), or from the committed ``BEER_FACTORY_EVAL``
set (self-contained, runs today).
"""

from __future__ import annotations

import argparse
import logging
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.scope import traverse_scope

from ..analyst.context import assemble_context
from ..analyst.governance import _licensed_table_ids
from ..corpus import load_corpus
from ..corpus.schemas import ReliabilityStatus, TableAsset
from ..graph import build_graph, plan_joins
from ..retrieval import RetrievalIndexCache, retrieve

logger = logging.getLogger("governed_bi.eval")

__all__ = [
    "ColumnRef",
    "GoldColumns",
    "QuestionRecall",
    "RetrievalEvalReport",
    "WidthPoint",
    "DEFAULT_WIDTH_BUDGETS",
    "gold_table_ids",
    "gold_column_refs",
    "evaluate_retrieval",
    "merge_reports",
]

#: ``(table asset id, lower-cased physical column name)``. Lower-cased because
#: BIRD gold SQL and the curated corpus disagree on case constantly
#: (``"T1"."CustomerID"`` vs ``customerid``), and a case-sensitive compare would
#: report a curation gap that is really a spelling convention.
ColumnRef = tuple[str, str]

#: Budgets the width curve is swept over by default. Brackets the router's own
#: ``schema_pick_max_columns`` (12) on both sides and carries on to 64 — past the
#: point where the curve flattens to zero on the 57-schema ladder corpus — so a
#: reader sees the tail rather than a curve that stops while still falling.
DEFAULT_WIDTH_BUDGETS: tuple[int, ...] = (6, 8, 12, 16, 20, 24, 32, 40, 64)


# --------------------------------------------------------------------------- #
# Corpus lookup tables
# --------------------------------------------------------------------------- #


class _TableIndex:
    """Physical table name -> asset id, schema-qualified first.

    A pooled corpus has 57 schemas and repeats table names across them
    (``country`` lives in ``address`` and in ``world``), so a bare-name map alone
    resolves a gold table to whichever schema happened to load last. Qualified
    lookups win; a bare name resolves only when exactly one table corpus-wide
    carries it (same contract as ``Corpus.table_by_name``), otherwise it is
    ambiguous and unresolved.
    """

    __slots__ = ("_qualified", "_bare")

    def __init__(self, corpus) -> None:
        self._qualified: dict[tuple[str, str], str] = {}
        bare: dict[str, list[str]] = {}
        for a in corpus.assets:
            if isinstance(a, TableAsset):
                self._qualified[(a.schema.lower(), a.physical_name.lower())] = a.id
                bare.setdefault(a.physical_name.lower(), []).append(a.id)
        self._bare: dict[str, str | None] = {
            name: (ids[0] if len(ids) == 1 else None) for name, ids in bare.items()
        }

    def resolve(self, table: exp.Table) -> str | None:
        """Asset id for a parsed ``exp.Table``, or ``None`` (unknown/ambiguous)."""
        name = table.name.lower()
        db = (table.db or "").lower()
        if db:
            tid = self._qualified.get((db, name))
            if tid is not None:
                return tid
            # A qualifier that is not a corpus schema (``main.t`` in SQLite gold)
            # should not defeat the lookup; fall through to the bare map.
        return self._bare.get(name)


def _column_index(corpus) -> dict[str, dict[str, object]]:
    """``{table id: {lower-cased column name: Column}}``."""
    return {
        a.id: {c.physical_name.lower(): c for c in a.columns}
        for a in corpus.assets
        if isinstance(a, TableAsset)
    }


# --------------------------------------------------------------------------- #
# Gold tables
# --------------------------------------------------------------------------- #


def _parse_gold(sql: str, dialect: str):
    """Parse gold SQL, logging (not raising) on a syntax error. ``None`` on failure."""
    try:
        tree = sqlglot.parse_one(sql, read=dialect)
    except sqlglot.errors.SqlglotError:
        # An unparseable gold SQL yields an empty gold set, understating recall for
        # this item; surface it rather than silently distorting the aggregate. A
        # non-parse bug (not a SqlglotError) now propagates.
        logger.warning("gold SQL did not parse; recall understated for: %.200s", sql)
        return None
    return tree


def gold_table_ids(corpus, sql: str, *, dialect: str = "sqlite") -> frozenset[str]:
    """The set of table asset ids a gold SQL statement references.

    Parses ``sql`` and maps every base-table name to a ``TableAsset`` id,
    schema-qualified when the gold SQL qualifies it and by unique bare name
    otherwise (case-insensitive). CTE / derived names never match a real table's
    physical name, so they drop out naturally. Returns an empty set if the SQL does
    not parse or references no known table.
    """
    tree = _parse_gold(sql, dialect)
    if tree is None:
        return frozenset()
    index = _TableIndex(corpus)
    ids: set[str] = set()
    for t in tree.find_all(exp.Table):
        tid = index.resolve(t)
        if tid is not None:
            ids.add(tid)
    return frozenset(ids)


# --------------------------------------------------------------------------- #
# Gold columns
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class GoldColumns:
    """Column references one gold SQL makes, resolved against the corpus.

    The four non-``refs`` fields are the honesty budget: every column reference in
    the statement lands in exactly one of the five, so a low ``refs`` count is
    always attributable rather than mysterious.
    """

    #: Resolved ``(table_id, column)`` pairs on tables the corpus knows.
    refs: frozenset[ColumnRef] = frozenset()
    #: ``(physical table name, column)`` for refs whose TABLE is not in the corpus.
    #: A table-level curation/routing gap, not a column one.
    unknown_table: frozenset[tuple[str, str]] = frozenset()
    #: Bare column names no scope could pin to exactly one base table. Reported,
    #: never guessed: attributing one to the wrong table would inflate or deflate
    #: coverage with no way to tell which.
    unresolvable: tuple[str, ...] = ()
    #: Names resolving to a CTE / derived-subquery output rather than a base column.
    #: The base columns feeding that subquery are captured in its own scope, so this
    #: is double-counting avoided, not information lost — except behind a ``SELECT *``.
    derived: tuple[str, ...] = ()
    #: ``SELECT *`` / ``t.*`` projections (never ``COUNT(*)``). Deliberately NOT
    #: expanded to the table's columns: expanding would make corpus coverage a
    #: function of the corpus being scored. Gold column sets for these questions are
    #: a lower bound, which is why the count is reported.
    stars: int = 0


def _star_count(scope) -> int:
    """``SELECT *`` / ``t.*`` projections in one scope, excluding ``COUNT(*)``.

    Only *projections* count. ``COUNT(*)`` is a ``Star`` too but names no column,
    so a naive ``find_all(exp.Star)`` would flag every counting question in BIRD.
    """
    expression = scope.expression
    selects = getattr(expression, "selects", None)
    if not selects:
        return 0
    n = 0
    for projection in selects:
        if isinstance(projection, exp.Star):
            n += 1
        elif isinstance(projection, exp.Column) and isinstance(projection.this, exp.Star):
            n += 1
    return n


def _global_alias_map(scopes, index: _TableIndex) -> dict[str, str | None]:
    """``alias/name -> table id`` across every scope, for correlated references.

    A correlated subquery names an outer alias that is not in its own
    ``scope.sources`` (``... WHERE t2.z = t1.z`` inside a subselect over ``t2``).
    Resolving through a statement-wide map recovers those. An alias bound to two
    different tables in two scopes maps to ``None`` — ambiguous, so unresolvable.
    """
    out: dict[str, str | None] = {}
    for scope in scopes:
        for alias, source in scope.sources.items():
            if not isinstance(source, exp.Table):
                continue
            tid = index.resolve(source)
            key = alias.lower()
            if key in out and out[key] != tid:
                out[key] = None
            else:
                out[key] = tid
    return out


def gold_column_refs(
    corpus,
    sql: str,
    *,
    dialect: str = "sqlite",
    columns_by_table: dict[str, dict[str, object]] | None = None,
) -> GoldColumns:
    """Resolve every column reference in a gold SQL statement to ``(table_id, col)``.

    Scope-aware, via ``sqlglot.optimizer.scope.traverse_scope``:

    - **Qualified** (``T1.households``) — the qualifier is looked up in the scope's
      sources, then (for correlated refs) in the statement-wide alias map. A
      qualifier naming a CTE/derived source counts as ``derived``.
    - **Bare** (``households``) — resolved when the scope has exactly one base
      table. With several, disambiguated by *schema membership*: if exactly one
      in-scope table declares a column of that name, that is the only table SQL
      itself would allow, so it is a resolution and not a guess. Zero or several
      candidates -> ``unresolvable``.

    ``columns_by_table`` (the raw-corpus column index) is what makes bare-name
    disambiguation possible; without it, a bare name in a multi-table scope is
    always unresolvable.

    **Each column node is attributed to the INNERMOST scope containing it.**
    ``sqlglot``'s ``Scope.columns`` for an outer scope also returns the columns of
    subqueries nested in its *expressions* (``WHERE x = (SELECT y FROM other)``) —
    only ``FROM``-position derived tables are pruned. Reading the scopes naively
    therefore attributes the inner query's columns to the outer query's table:
    measured on the 57-schema ladder corpus that alone invented 20 phantom
    "column missing from the corpus" hits, every one of them a filter column of an
    ``IN (SELECT ...)`` blamed on the outer table. ``traverse_scope`` yields
    leaf-first, so claiming each node for the first scope that reports it is
    exactly the innermost-scope rule.
    """
    tree = _parse_gold(sql, dialect)
    if tree is None:
        return GoldColumns()
    index = _TableIndex(corpus)
    cols = columns_by_table if columns_by_table is not None else _column_index(corpus)
    try:
        scopes = list(traverse_scope(tree))
    except Exception:  # pragma: no cover - sqlglot scope builder is best-effort
        logger.warning("gold SQL scope walk failed; columns unmeasured for: %.200s", sql)
        return GoldColumns()

    aliases = _global_alias_map(scopes, index)

    refs: set[ColumnRef] = set()
    unknown: set[tuple[str, str]] = set()
    unresolvable: list[str] = []
    derived: list[str] = []
    stars = 0
    claimed: set[int] = set()

    for scope in scopes:
        stars += _star_count(scope)
        base: dict[str, exp.Table] = {}
        n_derived_sources = 0
        for alias, source in scope.sources.items():
            if isinstance(source, exp.Table):
                base[alias.lower()] = source
            else:
                n_derived_sources += 1

        for column in scope.columns:
            if id(column) in claimed:
                continue  # an inner scope already owns this node
            claimed.add(id(column))
            if isinstance(column.this, exp.Star):
                continue  # `t.*`, already counted by _star_count
            name = column.name
            if not name:
                continue
            qualifier = (column.table or "").lower()

            if qualifier:
                source = scope.sources.get(column.table) or scope.sources.get(qualifier)
                if isinstance(source, exp.Table):
                    _record(refs, unknown, index.resolve(source), source.name, name)
                    continue
                if source is not None:
                    derived.append(f"{qualifier}.{name}")
                    continue
                # Not a source of THIS scope: a correlated outer reference, or a
                # table named without an alias in an enclosing scope.
                if qualifier in aliases:
                    tid = aliases[qualifier]
                    if tid is None:
                        unresolvable.append(f"{qualifier}.{name}")
                    else:
                        _record(refs, unknown, tid, qualifier, name)
                    continue
                unresolvable.append(f"{qualifier}.{name}")
                continue

            # Bare name.
            if n_derived_sources:
                # A derived source may expose a column of this name and the corpus
                # cannot say which columns it exposes, so nothing here is decidable.
                (derived if not base else unresolvable).append(name)
                continue
            if not base:
                # No sources at all (a UNION's outer scope, `SELECT 1`): the base
                # columns live in the inner scopes, already walked.
                derived.append(name)
                continue
            tid, verdict = _resolve_bare(name, scope, index, cols)
            if verdict == "resolved" and tid is not None:
                refs.add((tid, name.lower()))
            elif verdict == "unknown" and len(base) == 1:
                # One candidate table, and it is the corpus that does not know it.
                source = next(iter(base.values()))
                _record(refs, unknown, index.resolve(source), source.name, name)
            else:
                unresolvable.append(name)

    return GoldColumns(
        refs=frozenset(refs),
        unknown_table=frozenset(unknown),
        unresolvable=tuple(unresolvable),
        derived=tuple(derived),
        stars=stars,
    )


def _resolve_bare(
    name: str,
    scope,
    index: _TableIndex,
    cols: dict[str, dict[str, object]],
) -> tuple[str | None, str]:
    """Resolve a bare column name the way SQL itself does: innermost scope outward.

    Returns ``(table_id, verdict)`` with verdict in ``{"resolved", "ambiguous",
    "unknown"}``.

    A bare name binds to the innermost enclosing scope that has a table declaring
    it; only if no scope in the chain does is it an error. BIRD gold leans on this
    constantly — ``SELECT student_id FROM student WHERE course_id IN (...)`` where
    ``course_id`` belongs to the *outer* ``registration``, not to ``student`` —
    so stopping at the innermost scope reads the filter column off the wrong table
    and reports a curation gap that is not there.

    Schema membership (the corpus) is the disambiguator, which is not a guess: SQL
    rejects a bare name two in-scope tables both carry, so at most one candidate
    can be legal. Several candidates in one scope -> ``ambiguous``, reported and
    not attributed.

    The last resort — no scope in the chain declares the name — attributes it to
    the innermost scope's single table anyway, so a genuinely uncurated column is
    scored as the coverage miss it is instead of disappearing into the
    unresolvable bucket.
    """
    chain = []
    s = scope
    while s is not None:
        chain.append(s)
        s = s.parent
    for level in chain:
        candidates = []
        for source in level.sources.values():
            if not isinstance(source, exp.Table):
                continue
            tid = index.resolve(source)
            if tid is not None and name.lower() in cols.get(tid, {}):
                candidates.append(tid)
        if len(candidates) == 1:
            return candidates[0], "resolved"
        if len(candidates) > 1:
            return None, "ambiguous"
    base = [s for s in scope.sources.values() if isinstance(s, exp.Table)]
    if len(base) == 1:
        tid = index.resolve(base[0])
        return tid, "resolved" if tid is not None else "unknown"
    return None, "unknown"


def _record(
    refs: set[ColumnRef],
    unknown: set[tuple[str, str]],
    table_id: str | None,
    physical_name: str,
    column: str,
) -> None:
    """File one resolved reference under ``refs`` or ``unknown_table``."""
    if table_id is None:
        unknown.add((physical_name.lower(), column.lower()))
    else:
        refs.add((table_id, column.lower()))


# --------------------------------------------------------------------------- #
# Per-question record
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class QuestionRecall:
    """Per-question recall record.

    ``gold`` / ``retrieved`` / ``licensed`` are table asset ids. The ``col_*``
    fields are :data:`ColumnRef` sets, partitioned so that::

        col_gold == col_present | col_absent
        col_present == col_excluded | col_suspect | col_serveable

    and ``col_licensed <= col_serveable``. ``col_visible_at`` maps a width budget
    to the subset of ``col_licensed`` that survives the real per-table column
    selection at that budget.
    """

    question: str
    gold: frozenset[str]
    retrieved: frozenset[str]
    licensed: frozenset[str]

    # ── columns ──
    col_gold: frozenset[ColumnRef] = frozenset()
    #: gold columns that exist on their table in the RAW corpus
    col_present: frozenset[ColumnRef] = frozenset()
    #: gold columns whose table is in the corpus but which the corpus never curated
    col_absent: frozenset[ColumnRef] = frozenset()
    #: present, but ``governance.excluded`` — a deliberate refusal, not a miss
    col_excluded: frozenset[ColumnRef] = frozenset()
    #: present, but ``reliability.status == suspect`` — curator says do not use
    col_suspect: frozenset[ColumnRef] = frozenset()
    #: gold columns inside a licensed table and servable (present, not excluded)
    col_licensed: frozenset[ColumnRef] = frozenset()
    #: ``budget -> gold columns still rendered`` (subset of ``col_licensed``)
    col_visible_at: dict[int, frozenset[ColumnRef]] = field(default_factory=dict)
    #: widest gold-bearing table, in columns (0 when no gold table resolved)
    gold_table_width: int = 0
    #: the parse buckets that are not ``col_gold``
    unknown_table_cols: int = 0
    unresolvable_cols: tuple[str, ...] = ()
    derived_cols: int = 0
    star_projections: int = 0

    # ── table-level ──
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

    # ── column-level ──
    @property
    def col_serveable(self) -> frozenset[ColumnRef]:
        """Gold columns the corpus both has and is willing to serve.

        The denominator for licensed column recall: an excluded or suspect column
        is a governance decision, and counting it as a retrieval miss would score
        the governance layer as a bug.
        """
        return self.col_present - self.col_excluded - self.col_suspect

    @property
    def col_hit_corpus(self) -> bool:
        """Every gold column exists in the corpus."""
        return not self.col_absent and bool(self.col_gold)

    @property
    def col_hit_licensed(self) -> bool:
        """Every servable gold column is inside a licensed table."""
        return self.col_serveable <= self.col_licensed


@dataclass(frozen=True)
class WidthPoint:
    """One point of the width curve: what a ``max_table_columns=budget`` cap costs."""

    budget: int
    questions: int  # questions with at least one licensed gold column
    questions_dropping: int  # ... of which at least one gold column is cut
    columns_dropped: int
    columns_total: int

    @property
    def question_rate(self) -> float:
        return self.questions_dropping / self.questions if self.questions else 0.0

    @property
    def column_rate(self) -> float:
        return self.columns_dropped / self.columns_total if self.columns_total else 0.0


# --------------------------------------------------------------------------- #
# Report
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class RetrievalEvalReport:
    top_k: int
    per_question: list[QuestionRecall] = field(default_factory=list)
    skipped: int = 0  # gold items whose SQL named no known table (unparseable / cross-db)
    width_budgets: tuple[int, ...] = ()

    @property
    def n(self) -> int:
        return len(self.per_question)

    def _mean(self, attr: str) -> float:
        if not self.per_question:
            return 0.0
        return sum(getattr(q, attr) for q in self.per_question) / len(self.per_question)

    # ── table level ──
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

    # ── column level ──
    def _col_total(self, attr: str) -> int:
        return sum(len(getattr(q, attr)) for q in self.per_question)

    @property
    def n_col_gold(self) -> int:
        return self._col_total("col_gold")

    @property
    def n_col_present(self) -> int:
        return self._col_total("col_present")

    @property
    def n_col_absent(self) -> int:
        return self._col_total("col_absent")

    @property
    def n_col_excluded(self) -> int:
        return self._col_total("col_excluded")

    @property
    def n_col_suspect(self) -> int:
        return self._col_total("col_suspect")

    @property
    def n_col_serveable(self) -> int:
        return self._col_total("col_serveable")

    @property
    def n_col_licensed(self) -> int:
        return self._col_total("col_licensed")

    @property
    def corpus_column_coverage(self) -> float:
        """Fraction of resolved gold columns the corpus contains at all."""
        return self.n_col_present / self.n_col_gold if self.n_col_gold else 0.0

    @property
    def licensed_column_recall(self) -> float:
        """Fraction of *servable* gold columns inside a licensed table."""
        return self.n_col_licensed / self.n_col_serveable if self.n_col_serveable else 0.0

    @property
    def col_hit_rate_corpus(self) -> float:
        """Questions where EVERY gold column exists in the corpus."""
        graded = [q for q in self.per_question if q.col_gold]
        if not graded:
            return 0.0
        return sum(q.col_hit_corpus for q in graded) / len(graded)

    @property
    def col_hit_rate_licensed(self) -> float:
        """Questions where EVERY servable gold column is licensed."""
        graded = [q for q in self.per_question if q.col_serveable]
        if not graded:
            return 0.0
        return sum(q.col_hit_licensed for q in graded) / len(graded)

    @property
    def n_unresolvable(self) -> int:
        return sum(len(q.unresolvable_cols) for q in self.per_question)

    @property
    def n_unknown_table_cols(self) -> int:
        return sum(q.unknown_table_cols for q in self.per_question)

    @property
    def n_derived(self) -> int:
        return sum(q.derived_cols for q in self.per_question)

    @property
    def n_star(self) -> int:
        return sum(q.star_projections for q in self.per_question)

    @property
    def n_questions_with_star(self) -> int:
        return sum(1 for q in self.per_question if q.star_projections)

    def width_curve(self) -> list[WidthPoint]:
        """The ``max_table_columns`` sweep: what each budget would cost in gold columns."""
        graded = [q for q in self.per_question if q.col_licensed]
        points: list[WidthPoint] = []
        for budget in self.width_budgets:
            dropped_questions = 0
            dropped_cols = 0
            total_cols = 0
            for q in graded:
                visible = q.col_visible_at.get(budget, q.col_licensed)
                missing = q.col_licensed - visible
                total_cols += len(q.col_licensed)
                dropped_cols += len(missing)
                if missing:
                    dropped_questions += 1
            points.append(
                WidthPoint(
                    budget=budget,
                    questions=len(graded),
                    questions_dropping=dropped_questions,
                    columns_dropped=dropped_cols,
                    columns_total=total_cols,
                )
            )
        return points

    def width_histogram(self) -> list[tuple[str, int]]:
        """Questions bucketed by the width of their widest gold-bearing table."""
        edges = ((0, 9), (10, 14), (15, 19), (20, 29), (30, 39), (40, 59), (60, 10_000))
        counts = Counter()
        for q in self.per_question:
            if not q.gold_table_width:
                continue
            for lo, hi in edges:
                if lo <= q.gold_table_width <= hi:
                    counts[(lo, hi)] += 1
                    break
        return [
            (f"{lo}-{hi}" if hi < 10_000 else f"{lo}+", counts[(lo, hi)]) for lo, hi in edges
        ]

    def format(self, *, show_misses: bool = True, max_misses: int = 20) -> str:
        lines = [
            f"retrieval recall @ top_k={self.top_k}  (n={self.n}, skipped={self.skipped})",
            "",
            "TABLE level",
            f"  full-hit rate   retrieved={self.hit_rate_retrieved:.3f}   "
            f"licensed={self.hit_rate_licensed:.3f}",
            f"  mean recall     retrieved={self.mean_recall_retrieved:.3f}   "
            f"licensed={self.mean_recall_licensed:.3f}",
            "",
            "COLUMN level",
            f"  resolved gold column refs      {self.n_col_gold}",
            f"  1. corpus coverage             {self.corpus_column_coverage:.4f}   "
            f"({self.n_col_present}/{self.n_col_gold} present, "
            f"{self.n_col_absent} never curated)",
            f"     per-question all-present    {self.col_hit_rate_corpus:.4f}",
            f"  2. licensed column recall      {self.licensed_column_recall:.4f}   "
            f"({self.n_col_licensed}/{self.n_col_serveable} servable refs licensed)",
            f"     per-question all-licensed   {self.col_hit_rate_licensed:.4f}",
            "  governance (excluded from the denominator, not misses):",
            f"     governance.excluded         {self.n_col_excluded}",
            f"     reliability=suspect         {self.n_col_suspect}",
            "  unattributed references (counted, never guessed):",
            f"     unresolvable bare names     {self.n_unresolvable}",
            f"     table not in corpus         {self.n_unknown_table_cols}",
            f"     CTE / derived output refs   {self.n_derived}",
            f"     SELECT * projections        {self.n_star} "
            f"(on {self.n_questions_with_star} question(s); their gold column sets "
            f"are lower bounds)",
        ]
        curve = self.width_curve()
        if curve:
            lines += [
                "",
                "3. WIDTH CURVE - cost of Settings.analyst_max_table_columns",
                f"   (over the {curve[0].questions} question(s) with >=1 licensed gold column)",
                "   budget   questions dropping a gold column      gold columns dropped",
            ]
            for p in curve:
                lines.append(
                    f"   {p.budget:>6}   {p.questions_dropping:>6} / {p.questions:<6} "
                    f"({p.question_rate:6.1%})              "
                    f"{p.columns_dropped:>5} / {p.columns_total:<6} ({p.column_rate:5.1%})"
                )
        histogram = self.width_histogram()
        if any(count for _label, count in histogram):
            lines += ["", "   widest gold-bearing table (columns) -> questions"]
            for label, count in histogram:
                lines.append(f"     {label:>8}  {count:>5}")
        if show_misses:
            misses = [q for q in self.per_question if not q.hit_licensed]
            if misses:
                lines.append("")
                lines.append(
                    f"  {len(misses)} question(s) miss a gold table even after licensing:"
                )
                for q in misses[:max_misses]:
                    lines.append(f"    - {q.question!r} missing {sorted(q.missing_licensed)}")
                if len(misses) > max_misses:
                    lines.append(f"    ... and {len(misses) - max_misses} more")
        return "\n".join(lines)


def merge_reports(reports: list[RetrievalEvalReport]) -> RetrievalEvalReport:
    """Pool per-schema reports into one. ``top_k``/``width_budgets`` come from the first."""
    if not reports:
        return RetrievalEvalReport(top_k=0)
    per_question: list[QuestionRecall] = []
    skipped = 0
    for r in reports:
        per_question.extend(r.per_question)
        skipped += r.skipped
    return RetrievalEvalReport(
        top_k=reports[0].top_k,
        per_question=per_question,
        skipped=skipped,
        width_budgets=reports[0].width_budgets,
    )


# --------------------------------------------------------------------------- #
# Driver
# --------------------------------------------------------------------------- #


def _visible_refs(context) -> set[ColumnRef]:
    """``(table_id, column)`` pairs an assembled prompt context actually renders."""
    return {
        (tv.id, col.physical_name.lower()) for tv in context.tables for col in tv.columns
    }


def evaluate_retrieval(
    corpus,
    gold_items,
    *,
    top_k: int = 8,
    embedder=None,
    dialect: str = "sqlite",
    raw_corpus=None,
    width_budgets: tuple[int, ...] | list[int] = (),
) -> RetrievalEvalReport:
    """Score ``retrieve()`` against gold SQL over ``gold_items``.

    Items whose gold SQL names no table this corpus knows are skipped and counted
    in ``RetrievalEvalReport.skipped`` (a cross-schema gold, or one whose SQL did
    not parse in ``dialect``); the warning naming the SQL is logged by
    :func:`_parse_gold`.

    ``gold_items`` is any iterable of objects with ``.question`` and ``.sql``
    (``EvalItem`` from either dataset module works). ``corpus`` should be the
    ``for_analyst()`` view — the same one serve retrieves over. Items whose gold
    SQL names no table known to the corpus are skipped (cross-db / unparseable).

    ``raw_corpus`` is the pre-``for_analyst()`` corpus. It is what separates
    "the curator never wrote this column" from "governance deliberately removed
    it": ``for_analyst()`` drops ``governance.excluded`` columns outright, so
    without the raw view an excluded gold column is indistinguishable from an
    uncurated one and would be scored as a curation gap. Defaults to ``corpus``,
    which makes that distinction unavailable (and is fine for a corpus that
    excludes nothing).

    ``width_budgets`` sweeps ``analyst_max_table_columns``: for each budget the
    real prompt context is re-assembled and the surviving gold columns recorded.
    Empty (the default) skips the sweep — it costs one ``assemble_context`` per
    question per budget.
    """
    raw = raw_corpus if raw_corpus is not None else corpus
    graph = build_graph(corpus)
    cache = RetrievalIndexCache()
    raw_columns = _column_index(raw)
    analyst_columns = _column_index(corpus)
    table_widths = {
        a.id: len(a.columns) for a in corpus.assets if isinstance(a, TableAsset)
    }
    budgets = tuple(int(b) for b in width_budgets if int(b) > 0)

    records: list[QuestionRecall] = []
    skipped = 0
    for item in gold_items:
        gold = gold_table_ids(corpus, item.sql, dialect=dialect)
        if not gold:
            skipped += 1
            continue
        gold_cols = gold_column_refs(
            corpus, item.sql, dialect=dialect, columns_by_table=raw_columns
        )
        result = retrieve(
            corpus, item.question, top_k=top_k, embedder=embedder, index_cache=cache
        )
        retrieved = frozenset(result.table_ids)
        try:
            join_ids = plan_joins(graph, set(result.table_ids)).join_ids
        except ValueError:
            join_ids = []
        licensed = frozenset(_licensed_table_ids(corpus, graph, result, join_ids))

        present: set[ColumnRef] = set()
        absent: set[ColumnRef] = set()
        excluded: set[ColumnRef] = set()
        suspect: set[ColumnRef] = set()
        for ref in gold_cols.refs:
            table_id, name = ref
            column = raw_columns.get(table_id, {}).get(name)
            if column is None:
                absent.add(ref)
                continue
            present.add(ref)
            if column.governance.excluded:
                excluded.add(ref)
            if column.reliability.status is ReliabilityStatus.suspect:
                suspect.add(ref)
        serveable = present - excluded - suspect
        # A licensed table renders whole (no cap), so a servable gold column is
        # licensed exactly when its table is. The width sweep below is what makes
        # this stop being a restatement of table recall.
        licensed_cols = frozenset(
            ref for ref in serveable if ref[0] in licensed and ref[1] in analyst_columns.get(ref[0], {})
        )

        visible_at: dict[int, frozenset[ColumnRef]] = {}
        if budgets and licensed_cols:
            for budget in budgets:
                context = assemble_context(
                    corpus,
                    result,
                    licensed_table_ids=licensed,
                    max_table_columns=budget,
                )
                visible = _visible_refs(context)
                visible_at[budget] = frozenset(ref for ref in licensed_cols if ref in visible)

        records.append(
            QuestionRecall(
                question=item.question,
                gold=gold,
                retrieved=retrieved,
                licensed=licensed,
                col_gold=gold_cols.refs,
                col_present=frozenset(present),
                col_absent=frozenset(absent),
                col_excluded=frozenset(excluded),
                col_suspect=frozenset(suspect),
                col_licensed=licensed_cols,
                col_visible_at=visible_at,
                gold_table_width=max((table_widths.get(t, 0) for t in gold), default=0),
                unknown_table_cols=len(gold_cols.unknown_table),
                unresolvable_cols=gold_cols.unresolvable,
                derived_cols=len(gold_cols.derived),
                star_projections=gold_cols.stars,
            )
        )
    return RetrievalEvalReport(
        top_k=top_k, per_question=records, skipped=skipped, width_budgets=budgets
    )


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _items_from_run_dir(run_dir: str) -> dict[str, list]:
    """``{db_id: [EvalItem]}`` from a run's ``questions.jsonl`` side-car.

    The side-car is the *frozen* pool a run actually scored, gold SQL in the
    identifiers that run used — so a retrospective measurement of that run needs
    no BIRD checkout and cannot drift from it.
    """
    from .analysis import load_questions_sidecar
    from .dataset import EvalItem

    out: dict[str, list] = {}
    for qid, row in load_questions_sidecar(run_dir).items():
        sql = row.get("gold_sql") or row.get("sql_rename") or ""
        db_id = str(row.get("db_id") or "")
        question = row.get("question") or ""
        if not (sql and db_id and question):
            continue
        out.setdefault(db_id, []).append(
            EvalItem(
                question=question,
                sql=str(sql),
                question_id=qid,
                difficulty=row.get("difficulty"),
                evidence=row.get("evidence"),
            )
        )
    return out


def _load_gold_items(args, schema: str, run_items: dict[str, list] | None):
    """Gold items for one schema, from the run side-car, BIRD, or the committed set."""
    if run_items is not None:
        return run_items.get(schema, [])
    if args.dataset_dir:
        from .bird_loader import load_bird_items

        return load_bird_items(
            args.dataset_dir, schema, split=args.split, gold_sql_field=args.gold_sql_field
        )
    from .dataset import BEER_FACTORY_EVAL

    return BEER_FACTORY_EVAL


def _schemas(args, root: Path) -> list[str]:
    if not args.all_schemas:
        return [args.schema]
    return sorted(p.name for p in root.iterdir() if p.is_dir() and p.name != "_generated")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m governed_bi.eval.retrieval_eval",
        description="Offline table + column recall@k over gold SQL (no LLM).",
    )
    parser.add_argument("--corpus-root", default="corpus", help="corpus root (default: corpus)")
    parser.add_argument("--schema", default="beer_factory", help="db_id / schema subtree to load")
    parser.add_argument(
        "--all-schemas",
        action="store_true",
        help="score every schema subtree under --corpus-root independently and pool "
        "the results (isolates column recall from cross-schema routing)",
    )
    parser.add_argument(
        "--run-dir",
        default=None,
        help="an eval run directory; gold comes from its questions.jsonl side-car "
        "(the exact frozen pool and gold SQL that run scored)",
    )
    parser.add_argument(
        "--dataset-dir",
        default=None,
        help="BIRD-Obfuscation checkout with <split>_final.jsonl; omit to use "
        "--run-dir or the committed BEER_FACTORY_EVAL set",
    )
    parser.add_argument("--split", default="test")
    parser.add_argument("--gold-sql-field", default="sql_sqlite")
    parser.add_argument(
        "--dialect",
        default="sqlite",
        help="gold SQL dialect for sqlglot (use 'postgres' for sql_rename gold)",
    )
    parser.add_argument("--top-k", type=int, default=8)
    parser.add_argument(
        "--embedder",
        choices=("none", "hashing"),
        default="none",
        help="add the vector channel (hashing = free/offline); default lexical-only",
    )
    parser.add_argument(
        "--width-budgets",
        default=",".join(str(b) for b in DEFAULT_WIDTH_BUDGETS),
        help="comma-separated max_table_columns budgets for the width curve; "
        "empty string disables the sweep",
    )
    parser.add_argument(
        "--per-schema",
        action="store_true",
        help="also print a one-line summary per schema",
    )
    args = parser.parse_args(argv)

    root = Path(args.corpus_root)
    budgets = tuple(
        int(b) for b in (args.width_budgets or "").replace(" ", "").split(",") if b
    )
    embedder = None
    if args.embedder == "hashing":
        from ..llm import HashingEmbedder

        embedder = HashingEmbedder()
    run_items = _items_from_run_dir(args.run_dir) if args.run_dir else None

    reports: list[RetrievalEvalReport] = []
    for schema in _schemas(args, root):
        gold_items = _load_gold_items(args, schema, run_items)
        if not gold_items:
            continue
        raw = load_corpus(root, schema=schema)
        report = evaluate_retrieval(
            raw.for_analyst(),
            gold_items,
            top_k=args.top_k,
            embedder=embedder,
            dialect=args.dialect,
            raw_corpus=raw,
            width_budgets=budgets,
        )
        reports.append(report)
        if args.per_schema:
            print(
                f"[{schema:<28}] n={report.n:<4} "
                f"tbl_licensed={report.hit_rate_licensed:.3f} "
                f"col_coverage={report.corpus_column_coverage:.3f} "
                f"col_licensed={report.licensed_column_recall:.3f}"
            )
    pooled = merge_reports(reports)
    print(pooled.format(show_misses=not args.all_schemas))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
