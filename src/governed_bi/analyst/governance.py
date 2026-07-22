"""Shared governance helpers for the agentic serve core (ADR 0002).

The single source of truth for the fail-closed paths (refuse-gate matching, L4
licensing scope, cache re-guardrailing, answer finalization, the two-axis stamp,
and the live event stream) that ``analyst.agent``'s outer rails + middleware call,
so governance decisions live in exactly one place and cannot drift.
"""

from __future__ import annotations

import re
from dataclasses import replace
from typing import TYPE_CHECKING, Any

import sqlglot
from sqlglot import exp

from ..gateway import GuardrailLayer, check
from ..graph import join_neighborhood, plan_joins
from .answer import (
    LOW_CONFIDENCE_JOIN,
    RESULT_PREVIEW_ROWS,
    Answer,
    ReliabilityTier,
    ResultTable,
    SemanticAssurance,
    UncertaintySignals,
    assemble,
    graded_delivery,
    refusal,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from ..config import Settings
    from ..corpus import Corpus
    from ..gateway import ColumnAllowlist, Gateway, Identity, QueryResult
    from ..graph import MissingJoinPath
    from .narrate import AnswerNarrator

from ..corpus.schemas import JoinAsset, NegativeExampleAsset, TableAsset

# How many FK-join hops out from the retrieved tables L4 licensing extends (see
# _licensed_table_ids). 1 admits a retrieved table's direct FK neighbors; raising it
# widens the licensed table scope further. Tunable heuristic; tune against the
# eval's false_refusal_rate vs. any scope-widening cost.
LICENSE_JOIN_HOPS = 1

# Canned escalation blobs for the fail-closed paths (D5).
_ESCALATION_NO_COVERAGE = (
    "This question is outside the governed semantic layer. "
    "Contact the data owner to add coverage."
)
_ESCALATION_GUARDRAIL = (
    "The generated query was blocked by a safety guardrail. "
    "Rephrase the question or contact the data owner."
)
_ESCALATION_MISSING_EDGE = (
    "No curated relationship connects the schemas needed for this question. "
    "Contact the data owner to declare a cross-schema join."
)

# Refuse-gate tuning: how much a question must overlap a curated example to count
# as a match, and the stop-words dropped when keying on a negative pattern.
_REFUSE_JACCARD = 0.6
_STOPWORDS = frozenset(
    "a an and are about as at be by do does for from how in is many much of on or "
    "the their there to what when where which who why with work works".split()
)

# Graded delivery is an ALLOWLIST, never a denylist: the ONLY failures re-executed
# and delivered as "unverified" (when settings.grade_semantic_failures is on) are
# the curated SEMANTIC layers below. A query reaches one of these ONLY after
# clearing every safety/confidentiality layer before it — check() runs the layers
# in order and returns on the FIRST failure — so a semantic ``failed_layer`` is a
# proof, minted by check() itself, that L1/L2/L3 passed:
#   - L4 term_semantics: retrieval/licensing scope (touched an unlicensed table).
#   - L5 cost_estimate: accidental cartesian / cost guard.
# Everything else HARD-refuses, never graded-delivers:
#   - L2 policy_blacklist (DDL/DML/dangerous funcs) and L3 ast_column_allowlist
#     (governance.excluded + suspect columns, a CONFIDENTIALITY control — audit S2);
#   - the refuse-gate;
#   - and — critically — any entry that NEVER reached a check() verdict at all
#     (verdict "cap"/"error", exhausted/no_coverage, missing pass result — all carry
#     ``failed_layer=None``). A capped attempt cleared NO layer, so its SQL is never
#     executed on the graded-delivery path (audit Vuln 2 / broken access control).
# L3/L4/L5 stay *repairable mid-loop* (the agent retries on a BLOCKED ToolMessage,
# D5/D11); only the FINAL disposition is fixed here (pipeline-design §6).
_GRADED_DELIVERY_LAYERS = frozenset(
    {
        GuardrailLayer.term_semantics.value,
        GuardrailLayer.cost_estimate.value,
    }
)


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def _match_negative_example(corpus: "Corpus", question: str) -> "NegativeExampleAsset | None":
    """Return the first curated negative example the question matches, else None.

    Matches on either a distinctive keyword from the pattern (content words, stop
    words removed) or a high token-overlap with one of the example questions.
    Deterministic and conservative to avoid refusing a legitimate question.
    """
    q_tokens = set(_tokens(question))
    if not q_tokens:
        return None
    for asset in corpus.assets:
        if not isinstance(asset, NegativeExampleAsset):
            continue
        keywords = {t for t in _tokens(asset.pattern) if t not in _STOPWORDS}
        if q_tokens & keywords:
            return asset
        for example in asset.example_questions:
            ex_tokens = set(_tokens(example))
            if not ex_tokens:
                continue
            jaccard = len(q_tokens & ex_tokens) / len(q_tokens | ex_tokens)
            if jaccard >= _REFUSE_JACCARD:
                return asset
    return None


def _licensed_table_ids(corpus, graph, retrieval, join_ids, *, hops=LICENSE_JOIN_HOPS) -> frozenset[str]:
    """Table-asset ids the query is licensed to touch (the L4 term-semantics set).

    The union of three sources:
      1. the retrieval scope (candidate tables, which already include a bound
         metric's base table via grounding);
      2. the join endpoints of ``join_ids`` (the Steiner points the plan bridges
         through to connect the retrieved tables);
      3. the FK ``join_neighborhood`` of the retrieved tables, ``hops`` deep.

    Deliberately excludes the generator's self-declared tables so a rogue
    generator cannot authorize a table retrieval never surfaced. The physical-name
    ``allowed_tables`` the guardrail uses is derived from the assembled context
    (``PromptContext.allowed_table_names``) so the model sees exactly what L4 will
    permit.

    Decoupling L4 from retrieval recall (source 3): the lexical retriever can miss
    a table the correct answer needs; licensing the retrieved tables' FK neighbors
    means such a table is not refused just because retrieval under-recalled. This
    is safe because L3 (``column_allowlist``) still guards every column
    independently: only non-excluded, non-suspect columns are ever allowed, so
    reaching a FK-neighbor table exposes only its already-allowed columns and never
    leaks excluded/suspect data. It only widens which related tables' allowed
    columns are reachable, not what any single table exposes. A table beyond
    ``hops`` of (or disconnected from) every retrieved table stays out of scope and
    is still blocked at L4.
    """
    table_ids: set[str] = set(retrieval.table_ids)
    for join_id in join_ids:
        join = corpus.by_id(join_id)
        if isinstance(join, JoinAsset):
            table_ids.add(join.left_table)
            table_ids.add(join.right_table)
    table_ids |= join_neighborhood(graph, set(retrieval.table_ids), hops=hops)
    return frozenset(tid for tid in table_ids if isinstance(corpus.by_id(tid), TableAsset))


def missing_edge_refusal(
    base_provenance: dict,
    missing: "MissingJoinPath",
) -> Answer:
    """Fail-closed Answer for a D15 cross-schema missing curated join."""
    return refusal(
        escalation=_ESCALATION_MISSING_EDGE,
        provenance={
            **base_provenance,
            "refused_by": "missing_edge",
            "table_ids": sorted(missing.table_ids),
            "schemas": sorted(missing.schemas),
            "reason": missing.reason,
            "clarification_hint": {
                "kind": "missing_cross_schema_join",
                "schemas": sorted(missing.schemas),
                "table_ids": sorted(missing.table_ids),
            },
        },
    )


def _suspect_in_scope(sql: str, suspect: frozenset[str], dialect: str | None) -> bool:
    """Whether the SQL references a curator-flagged suspect column (by bare name).

    Only meaningful in prod, where suspect columns are permitted; in dev L3 has
    already hard-blocked them. Approximate (bare name, not table-qualified), which
    is sufficient for lowering the reliability stamp.
    """
    if not suspect:
        return False
    suspect_bare = {ref.split(".", 1)[1] for ref in suspect}
    try:
        tree = sqlglot.parse_one(sql, read=dialect)
    except Exception:
        return False
    return any(col.name in suspect_bare for col in tree.find_all(exp.Column))


def _render(result, generated) -> str:
    """A compact textual answer from the executed result."""
    if result.row_count == 1 and len(result.columns) == 1:
        return f"{result.columns[0]} = {result.rows[0][0]}"
    if result.row_count == 0:
        return "no rows"
    head = ", ".join(result.columns)
    suffix = " (truncated)" if result.truncated else ""
    return f"{result.row_count} row(s) over [{head}]{suffix}"


def _result_table(result: "QueryResult") -> ResultTable:
    """A bounded, display-ready snapshot of the executed result rows.

    Clipped to ``RESULT_PREVIEW_ROWS`` so a wide result never bloats the Answer;
    ``truncated`` reflects the gateway cap or this preview cap.
    """
    rows = list(result.rows[:RESULT_PREVIEW_ROWS])
    return ResultTable(
        columns=list(result.columns),
        rows=rows,
        row_count=result.row_count,
        truncated=result.truncated or len(result.rows) > RESULT_PREVIEW_ROWS,
    )


def _answer_text(
    question: str, sql: str, result: "QueryResult", table: ResultTable, narrator: "AnswerNarrator | None"
) -> str:
    """The answer text: a grounded NL phrasing when a narrator is injected, else
    the compact deterministic render. A narrator failure falls back to the render
    so a model hiccup never turns a governed answer into an error."""
    if narrator is None:
        return _render(result, None)
    try:
        return narrator.narrate(question, sql, table)
    except Exception:
        return _render(result, None)


def _unverified_prefix(provenance: dict) -> str:
    """The graded-delivery caveat banner (pipeline-design §6).

    Shared by the deterministic finalizer text and the LLM ``narrate`` node so the
    two phrasings of an unverified answer never drift.
    """
    refused_by = provenance.get("refused_by", "?")
    failed_layer = provenance.get("failed_layer")
    return (
        f"⚠️ Unverified — this answer did not pass the governed layer "
        f"({refused_by}/{failed_layer or 'n/a'}); treat it with caution.\n\n"
    )


def narrate_answer(
    answer: "Answer", question: str, narrator: "AnswerNarrator | None"
) -> "Answer":
    """Re-phrase a delivered answer's text with the LLM narrator.

    The serve graph's dedicated ``narrate`` node calls this so the narrator's model
    call is a first-class graph step (one trace span under the turn), not a side
    call buried inside finalization. Only answers carrying an executed result grid
    are narrated; refusals (no ``result``) and the no-narrator path return the
    answer unchanged, and a narrator failure keeps the deterministic text so a
    model hiccup never turns a governed answer into an error. A graded-delivery
    answer keeps its unverified banner, identical to the deterministic path.
    """
    if narrator is None or answer is None or answer.result is None:
        return answer
    try:
        body = narrator.narrate(question, answer.sql or "", answer.result)
    except Exception:
        return answer
    if answer.semantic_assurance is SemanticAssurance.unverified:
        body = _unverified_prefix(answer.provenance or {}) + body
    return replace(answer, text=body)


def _emit(on_event: "Callable[[dict], None] | None", stage: str, **detail) -> None:
    """Fire a best-effort stage-progress event (no-op without a callback).

    The serve flow stays authoritative: a callback that raises must never turn a
    governed answer into an error, so failures here are swallowed. The payload is
    a small stable dict ``{"stage": ..., **detail}`` that the LangGraph server
    maps to a labeled UI stage (see docs/plans/agent-step-visualization.md). Stages, in
    pipeline order: ``route``, ``refuse_gate``, ``cache_hit``, ``retrieve``,
    ``generate``, ``guardrail``, ``execute``, ``compose``.
    """
    if on_event is None:
        return
    try:
        on_event({"stage": stage, **detail})
    except Exception:
        pass


# Ledger ``verdict`` → the step-event ``status`` the UI renders. Keeps the live
# stream and the final ``governance_ledger`` from drifting (Inv #10).
_LEDGER_STATUS = {
    "pass": "ok",
    "block": "blocked",
    "error": "error",
    "cap": "cap",
    "deny": "blocked",
}


class GovEventStream:
    """Emit the rich agent step-event contract over a raw ``on_event`` callback.

    One instance per turn (call :meth:`reset` at the turn boundary). Stamps a
    monotonic ``seq`` so the frontend can order events, and tags the first event
    of the turn with ``serve_path`` so the UI picks the agent renderer (see
    docs/plans/agent-step-visualization.md). This is the *agent* path's emitter;
    the shared helpers below (:func:`_try_cache_hit`, :func:`_finalize_success`,
    :func:`_finish_unsuccessful`) still accept an ``on_event`` callback and fall
    back to the bare :func:`_emit` legacy ``{stage}`` shape when one is passed,
    but ``analyst.agent`` always passes ``on_event=None`` there and drives this
    emitter instead, so extending one never disturbs the other.

    Best-effort like :func:`_emit`: a callback that raises must never turn a
    governed answer into an error, so failures are swallowed.

    When ``finalize_ctx`` is set, :meth:`final` stamps metadata + appends the
    portable run log (ADR 0004 L5/L6) and returns the stamped ``Answer``.
    """

    def __init__(
        self,
        on_event: "Callable[[dict], None] | None",
        *,
        serve_path: str = "agent",
        finalize_ctx: Any = None,
    ):
        self._on_event = on_event
        self._serve_path = serve_path
        self._seq = 0
        self._started = False
        self._finalize_ctx = finalize_ctx
        self._token_usage_extra: list = []

    def reset(self) -> None:
        """Start a fresh turn: reset the sequence and the serve_path tag."""
        self._seq = 0
        self._started = False
        self._token_usage_extra = []

    def add_token_usage(self, entries: list | None) -> None:
        """Accumulate usage snapshots (agent_core / router) before :meth:`final`."""
        if entries:
            self._token_usage_extra.extend(entries)

    def _emit_event(
        self,
        kind: str,
        step: str,
        status: str,
        *,
        step_id: str | None = None,
        label: str | None = None,
        detail: dict | None = None,
    ) -> None:
        if self._on_event is None:
            return
        payload: dict = {"seq": self._seq, "kind": kind, "step": step, "status": status}
        self._seq += 1
        if step_id is not None:
            payload["id"] = step_id
        if label is not None:
            payload["label"] = label
        payload["detail"] = {k: v for k, v in (detail or {}).items() if v is not None}
        if not self._started:
            payload["serve_path"] = self._serve_path
            self._started = True
        try:
            self._on_event(payload)
        except Exception:
            pass

    def rail(self, step: str, status: str = "ok", *, label: str | None = None, **detail) -> None:
        """A deterministic outer-rail step (route/refuse_gate/cache/assemble)."""
        self._emit_event("rail", step, status, label=label, detail=detail)

    def tool(
        self,
        step: str,
        status: str,
        *,
        step_id: str | None = None,
        label: str | None = None,
        **detail,
    ) -> None:
        """A governed-tool action inside the agent loop (start or resolve)."""
        self._emit_event("tool", step, status, step_id=step_id, label=label, detail=detail)

    def final(self, answer: "Answer", *, step: str = "finalize") -> "Answer":
        """Stamp metadata + emit the terminal answer event; return the stamped Answer."""
        if self._finalize_ctx is not None:
            from .run_log import finalize_and_log

            usage = list(self._finalize_ctx.token_usage or []) + list(
                self._token_usage_extra
            )
            ctx = replace(
                self._finalize_ctx,
                token_usage=usage,
                serve_path=self._serve_path or self._finalize_ctx.serve_path,
            )
            answer = finalize_and_log(answer, ctx=ctx)
        prov = answer.provenance or {}
        status = "refused" if answer.tier is ReliabilityTier.refused else "ok"
        self._emit_event(
            "final",
            step,
            status,
            detail={
                "tier": answer.tier.value,
                "semantic_assurance": answer.semantic_assurance.value,
                "safety_clearance": answer.safety_clearance,
                "tables_used": prov.get("tables_used"),
                "min_join_confidence": prov.get("min_join_confidence"),
                "coverage_best_effort": prov.get("coverage_best_effort"),
            },
        )
        return answer



def _try_cache_hit(
    cache, question, gateway, identity, settings, allowlist, dialect, graph, base_provenance,
    *,
    default_schema: str | None = None,
    narrator: "AnswerNarrator | None" = None,
    on_event: "Callable[[dict], None] | None" = None,
) -> "Answer | None":
    """Serve a semantic-cache hit, or return None to fall through to the pipeline.

    A hit is re-guardrailed (against the licensed tables stored with it) and
    re-executed for freshness (D7). If the re-check fails - a corpus change now
    blocks the cached SQL - or execution errors, this returns None so the full
    pipeline runs instead. Fail-closed: a stale/blocked cached query is never
    served. The reliability stamp is **re-derived** from the current graph (over
    the stored ``tables_used``), identical to a fresh miss, so it never goes stale.
    """
    entry = cache.lookup(question)
    if entry is None:
        return None
    verdict = check(
        entry.sql,
        allowed_columns=set(allowlist.allowed),
        suspect_columns=allowlist.suspect,
        allowed_tables=entry.licensed_tables,
        hard_block_suspect=settings.hard_block_suspect_columns,
        dialect=dialect,
        default_schema=default_schema,
    )
    if not verdict.passed:
        return None
    try:
        result = gateway.execute(entry.sql, identity)
    except Exception:
        return None
    try:
        stamp_plan = plan_joins(graph, set(entry.tables_used))
        join_ids, min_confidence = stamp_plan.join_ids, stamp_plan.min_confidence
    except ValueError:
        join_ids, min_confidence = [], 1.0
    signals = UncertaintySignals(
        low_confidence_join=min_confidence < LOW_CONFIDENCE_JOIN,
        suspect_in_scope=_suspect_in_scope(entry.sql, allowlist.suspect, dialect),
    )
    provenance = {
        **base_provenance,
        "metric_id": entry.metric_id,
        "tables_used": sorted(entry.tables_used),
        "join_ids": join_ids,
        "min_join_confidence": min_confidence,
        "row_count": result.row_count,
        "truncated": result.truncated,
        "cache_hit": True,
    }
    _emit(on_event, "cache_hit", metric_id=entry.metric_id)
    table = _result_table(result)
    text = _answer_text(question, entry.sql, result, table, narrator)
    return assemble(text=text, sql=entry.sql, signals=signals, provenance=provenance, result=table)


def _finish_unsuccessful(
    *,
    settings: "Settings",
    gateway: "Gateway",
    identity: "Identity",
    last_refusal: dict,
    attempts: int,
    base_provenance: dict,
    question: str,
    narrator: "AnswerNarrator | None" = None,
    on_event: "Callable[[dict], None] | None" = None,
    allowlist: "ColumnAllowlist | None" = None,
    dialect: str | None = None,
    default_schema: str | None = None,
) -> "Answer":
    """Hard-refuse safety failures; §6 deliver-and-grade semantic ones when enabled.

    ``allowlist``/``dialect``/``default_schema`` (threaded from ``analyst.agent``)
    let this re-run :func:`check` on the SQL right before executing it, mirroring
    :func:`_try_cache_hit`. Absent (a direct unit call), the semantic-layer
    allowlist gate below already fails closed.
    """
    record = dict(last_refusal)
    escalation = record.pop("escalation", _ESCALATION_NO_COVERAGE)
    refused_by = record.get("refused_by", "no_coverage")
    failed_layer = record.get("failed_layer")
    sql = record.get("sql")
    provenance = {**base_provenance, **record, "attempts": attempts}

    # Graded delivery is an ALLOWLIST: only a SQL that FAILED a curated semantic
    # layer (L4/L5) — which proves it cleared L1/L2/L3 first — is ever re-executed.
    # A refuse-gate hit, an L2/L3 safety/confidentiality block, or anything that
    # never earned a check() verdict (a capped/errored attempt carries
    # ``failed_layer=None``) is a hard refuse and NEVER executed (audit Vuln 2).
    deliverable = failed_layer in _GRADED_DELIVERY_LAYERS
    if not deliverable or not sql or not settings.grade_semantic_failures:
        _emit(on_event, "refuse", refused_by=refused_by, failed_layer=failed_layer)
        return refusal(escalation=escalation, provenance=provenance)

    # Defense-in-depth: re-run the guardrail right before executing, so nothing but
    # check() itself ever authorizes an execution (mirrors _try_cache_hit; never
    # trust the ledger's ``failed_layer`` label). ``allowed_tables=None`` skips L4
    # (the term-scope layer graded delivery exists to forgive), so a genuine L4/L5
    # failure still delivers — but if the SQL now trips a safety/confidentiality
    # layer (L2/L3), or any non-semantic layer, refuse and never execute.
    if allowlist is not None:
        verdict = check(
            sql,
            allowed_columns=set(allowlist.allowed),
            suspect_columns=allowlist.suspect,
            allowed_tables=None,
            hard_block_suspect=settings.hard_block_suspect_columns,
            dialect=dialect,
            default_schema=default_schema,
        )
        recheck_layer = verdict.failed_layer.value if verdict.failed_layer else None
        if not verdict.passed and recheck_layer not in _GRADED_DELIVERY_LAYERS:
            _emit(on_event, "refuse", refused_by=refused_by, failed_layer=recheck_layer)
            return refusal(
                escalation=escalation,
                provenance={**provenance, "graded_delivery_recheck_failed": recheck_layer},
            )

    # §6: deliver the last generated SQL with unverified assurance. Try to
    # execute for a complete answer; if execute fails, still return the SQL so
    # eval can grade it (and the UI can show an unverified payload).
    _emit(
        on_event,
        "graded_delivery",
        refused_by=refused_by,
        failed_layer=failed_layer,
    )
    try:
        result = gateway.execute(sql, identity)
        table = _result_table(result)
        # Deliver the REAL answer (narrated from the executed result), clearly
        # marked unverified. A curated SEMANTIC layer (L4/L5) failed, but reaching
        # one proves the SQL cleared L1/L2/L3 (check() returns on first failure),
        # and the re-check above just re-confirmed it clears safety/confidentiality,
        # so this read-only query is safe to show.
        answer_text = _answer_text(question, sql, result, table, narrator)
        text = _unverified_prefix(provenance) + answer_text
        return graded_delivery(
            sql=sql,
            provenance=provenance,
            result=table,
            text=text,
        )
    except Exception as err:
        provenance = {**provenance, "graded_delivery_execute_error": str(err)}
        return graded_delivery(
            sql=sql,
            provenance=provenance,
            result=None,
            text=(
                f"(unverified) SQL retained after semantic failure "
                f"({refused_by}/{failed_layer or 'n/a'}); execution failed."
            ),
        )


def _finalize_success(
    *, question, graph, generated, result, attempts, base_provenance, dialect, allowlist, licensed,
    cache, narrator: "AnswerNarrator | None" = None, on_event: "Callable[[dict], None] | None" = None,
    coverage_best_effort: bool = False,
    ledger: list | None = None,
) -> "Answer":
    """Stamp + assemble a successful answer, and write back a clean one to the cache.

    The stamp reflects the joins the executed SQL actually needs and whether it
    took a repair to get here (a repaired answer is lineage, not governed). Kept
    here in ``analyst.governance`` (rather than inline in ``analyst.agent``) so the
    stamping logic stays centralized and testable on its own.
    ``ledger`` (optional) attaches the agent governance ledger to provenance (Inv #10).
    """
    try:
        stamp_plan = plan_joins(graph, set(generated.tables_used))
        join_ids, min_confidence = stamp_plan.join_ids, stamp_plan.min_confidence
    except ValueError:
        join_ids, min_confidence = [], 1.0

    signals = UncertaintySignals(
        low_confidence_join=min_confidence < LOW_CONFIDENCE_JOIN,
        suspect_in_scope=_suspect_in_scope(generated.sql, allowlist.suspect, dialect),
        fenced_raw_fallback=coverage_best_effort,
        repaired=attempts > 1 or coverage_best_effort,
    )
    provenance = {
        **base_provenance,
        "metric_id": generated.metric_id,
        "tables_used": sorted(generated.tables_used),
        "join_ids": join_ids,
        "min_join_confidence": min_confidence,
        "row_count": result.row_count,
        "truncated": result.truncated,
        "attempts": attempts,
        "coverage_best_effort": coverage_best_effort,
    }
    if ledger is not None:
        provenance["governance_ledger"] = list(ledger)
    table = _result_table(result)
    _emit(on_event, "compose")
    text = _answer_text(question, generated.sql, result, table, narrator)
    answer = assemble(
        text=text, sql=generated.sql, signals=signals, provenance=provenance, result=table
    )
    # Cache admission gates on the *semantic* axis, never on safety alone: only a
    # ``grounded`` answer (clean run, no uncertainty flag) is written back, so a
    # later hit is always high-assurance. Cache SQL text only, never results (D7).
    if cache is not None and answer.semantic_assurance is SemanticAssurance.grounded:
        cache.put(
            question,
            generated.sql,
            licensed_tables=licensed,
            tables_used=frozenset(generated.tables_used),
            metric_id=generated.metric_id,
        )
    return answer
