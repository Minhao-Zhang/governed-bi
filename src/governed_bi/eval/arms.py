"""The eval-ladder harness (Architecture section 8; D4).

Runs a set of questions through a *solver* (question -> SQL, or None if it
declines/refuses) and scores EX plus the free behavioral signals:

- **decoy-touch rate**: share of produced queries that reference a
  manifest-flagged fake column (Analyst "three points" #1 drives this to 0 in
  dev via the suspect hard-block). Computed here from the corpus suspect set.
- **governed-path adherence**: share of questions the solver actually answered
  (produced SQL for) rather than refused.

The eval ladder's fair rungs differ only by the corpus fed into the *same* serve
path, and they are ordered so that each **adjacent** step changes exactly one
thing — see :class:`Arm`. ``baseline -> seeded`` is multi-mechanism and free of
LLM cost: train-SQL-derived joins and metrics, decoy / negative-space marking of
columns absent from gold, and dropping baseline's FK-name guesses — **no
few-shots**. Do not read it as "what parsing the training SQL is worth" alone.
``seeded -> curated`` is what the curator LLM adds on top (including few-shots);
the SME steps are the growth axis. Deltas between non-adjacent rungs bundle more
than one intervention and are labelled as such by the driver
(``run_datalake.skipped_rungs``). This module supplies the reusable scorer
(``run_arm``) and ``agent_solver``, which drives the agentic serve core (ADR 0002)
for every fair rung — ``run_arms`` scores whichever rungs the caller supplies
solvers for.

Two different test-aware constructs stay out of this enum, and they are not the
same thing.

``ceiling`` (D14) is a Simulated SME given a split-scoped retrieval index: it may
see test *questions* and their evidence, but never test gold SQL. That leakage
boundary is its definition, not a detail — it is what makes ``1 - ceiling`` read
as irreducible SQL-generation error. It is still designed, not built; there is no
split-scoped index in :mod:`governed_bi.curator.sme`, whose brief remains
train-only. See ``docs/glossary.md`` and the D14 amendment.

The counterfactual oracle rungs in :mod:`governed_bi.eval.oracle` (``oracle_sql``,
``oracle_schema``, ``oracle_tables``, ``oracle_tables_padded``) do exist, and they
are a different question: hand one stage its gold answer and re-measure, so the
lift bounds that stage's headroom. They read the answer key outright —
``oracle_sql`` submits test gold SQL verbatim — which is precisely what the
ceiling forbids, so building them did not build the ceiling.

Neither gets an ``Arm`` member. Keeping them out is what stops one of their
numbers being quoted as system performance, and it is pinned by
``tests/test_oracle_and_probes.py::test_oracle_rungs_are_not_members_of_the_fair_arm_ladder``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any, Iterable, Protocol, runtime_checkable

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.scope import traverse_scope

from .ex import execution_match

_log = logging.getLogger(__name__)

if TYPE_CHECKING:
    from ..config import Settings
    from ..corpus import Corpus
    from ..gateway import Gateway, Identity
    from .dataset import EvalItem


class Arm(str, Enum):
    """The fair ladder, ordered so each step changes exactly one thing.

    ``seeded`` exists because ``baseline -> curated`` used to change two independent
    things at once and no arm separated them. ``build_curated_corpus`` always runs a
    *mechanical* pass first — train-SQL-derived join and metric seeding, plus
    marking columns absent from gold as decoys — and then *optionally* the LLM
    curator agent on top. Every ``baseline -> curated`` delta was therefore equally
    explainable by the free, deterministic seed alone, and the headline claim
    ("the curator LLM layer is worth N points") could not be told apart from
    ("parsing the training SQL is worth N points"). The distinction decides whether
    the product needs an LLM curator at all.

    ``seeded`` costs no model calls to build — it is the same code path with
    ``run_agent=False`` — so the only price is one more serve pass.
    """

    baseline = "baseline"  # deterministic-max, DB-derivable only; no train SQL
    seeded = "seeded"  # + train-SQL-derived joins/metrics/decoys; still no LLM
    curated = "curated"  # + curator LLM agent pass over that seed
    #: The Simulated-SME clarification round.
    #:
    #: **This rung bundles two mechanisms and cannot be split.** The SME's brief is
    #: built from BIRD's ``database_description/*.csv`` — human-authored column and
    #: value descriptions — which Phase A never sees. So a positive delta is exactly
    #: as consistent with "we handed the pipeline a new, higher-quality knowledge
    #: source for the first time" as with "the clarification protocol works", and the
    #: headline claim is the latter.
    #:
    #: A ``curated_sme_blind`` rung used to exist to separate them, building the brief
    #: from train questions and evidence only. It was removed 2026-07-28 as
    #: meaningless: those are inputs Phase A *already* has, so the rung compared the
    #: curator against itself re-asked through a Q&A round-trip, and the only thing it
    #: genuinely added was ``certified`` provenance stamping. Splitting this confound
    #: needs a knowledge source the curator lacks and a human does not simulate — not
    #: another arm over the same inputs. Until then, do not read a ``curated_sme``
    #: delta as evidence for the protocol.
    curated_sme = "curated_sme"
    # No member for ``ceiling`` (still unbuilt) or for the oracle rungs (built, but
    # they read the answer key). See the module docstring — the two are different
    # constructs and neither belongs on the fair ladder.


#: The ladder in order, derived from the enum rather than spelled again. Two
#: independent spellings of the same sequence drift, and both reporting paths
#: (``run_datalake``'s ``summary.json`` and ``analysis``'s ``analysis.json``) decide
#: adjacency from this one.
ARM_ORDER: tuple[str, ...] = tuple(a.value for a in Arm)


#: What each ladder step actually changes, one entry per mechanism. Adjacency is not
#: the same claim as "one mechanism": ``baseline -> seeded`` is a single rung and
#: changes three things at once, and reporting it as ``single_variable: true`` was the
#: measurement error AUDIT E5 named. Keyed by the step's UPPER arm.
STEP_MECHANISMS: dict[str, tuple[str, ...]] = {
    "seeded": (
        "train-SQL-derived joins",
        "train-SQL-derived metrics",
        "decoy / negative-space column marking",
    ),
    "curated": (
        "LLM curator agent pass",
        "few-shot exemplars",
    ),
    "curated_sme": (
        "clarification protocol",
        "BIRD human column documentation (SME brief)",
    ),
}


def step_mechanisms(lo: str, hi: str) -> tuple[str, ...]:
    """Every mechanism that differs between two ladder arms, in ladder order.

    ``()`` for a pair that is not on the ladder (oracle / replicate diagnostics), for
    the same reason :func:`skipped_rungs` returns ``[]`` there: there is no span.
    """
    order = list(ARM_ORDER)
    if lo not in order or hi not in order:
        return ()
    lo, hi = sorted((lo, hi), key=order.index)
    out: list[str] = []
    for arm in order[order.index(lo) + 1 : order.index(hi) + 1]:
        out.extend(STEP_MECHANISMS.get(arm, ()))
    return tuple(out)


def ladder_steps(present: "Iterable[str]") -> list[tuple[str, str]]:
    """Adjacent rungs to report a delta between, given the arms actually scored.

    Deltas are reported only between *consecutive* rungs, because that is the only
    pairing where exactly one thing changed. ``baseline -> seeded`` adds the
    deterministic train-SQL joins/metrics and decoy / negative-space marking (no
    few-shots, no LLM); ``seeded -> curated`` adds the LLM curator agent on top of
    that seed (including few-shots); ``curated -> curated_sme`` adds the
    clarification protocol *and* BIRD's human column documentation together. A
    ``baseline -> curated`` delta bundles the first two and cannot say which paid
    for it, and the SME step is permanently bundled — see :class:`Arm`.

    Derived from what ran rather than fixed, so a partial ``--arms`` selection
    chains the rungs it has instead of either inventing a comparison or reporting
    none.
    """
    order = [a for a in ARM_ORDER if a in set(present)]
    return list(zip(order, order[1:]))


def skipped_rungs(lo: str, hi: str) -> list[str]:
    """Ladder rungs between ``lo`` and ``hi`` that this run did not score.

    Non-empty means the step bundles more than one intervention, so its delta cannot
    be attributed to either. Reported rather than suppressed: the comparison is still
    the best available, and a reader who is told what it bundles can judge it, while
    a reader who is not will read it as a single-variable result — which is how
    "the curator LLM layer is worth N points" came to mean "the LLM layer or the
    train-SQL seed, unknown which".

    Order-insensitive. Called with its arguments reversed it used to return ``[]`` —
    "nothing skipped, one thing changed" — for a pair spanning the whole ladder, which
    is the most wrong answer available and silent. Every call site happened to
    pre-sort, so it was a footgun rather than a bug; normalising here means a new
    caller cannot re-arm it.

    An arm not on the ladder yields ``[]`` because there is no span to compute. That
    is not "one thing changed", and callers must not read it as such — see how
    ``single_variable`` is set to ``None`` rather than ``True`` for those pairs.
    """
    order = list(ARM_ORDER)
    if lo not in order or hi not in order:
        return []
    lo, hi = sorted((lo, hi), key=order.index)
    return order[order.index(lo) + 1 : order.index(hi)]


@dataclass(frozen=True)
class ArmResult:
    arm: Arm
    ex: float
    decoy_touch_rate: float
    governed_path_adherence: float
    n: int


@runtime_checkable
class Solver(Protocol):
    """Turns a question into SQL, or ``None`` if it declines / refuses."""

    def solve(self, question: str) -> str | None: ...


@runtime_checkable
class MetaSolver(Solver, Protocol):
    """A :class:`Solver` that also returns per-question audit metadata.

    ``solve_with_meta`` is the primitive: it returns ``(sql, meta)`` for one
    question with **no shared-mutable state**, so a result pairs to its question
    by return value (not by call order). That makes it safe to call
    concurrently on distinct instances and removes the stale-meta hazard the old
    ``last_solve_meta`` instance attribute carried (audit-backlog C5). ``solve``
    stays as the SQL-only convenience for callers that do not need the meta
    (``run_arm`` / the refuse-gate).
    """

    def solve_with_meta(self, question: str) -> tuple[str | None, dict]: ...


def _split_suspect_refs(
    suspect_columns: frozenset[str],
) -> tuple[frozenset[str], frozenset[str]]:
    """Split a suspect set into ``table.column`` refs and table-less column names.

    Callers hand in a mix of spellings: ``column_allowlist`` yields
    ``schema.table.column``, the corpus scan and the BIRD trap manifest yield
    ``table.column``, and a caller that only knows a column name passes it bare.
    Anything qualified is folded to its last two segments, because the physical
    table is what a query actually names.

    A bare name whose column is *also* covered by a qualified ref is dropped.
    Keeping it would make the qualified ref moot and re-open C6 — bare matching is
    exactly how a legitimate column sharing a decoy's name in another table inflated
    ``decoy_touch_rate``. A bare name with **no** qualified counterpart is kept: the
    caller could not attribute it to a table, and dropping it would narrow the metric
    to a silent zero, which is the worse error of the two.
    """
    qualified = {".".join(ref.lower().split(".")[-2:]) for ref in suspect_columns if "." in ref}
    covered = {ref.split(".", 1)[1] for ref in qualified}
    bare = {ref.lower() for ref in suspect_columns if "." not in ref}
    return frozenset(qualified), frozenset(bare - covered)


def _binding(scope: Any, qualifier: str) -> Any:
    """Innermost lexical binding for a column qualifier, or ``None`` if undeclared.

    Walks outward because a correlated subquery may qualify a column with an alias
    the outer query declared. One flat alias map per statement would let a reused
    alias (``t`` naming a different table inside and outside) attribute a column to
    the wrong table, which is the same defect C6 is about.
    """
    want = qualifier.lower()
    current = scope
    while current is not None:
        for name, source in current.sources.items():
            if name.lower() == want:
                return source
        current = current.parent
    return None


def _touches_suspect(sql: str, suspect_columns: frozenset[str], dialect: str) -> bool:
    """Did ``sql`` reference a decoy / suspect column?

    Matching is on the resolved ``table.column`` (C6): a query reading
    ``orders.city`` must not count as a decoy touch merely because
    ``customers.city`` is flagged in a different table.

    Where a query is genuinely ambiguous the attribution errs toward *counting* — an
    unqualified name is judged against every base table in its own scope, the same
    fail-closed reading guardrail L3 uses. This metric exists to catch the model
    reading a poisoned column, and an over-count is visible in the rate while an
    under-count is indistinguishable from good behaviour.
    """
    if not suspect_columns:
        return False
    qualified, bare_only = _split_suspect_refs(suspect_columns)
    if not qualified and not bare_only:
        return False
    try:
        tree = sqlglot.parse_one(sql, read=dialect)
    except sqlglot.errors.SqlglotError:
        return False  # unparseable SQL can't be inspected; a non-parse bug is not swallowed

    def _owned(owners: "set[str]", name: str) -> bool:
        return any(f"{owner}.{name}" in qualified for owner in owners)

    try:
        scopes = list(traverse_scope(tree))
    except Exception:
        scopes = []

    if not scopes:
        # No lexical scopes (a bare expression, or a statement sqlglot cannot scope):
        # every table the statement names is a candidate owner. Guessing wide keeps an
        # unscoped query from reading as decoy-free just because it could not be resolved.
        every_table = {t.name.lower() for t in tree.find_all(exp.Table)}
        for col in tree.find_all(exp.Column):
            if isinstance(col.this, exp.Star):
                continue
            name = col.name.lower()
            owners = {col.table.lower()} if col.table else every_table
            if name in bare_only or _owned(owners, name):
                return True
        return False

    for scope in scopes:
        base = {
            src.name.lower() for src in scope.sources.values() if isinstance(src, exp.Table)
        }
        for col in scope.find_all(exp.Column):
            if isinstance(col.this, exp.Star):
                continue  # ``t.*`` names no column to attribute
            name = col.name.lower()
            if name in bare_only:
                return True
            if not col.table:
                owners = base  # ambiguous bare name: any in-scope base table could own it
            else:
                source = _binding(scope, col.table)
                if isinstance(source, exp.Table):
                    owners = {source.name.lower()}
                elif source is None:
                    owners = {col.table.lower()}  # declared nowhere: take the qualifier literally
                else:
                    continue  # a CTE / derived source; its base columns are checked in their own scope
            if _owned(owners, name):
                return True
    return False


def run_arm(
    arm: Arm,
    gateway: "Gateway",
    items: "list[EvalItem]",
    solver: Solver,
    *,
    suspect_columns: frozenset[str] = frozenset(),
    dialect: str = "sqlite",
) -> ArmResult:
    """Score one arm: EX over ``items`` plus decoy-touch and governed-path rates."""
    matches = 0
    produced = 0
    decoy = 0
    for item in items:
        pred = solver.solve(item.question)
        if not pred:
            continue  # refused / no SQL: not a governed-path answer
        produced += 1
        if _touches_suspect(pred, suspect_columns, dialect):
            decoy += 1
        if execution_match(pred, item.sql, gateway):
            matches += 1
    n = len(items)
    return ArmResult(
        arm=arm,
        ex=matches / n if n else 0.0,
        decoy_touch_rate=decoy / produced if produced else 0.0,
        governed_path_adherence=produced / n if n else 0.0,
        n=n,
    )


def run_arms(
    gateway: "Gateway",
    items: "list[EvalItem]",
    solvers: dict[Arm, Solver],
    *,
    suspect_columns: frozenset[str] = frozenset(),
    dialect: str = "sqlite",
) -> dict[Arm, ArmResult]:
    """Score every provided arm. Callers supply the solvers they can run (e.g.
    just the ``curated`` arm in dev); the other fair rungs plug in the same way
    once their solvers exist."""
    return {
        arm: run_arm(
            arm, gateway, items, solver, suspect_columns=suspect_columns, dialect=dialect
        )
        for arm, solver in solvers.items()
    }


def _ledger_for_artifact(ledger: list | None) -> list | None:
    """Project the serve-path ledger for generations jsonl.

    Keep per-action layer/verdict fields needed to prove graded-delivery recheck
    after the fact. Drop ``result`` (full row payloads — up to ``max_rows`` and
    non-JSON types like ``Decimal`` / ``bytes``) and every other key.
    """
    if ledger is None:
        return None
    out: list[dict[str, Any]] = []
    for entry in ledger:
        if not isinstance(entry, dict):
            continue
        row = {k: entry.get(k) for k in ("action", "verdict", "layer", "sql", "allowed")}
        result = entry.get("result")
        if isinstance(result, dict) and "row_count" in result:
            row["row_count"] = result["row_count"]
        out.append(row)
    return out


def agent_solver(
    corpus: "Corpus",
    gateway: "Gateway",
    settings: "Settings",
    identity: "Identity",
    *,
    model,
    embedder=None,
    session_id: str = "eval",
    enable_run_log: bool = False,
) -> MetaSolver:
    """A :class:`MetaSolver` that drives the ADR-0002 agentic serve core.

    Routes through ``answer_question_agent`` (the ``create_agent`` +
    governance-middleware path) — the one serve path shared by every fair rung
    of the eval ladder (``baseline`` / ``seeded`` / ``curated`` / ``curated_sme``).
    The outer rails graph is built once and invoked per question; each call is independent
    (no working memory / cache), matching the single-round eval contract.
    ``solve_with_meta`` returns ``(sql, meta)`` where ``meta`` carries audit
    fields plus the governance-ledger length; ``solve`` returns just the SQL.

    Each question increments ``n_human`` and mints a fresh ``run_id`` (see
    ``ingest``) so portable run-log UPSERTs do not collapse an N-question run
    into one ``{session_id}:1`` row. Pass a distinct ``session_id`` per arm (and,
    under concurrency, per worker) so graphs do not collide on it either.

    Portable run logging is forced off here: eval metrics live in the returned
    ``meta`` / experiment rows. Opt in by passing settings with ``run_log_kind``
    already set to a non-default destination via ``enable_run_log=True``.
    """
    from dataclasses import replace as dc_replace

    from ..analyst.agent import build_serve_rails

    log_settings = (
        settings
        if enable_run_log
        else dc_replace(settings, run_log_kind="off")
    )

    graph = build_serve_rails(
        corpus=corpus,
        gateway=gateway,
        settings=log_settings,
        identity=identity,
        model=model,
        embedder=embedder,
        session_id=session_id,
    )

    class _AgentSolver:
        def solve_with_meta(self, question: str) -> tuple[str | None, dict]:
            from ..logging_setup import bind_log_context, reset_log_context
            from ..obs import RunContext, tracing_invoke_config
            from ..prompts import prompt_set_hash as _psh
            from ..provenance import new_run_id, turn_id as make_turn_id

            self._n = getattr(self, "_n", 0) + 1
            rid = new_run_id()
            tid = make_turn_id(session_id, self._n)
            log_tokens = bind_log_context(run_id=rid, turn_id=tid)
            try:
                # One line per question so run.log joins to Langfuse / stage_events
                # on the same run_id (N12a three-sink accept). Progress stays on
                # stdout via the driver's on_result hook — do not replace that.
                _log.info("serve question n=%s session=%s", self._n, session_id)
                ctx = RunContext(
                    run_id=rid,
                    turn_id=tid,
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
                return None, {"refused_by": "no_coverage"}
            prov = dict(answer.provenance or {})
            ledger = prov.get("governance_ledger")
            meta = {
                "refused_by": prov.get("refused_by"),
                # The exception class behind a ``model_error``. Those classify as
                # crashes, so a wave of them already blocks quotability — but without
                # the type an operator sees only "crash_rate 0.4" and cannot tell a
                # provider rate limit from a bug in us. That distinction decides
                # whether to re-run at lower concurrency or to go and fix something,
                # and it gets more valuable the higher ``--workers`` /
                # ``--build-workers`` are set.
                "error_type": prov.get("error_type"),
                "failed_layer": prov.get("failed_layer"),
                "graded_delivery": bool(prov.get("graded_delivery")),
                "coverage_best_effort": bool(prov.get("coverage_best_effort")),
                "tier": answer.tier.value,
                "semantic_assurance": answer.semantic_assurance.value,
                "safety_clearance": answer.safety_clearance,
                "attempts": prov.get("attempts"),
                # ``None`` when no ledger was recorded at all, ``0`` when one was
                # recorded and stayed empty. ``len(... or [])`` collapsed the first
                # into the second, which is how "we never looked" reads as "nothing
                # happened".
                "ledger_len": len(ledger) if ledger is not None else None,
                # Per-action layer/verdict list (projected — no query result rows).
                # Kept under the serve-path name ``governance_ledger`` (do not rename
                # to ``guardrail_log`` here — that is checklist 4.1).
                "governance_ledger": _ledger_for_artifact(
                    list(ledger) if ledger is not None else None
                ),
                # Per-stage diagnostics the serve path stamps on provenance. Relayed
                # verbatim with no default: a missing key means the producer recorded
                # no stages, and an empty dict there would assert the different (and
                # false) fact that it recorded zero.
                "stage_events": prov.get("stage_events"),
                "n_tool_calls": prov.get("n_tool_calls"),
                "by_guardrail_layer": prov.get("by_guardrail_layer"),
                # Schema-routing provenance (D15 data-lake): which schemas the router
                # shortlisted/kept and, under llm-pick, the single chosen schema —
                # so a pooled run can score routing recall separately from EX.
                "routed_schemas": prov.get("routed_schemas"),
                "shortlisted_schemas": prov.get("shortlisted_schemas"),
                "schema_pick": prov.get("schema_pick"),
                "schema_pick_fallback": prov.get("schema_pick_fallback"),
                "total_schemas": prov.get("total_schemas"),
                # Asserted by the assemble node when the corpus holds one schema, so
                # "the router never ran" is positive evidence rather than a count the
                # eval has to reinterpret. Without the relay the flag dies here and
                # the driver falls back to guessing from ``total_schemas``.
                "routing_bypassed": prov.get("routing_bypassed"),
                # Table-level provenance: what retrieval offered vs what licensing
                # kept, so a wrong-table answer can be attributed to retrieval or to
                # generation offline (see ``governed_bi.eval.analysis``).
                "retrieved_tables": prov.get("retrieved_tables"),
                "licensed_tables": prov.get("licensed_tables"),
                # The tables in the SQL that was actually delivered, as asset ids. The
                # only signal that can see the agent reaching past the router:
                # ``licensed_tables`` is the assemble-time seed license, computed from the
                # ROUTED corpus and never amended, so it cannot contain an out-of-routed
                # schema however far the agent went.
                "tables_used": prov.get("tables_used"),
                # Delivery: what the assembled context actually handed the model.
                "injected_note_ids": prov.get("injected_note_ids"),
                "n_notes_injected": prov.get("n_notes_injected"),
                "n_few_shots_injected": prov.get("n_few_shots_injected"),
                "n_joins_injected": prov.get("n_joins_injected"),
                "n_metrics_injected": prov.get("n_metrics_injected"),
                "n_terms_injected": prov.get("n_terms_injected"),
                "n_caveats_injected": prov.get("n_caveats_injected"),
                "context_chars": prov.get("context_chars"),
                # Identity of the assembled context, not just its size. Two arms
                # whose corpora differ but whose prompts do not are not two arms;
                # ``eval.treatment`` compares these to catch a treatment that was
                # built but never delivered.
                "context_hash": prov.get("context_hash"),
                # Which prompt text produced this row. Relayed from the serve
                # path's own stamp rather than re-read from settings here: the
                # point is to record what the graph sent, and a driver-side
                # re-derivation would still agree with itself if the threading
                # broke.
                "prompt_variants": prov.get("prompt_variants"),
                "prompt_set_hash": prov.get("prompt_set_hash"),
                # ADR 0004 L7: token / cost from finalize_and_log provenance.
                "token_sum": prov.get("token_sum"),
                # The per-source breakdown, not just the turn total. Entries are
                # ``{"source": "router"|"agent_core"|"narrator"|..., "usage_metadata":
                # {...}}``, already tagged where the tokens were spent. Relaying only
                # the collapsed ``token_sum`` meant a cost difference between two arms
                # could not be attributed to a stage — you could see an arm got dearer
                # and not whether the router, the agent loop or a repair pass did it,
                # which is the first question anyone asks of a cost regression.
                "token_usage": prov.get("token_usage"),
                "cost_est_usd": prov.get("cost_est_usd"),
                "usage": prov.get("token_sum") or prov.get("usage"),
                # Prefer the ids bound into Langfuse / logging for this invoke —
                # provenance should match, but the outer mint is the join key.
                "turn_id": tid,
                "run_id": rid,
            }
            return answer.sql, meta

        def solve(self, question: str) -> str | None:
            return self.solve_with_meta(question)[0]

    return _AgentSolver()
