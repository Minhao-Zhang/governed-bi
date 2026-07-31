"""Counterfactual rungs: what one stage's failures actually cost.

The error taxonomy says *where* wrong answers come from. It cannot say what fixing
a stage would buy, and the arithmetic that looks like it can is wrong. On the last
benchmark 61% of wrong answers were wrong along more than one dimension at once
(RETIRED figure; see docs/measurement.md), so
"203 questions have the wrong table" is not 203 recoverable questions — fix the
tables and most of those queries are still wrong about something else. Adding
per-class counts to get a headroom estimate over-counts every query in more than
one class, which is how one report arrived at "+46 points available" and then
revised it to "3–5" with nothing in between to justify either.

An oracle rung answers the question directly instead of estimating it. Hand one
stage the gold answer, leave every other stage alone, re-serve, and measure. The EX
difference *is* that stage's headroom, with the interactions already priced in
because the rest of the pipeline still has to do its job.

Four rungs, in increasing cost and decreasing realism:

``oracle_sql``
    Skip the model; submit gold SQL to the grader. Costs nothing and answers a
    question nobody had been asking: what does the *grader* score gold at? Anything
    below 1.0 is a grading gap — a frozen constant, a stale hash, a normalisation
    quirk — and it is the true ceiling every other number should be read against.
    Run this first. A ceiling of 0.81 makes an EX of 0.44 a very different result
    from what it looks like against an assumed 1.0.

``oracle_schema``
    Pin the corpus to the gold schema, so routing cannot miss. EX lift = everything
    schema routing costs, including the part that leaks into SQL generation when a
    model writes plausible SQL against the wrong tables.

``oracle_tables``
    Restrict the corpus to the tables gold actually uses. EX lift over
    ``oracle_schema`` bounds the headroom from the model never touching a non-gold
    table — an upper bound on the taxonomy's ``table_select`` class, and NOT an
    attribution to table selection. Narrowing the corpus changes several things at
    once (context size, licensed scope and therefore guardrail enforcement, join
    edges offered, decoy caveats), and on the corpus this was first measured on,
    ``oracle_schema`` had already licensed every gold table on 103/103 questions —
    so there was no retrieval-level selection error left for the rung to remove.
    See ``docs/oracle-ladder.md`` before quoting the delta.

``oracle_tables_padded``
    The control. Same gold tables, padded back up to ``oracle_schema``'s table
    count with non-gold tables from the same schema (:func:`pad_tables`), so only
    which tables are present varies and roughly not how many. Scoring like
    ``oracle_tables`` means the effect is table identity; scoring like
    ``oracle_schema`` means it was prompt size.

All four are ordinary arms — same serve path, same guardrails, same grader. The
only difference is a narrowed corpus, which is also why they are honest: nothing is
mocked, the model still has to write the query.

These are diagnostics and can never be reported as system performance. They are
test-aware by construction: the corpus is built from the answer. The ladder is
opt-in for that reason, and every rung stamps ``oracle_rung`` on its rows so a
number from one cannot be mistaken for a product metric later.
"""

from __future__ import annotations

import hashlib
import logging
from collections import OrderedDict
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any, Iterable, Mapping

from .sql_diff import extract_features

if TYPE_CHECKING:
    from ..config import Settings
    from ..corpus import Corpus
    from ..gateway import Gateway, Identity

logger = logging.getLogger("governed_bi.eval")

__all__ = [
    "OracleRung",
    "GoldIndex",
    "gold_tables_for",
    "restrict_corpus",
    "oracle_solver",
]


class OracleRung(str, Enum):
    """Which stage is handed its gold answer."""

    sql = "oracle_sql"
    schema = "oracle_schema"
    tables = "oracle_tables"
    #: The control for ``tables``. Same gold tables, padded back up to
    #: ``oracle_schema``'s table count with non-gold tables from the same schema,
    #: so the prompt is about as large as ``oracle_schema``'s and only the
    #: *identity* of the tables differs. See :func:`pad_tables`.
    tables_padded = "oracle_tables_padded"


@dataclass
class GoldIndex:
    """Gold lookup keyed by question text, because that is all a solver receives.

    The :class:`~governed_bi.eval.arms.MetaSolver` protocol is ``solve(question)``
    — no id — so an oracle has to find its gold by text. That is safe only if the
    mapping is unambiguous, and on this benchmark it nearly isn't: five questions
    appear in both the train and test splits with byte-identical text. Those five
    share gold SQL, so they are harmless, but a future collision that does *not*
    would silently hand one question another's answer and inflate the rung it was
    meant to measure. So the index is built strictly and raises on a real
    ambiguity rather than picking one.
    """

    by_text: dict[str, Mapping[str, Any]]

    @classmethod
    def build(cls, items: Iterable[Mapping[str, Any]]) -> "GoldIndex":
        by_text: dict[str, Mapping[str, Any]] = {}
        for item in items:
            question = str(item.get("question") or "").strip()
            if not question:
                continue
            existing = by_text.get(question)
            if existing is not None:
                if _gold_sql(existing) != _gold_sql(item):
                    raise ValueError(
                        "two questions share the text "
                        f"{question[:70]!r} but have different gold SQL "
                        f"({existing.get('question_id')} vs {item.get('question_id')}). "
                        "An oracle keyed on question text would hand one of them the "
                        "other's answer; give the oracle ids instead of text."
                    )
                continue
            by_text[question] = item
        return cls(by_text=by_text)

    def get(self, question: str) -> Mapping[str, Any] | None:
        return self.by_text.get(question.strip())


def _gold_sql(item: Mapping[str, Any]) -> str | None:
    for key in ("sql_rename", "sql", "gold_sql", "sql_base"):
        value = item.get(key)
        if value:
            return str(value)
    return None


def gold_tables_for(gold_sql: str | None, *, dialect: str = "postgres") -> frozenset[str]:
    """Physical table names the gold statement reads. Empty when unparseable."""
    if not gold_sql:
        return frozenset()
    return extract_features(gold_sql, dialect=dialect).tables


def pad_tables(
    corpus: "Corpus",
    *,
    schema: str,
    gold: frozenset[str],
    target: int,
    seed_key: str,
) -> frozenset[str]:
    """``gold`` plus enough non-gold tables from ``schema`` to reach ``target``.

    The control for ``oracle_tables``. Narrowing a corpus to the gold tables does
    two things at once: it fixes which tables the model can reach, and it halves
    the prompt. Both plausibly help, and the delta cannot say which did. Padding
    holds the second roughly constant so the first is what varies.

    Selection is deterministic without a random source: ``Math.random``-style
    nondeterminism would make a rung unreproducible across resumes, and the run
    directory is the only record. Non-gold tables are ranked by a stable hash of
    ``(seed_key, physical_name)`` and taken in that order, so the same question
    pads with the same distractors on every run while different questions get
    different ones.
    """
    from ..corpus.schemas import TableAsset

    available = sorted(
        a.physical_name.lower()
        for a in corpus.assets
        if isinstance(a, TableAsset)
        and (a.schema or "").lower() == schema.lower()
        and a.physical_name.lower() not in gold
    )
    need = max(0, target - len(gold))
    if need == 0 or not available:
        return frozenset(gold)
    ranked = sorted(
        available,
        key=lambda name: hashlib.sha256(
            f"{seed_key}\x00{name}".encode("utf-8")
        ).hexdigest(),
    )
    return frozenset(gold | set(ranked[:need]))


def restrict_corpus(
    corpus: "Corpus",
    *,
    schema: str | None = None,
    tables: frozenset[str] | None = None,
) -> "Corpus":
    """A corpus narrowed to one schema and optionally to a set of tables.

    Dependent assets follow their tables: a join whose endpoints are not both kept
    would describe an edge to nowhere, and a metric over a dropped table would
    advertise a column the model cannot reach. Terms, notes and negative examples
    are kept whole — they are scoped by their own machinery, and dropping them here
    would change more than the one variable the rung is supposed to isolate.

    Returns a corpus with an empty table set if ``tables`` matches nothing; the
    caller decides whether that is a skip or a failure, because silently falling
    back to the full corpus would make the rung measure nothing while looking fine.
    """
    from ..corpus import Corpus
    from ..corpus.schemas import (
        FewShotAsset,
        JoinAsset,
        MetricAsset,
        TableAsset,
    )

    wanted = {t.lower() for t in tables} if tables is not None else None

    kept_table_ids: set[str] = set()
    kept: list[Any] = []
    for asset in corpus.assets:
        if isinstance(asset, TableAsset):
            if schema is not None and (asset.schema or "").lower() != schema.lower():
                continue
            if wanted is not None and asset.physical_name.lower() not in wanted:
                continue
            kept_table_ids.add(asset.id)
            kept.append(asset)

    for asset in corpus.assets:
        if isinstance(asset, TableAsset):
            continue
        if isinstance(asset, JoinAsset):
            if asset.left_table in kept_table_ids and asset.right_table in kept_table_ids:
                kept.append(asset)
            continue
        if isinstance(asset, MetricAsset):
            if asset.base_table in kept_table_ids:
                kept.append(asset)
            continue
        if isinstance(asset, FewShotAsset):
            if schema is not None and (asset.schema or "").lower() != schema.lower():
                continue
            # Filtered by table too, not only by schema. An exemplar whose SQL
            # reads a table the narrowed corpus dropped is gold SQL the turn is
            # then blocked from imitating: the prompt says "use ONLY these
            # identifiers" and the example uses others. Measured at 73.7% of  # RETIRED
            # exemplars under ``oracle_tables`` before this filter existed, which
            # depresses the rung for a reason that has nothing to do with the
            # stage it is meant to isolate.
            if wanted is not None and not _few_shot_fits(asset, wanted):
                continue
            kept.append(asset)
            continue
        kept.append(asset)

    return Corpus(assets=kept)


def _few_shot_fits(asset: Any, wanted: frozenset[str]) -> bool:
    """True when every table the exemplar's SQL reads survived the narrowing.

    An exemplar whose SQL will not parse is kept: dropping it would silently
    shrink the prompt for a parser gap rather than for the intervention, and the
    exemplar may well be fine.
    """
    from .sql_diff import extract_features

    features = extract_features(getattr(asset, "sql", None))
    if not features.parsed:
        return True
    return features.tables <= wanted


def oracle_solver(
    rung: OracleRung,
    corpus: "Corpus",
    gateway: "Gateway",
    settings: "Settings",
    identity: "Identity",
    *,
    model,
    gold: GoldIndex,
    embedder=None,
    session_id: str = "oracle",
    dialect: str = "postgres",
    # How many tables ``oracle_tables_padded`` pads up to. Matches the retrieval
    # table budget (``retrieval.rvgd.retrieve``'s ``top_k``, 8 by default), because
    # that is what bounds the licensed set an ``oracle_schema`` turn actually sees.
    # Padding beyond it would hand the model tables retrieval would never have
    # licensed, which is a different and less interesting counterfactual.
    table_budget: int = 8,
    # Graphs held by THIS solver. A parameter because the pool gives each worker its
    # own solver, so the run's total is this times the worker count; the caller
    # divides the budget rather than multiplying the footprint.
    graph_cache_max: int = 32,
    enable_run_log: bool = False,
):
    """A solver for one oracle rung.

    ``oracle_sql`` never calls the model. The other two build a serve graph per
    distinct narrowed corpus and cache it: ``oracle_schema`` needs one per schema
    (bounded by the lake's schema count), while ``oracle_tables`` needs one per
    distinct gold table set, which is roughly one per question. Graph construction
    is small next to a model call, but it is not free, and that is the honest cost
    of the most informative rung.

    Portable run logging is forced off, as in ``arms.agent_solver`` — and with more
    at stake here. A rung's corpus is built from the answer key, so its turns are
    answer-key-derived by construction; landing them in the durable log stamps them
    ``producer=serve, serve_path=agent``, with ``oracle_rung`` living only in the eval
    ``meta`` and never in provenance. Such a row is indistinguishable from a real
    serve turn except by a ``thread_id`` prefix convention, which is not a governance
    boundary. The module docstring says these numbers can never be reported as system
    performance; keeping them out of the log is how that holds when someone later
    queries the log instead of the run directory. Opt in with ``enable_run_log=True``
    and settings whose ``run_log_kind`` points somewhere deliberate.
    """
    from dataclasses import replace as dc_replace

    from ..analyst.agent import ServeDeployment, build_serve_rails
    from ..logging_setup import bind_log_context, reset_log_context
    from ..obs import RunContext, tracing_invoke_config
    from ..prompts import prompt_set_hash as _psh
    from ..provenance import new_run_id, turn_id as make_turn_id

    log_settings = (
        settings
        if enable_run_log
        else dc_replace(settings, run_log_kind="off")
    )

    # Bounded LRU, not an unbounded dict. ``oracle_schema`` needs one graph per
    # schema (tens), but ``oracle_tables`` needs one per distinct gold table set —
    # roughly one per question. On a full benchmark that is thousands of compiled
    # graphs, each closing over a corpus, a join graph, an allowlist and a retrieval
    # index cache, all held for the life of the run. A cache that grows without limit
    # to serve mostly-unrepeated keys is just a memory leak with good intentions; the
    # cap keeps the reuse that matters (consecutive questions over one schema) and
    # lets the rest go.
    #
    # Closure-local, and deliberately so: this is what makes a rung safe to run
    # concurrently. Each worker gets its own solver and therefore its own cache and
    # its own ``n_built``, which is the ``ServeWorker`` isolation contract. Sharing
    # one solver across threads would race on both.
    _GRAPH_CACHE_MAX = max(1, graph_cache_max)
    graphs: "OrderedDict[tuple, Any]" = OrderedDict()
    n_built = 0

    def _graph_for(key: tuple, narrowed: "Corpus"):
        nonlocal n_built
        cached = graphs.get(key)
        if cached is not None:
            graphs.move_to_end(key)
            return cached
        graph = build_serve_rails(
            deployment=ServeDeployment(
                corpus=narrowed,
                gateway=gateway,
                settings=log_settings,
                identity=identity,
                model=model,
                embedder=embedder,
                # Distinct per BUILD, not per cache slot: an evicted-then-rebuilt
                # graph must not reuse a session id, or two graphs' turns collide.
                session_id=f"{session_id}:{n_built}",
            )
        )
        n_built += 1
        graphs[key] = graph
        while len(graphs) > _GRAPH_CACHE_MAX:
            graphs.popitem(last=False)
        return graph

    class _OracleSolver:
        def solve_with_meta(self, question: str) -> tuple[str | None, dict]:
            item = gold.get(question)
            if item is None:
                # No gold for this question: the rung cannot be applied, and
                # answering it anyway would mix an ordinary turn into a
                # counterfactual arm.
                return None, {
                    "refused_by": "no_coverage",
                    "oracle_rung": rung.value,
                    "oracle_applied": False,
                }
            gold_sql = _gold_sql(item)

            schema = str(item.get("db_id") or "") or None

            if rung is OracleRung.sql:
                # The grader's own ceiling. No model, no serve path, so no routing
                # provenance either — reported as the gold schema for the same
                # reason as the other rungs below.
                #
                # No ``schema_pick``, for the reason the graph-serving branch states
                # at length below: a rung is *handed* its schema, it does not choose
                # one. Stamping the answer key here made ``pick_hit`` true on every
                # row by construction, so the run published
                # ``schema_pick_accuracy: 1.0`` — into the ledger headline, beside
                # the real arms — for a picker that never ran. ``routed_schemas``
                # already carries the provenance, and ``routing_bypassed`` already
                # keeps it out of the recall denominator.
                return gold_sql, {
                    "oracle_rung": rung.value,
                    "oracle_applied": True,
                    "tier": "governed",
                    "semantic_assurance": "unflagged",
                    "routed_schemas": [schema] if schema else [],
                    "routing_bypassed": True,
                }

            tables: frozenset[str] | None = None
            gold_only: frozenset[str] | None = None
            padding_degenerate = False
            if rung in (OracleRung.tables, OracleRung.tables_padded):
                tables = gold_tables_for(gold_sql, dialect=dialect)
                gold_only = tables
                if rung is OracleRung.tables_padded and schema:
                    # Pad up to the number of tables retrieval would have LICENSED
                    # under ``oracle_schema``, not up to the schema's table count.
                    # Padding to the latter reproduces the schema corpus exactly and
                    # the arm degenerates into ``oracle_schema``, measuring nothing —
                    # observed live at 11/11 tables and a byte-identical context.
                    schema_tables = len(restrict_corpus(corpus, schema=schema).tables())
                    tables = pad_tables(
                        corpus,
                        schema=schema,
                        gold=tables,
                        target=min(schema_tables, table_budget),
                        seed_key=str(item.get("question_id") or question),
                    )
                    # The control is vacuous at BOTH ends, and each end collapses it
                    # onto a different neighbour, so neither can be left unflagged.
                    # Padded up to the whole schema, it is ``oracle_schema``. Not
                    # padded at all — which happens whenever gold already needs as
                    # many tables as the budget allows — it is ``oracle_tables``.
                    # Either way the row carries no information about table identity
                    # and must not be averaged in as though it did.
                    padding_degenerate = (
                        len(tables) >= schema_tables or tables == gold_only
                    )
            narrowed = restrict_corpus(corpus, schema=schema, tables=tables)
            if not narrowed.tables():
                logger.warning(
                    "oracle %s: no corpus tables survived for question in schema %s "
                    "(gold tables %s) — scoring it as unsolvable rather than "
                    "quietly falling back to the full corpus",
                    rung.value,
                    schema,
                    sorted(tables or ()),
                )
                return None, {
                    "refused_by": "no_coverage",
                    "oracle_rung": rung.value,
                    "oracle_applied": False,
                }

            key = (schema, tables)
            graph = _graph_for(key, narrowed)
            self._n = getattr(self, "_n", 0) + 1
            rid = new_run_id()
            tid = make_turn_id(session_id, self._n)
            log_tokens = bind_log_context(run_id=rid, turn_id=tid)
            try:
                ctx = RunContext(
                    run_id=rid,
                    turn_id=tid,
                    schema=schema,
                    corpus_pin=getattr(log_settings.datasource, "corpus_pin", None),
                    prompt_set_hash=_psh(log_settings.prompt_variants),
                )
                final = graph.invoke(
                    {"question": question, "session_id": session_id},
                    config=tracing_invoke_config(ctx=ctx),
                )
                answer = final.get("answer")
            finally:
                reset_log_context(log_tokens)
            if answer is None:
                return None, {
                    "refused_by": "no_coverage",
                    "oracle_rung": rung.value,
                    "oracle_applied": True,
                }
            prov = dict(answer.provenance or {})
            meta = {
                "oracle_rung": rung.value,
                "oracle_applied": True,
                # What the rung actually handed over, so the delta is inspectable
                # rather than taken on trust. Compare `oracle_gold_tables` against
                # the `licensed_tables` a fair arm recorded for the same question:
                # if licensing already contained every gold table, this rung
                # removed no table-selection error and its lift is distractor
                # removal, not selection. That was true on 103/103 questions of the
                # corpus this was first measured on.
                # The gold tables, and separately the set actually offered. Under
                # ``oracle_tables`` they are the same; under the padded rung the
                # offered set is gold plus distractors, and storing that under a
                # name containing "gold" would make the licensing-recall check the
                # docs prescribe silently wrong on exactly the arm it matters for.
                "oracle_gold_tables": sorted(gold_only) if gold_only is not None else None,
                "oracle_offered_tables": sorted(tables) if tables is not None else None,
                "oracle_corpus_tables": len(narrowed.tables()),
                # True when the schema was too small for padding to leave any
                # distractor out, so this row's padded arm IS oracle_schema and
                # carries no information about table identity.
                "oracle_padding_degenerate": padding_degenerate,
                "refused_by": prov.get("refused_by"),
                "failed_layer": prov.get("failed_layer"),
                "tier": answer.tier.value,
                "semantic_assurance": answer.semantic_assurance.value,
                # The rest of the governance stamp, mirroring ``arms.agent_solver``.
                # These were dropped here, so every oracle row recorded ``None`` for all
                # three and the summary's ``n_*_observed`` counts read 0 on an arm that
                # delivered every row — making the rates unreadable on exactly the rungs
                # whose purpose is to isolate where EX comes from. ``oracle_schema`` and
                # friends serve through the real graph and hold a real ``Answer``, so the
                # stamp is there to relay.
                "safety_clearance": answer.safety_clearance,
                "graded_delivery": bool(prov.get("graded_delivery")),
                "coverage_best_effort": bool(prov.get("coverage_best_effort")),
                "injected_note_ids": prov.get("injected_note_ids"),
                "n_notes_injected": prov.get("n_notes_injected"),
                "context_chars": prov.get("context_chars"),
                "context_hash": prov.get("context_hash"),
                "stage_events": prov.get("stage_events"),
                "usage": prov.get("token_sum") or prov.get("usage"),
                # Routing is *bypassed* on these rungs, not failed: a corpus pinned
                # to one schema gives the router a single candidate, so it never
                # engages and stamps no provenance. Relaying that absence verbatim
                # records `routed_hit=False` on every row, which the taxonomy then
                # reads as a routing miss — so a rung whose entire purpose is to
                # remove routing error would report that routing is the whole
                # problem. The honest value is the schema the rung pinned: routing
                # here is trivially correct by construction.
                "routed_schemas": prov.get("routed_schemas") or [schema],
                # NOT relayed as a pick. A rung pins its schema; it does not choose
                # one. Stamping `schema_pick` here enrolled every oracle row in
                # `schema_pick_accuracy` as a unanimous success of a picker that never
                # ran — the same defect the assemble node avoids on the single-schema
                # path. `routed_schemas` above is enough for `routed_hit`.
                #
                # The bypass flag is RELAYED, never re-derived. It used to read
                # `not prov.get("routed_schemas")`, which inferred the bypass from an
                # absence; the moment `assemble` started stamping `routed_schemas` on
                # the single-schema path — which is exactly the case every rung
                # creates — that expression silently flipped to False and the rung
                # rejoined the routing denominator, scoring a trivially perfect recall
                # off a schema it had been handed. Positive evidence only.
                "routing_bypassed": bool(prov.get("routing_bypassed")),
            }
            return answer.sql, meta

        def solve(self, question: str) -> str | None:
            return self.solve_with_meta(question)[0]

    return _OracleSolver()
