"""Turn-record schema: projection, presence, quotability gates.

Adding a row records and gates a field by default. Gates are declared on fields;
:func:`missing_required` treats ``None`` and unmeasured
:class:`~.quantity.Measured` as absent for :attr:`Absence.never`. Not the knob
register (:mod:`.knobs`).

A fifth column, ``redaction``, is gone (audit §8.1/§10): nothing enforced it, and
``api/trace_store.append_turn`` wrote the question, the answer and ``executed_sql``
verbatim. Records here are unredacted by design -- this is a local-first single-user
tool and the log is the user's own transcript. A redaction vocabulary needs a threat
model first; a declaration with no enforcer reads as behaviour.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Mapping

from .quantity import Measured
from .stages import FACET_STAGES, Stage

__all__ = [
    "Tier",
    "Absence",
    "RecordField",
    "RECORD_REGISTER",
    "GATE_CONDITIONS",
    "record_keys",
    "required_keys",
    "gate_keys",
    "live_capture_keys",
    "missing_required",
    "undeclared_keys",
    "project",
]


class Tier(str, Enum):
    """Why a field is recorded, which decides how a reader may use it."""

    #: Identifies the turn or the run. A comparison joins on these.
    identity = "identity"
    #: The treatment. The delivery gate reads these.
    treatment = "treatment"
    #: Retrieval and governance decisions. Attribution reads these.
    decision = "decision"
    #: How the turn ended. ``stages.classify_outcome`` decides it.
    outcome = "outcome"
    #: Cost and latency.
    cost = "cost"
    #: Degradation counters. Every health field must feed a quotability gate
    #: (enforced at import).
    health = "health"


class Absence(str, Enum):
    """What a missing or null value means, declared per field. All three encode as
    JSON ``null``; this column is the only thing distinguishing them.
    """

    #: Always written with a real value on every terminal path. ``None`` fails
    #: the presence test.
    never = "never"
    #: ``None`` means the producing stage did not run (e.g. fan-out skipped).
    not_applicable = "not_applicable"
    #: ``None`` means the provider or upstream did not report it.
    not_measured = "not_measured"


@dataclass(frozen=True, slots=True)
class RecordField:
    """One declared field of a turn record."""

    name: str
    tier: Tier
    absence: Absence

    #: Stage after which this value is final (last contributing stage).
    owner: Stage

    why: str

    #: Quotability precondition this field feeds, if any.
    gate: str | None = None

    #: Recoverable from other artifacts after the run; otherwise capture live.
    reconstructable: bool = False

def _f(
    name: str,
    tier: Tier,
    absence: Absence,
    owner: Stage,
    why: str,
    *,
    gate: str | None = None,
    reconstructable: bool = False,
) -> RecordField:
    return RecordField(name, tier, absence, owner, why, gate=gate, reconstructable=reconstructable)


#: The register. **Adding a row here is the only way to add a recorded field.**
RECORD_REGISTER: tuple[RecordField, ...] = (
    # ── identity ────────────────────────────────────────────────────────────
    _f("run_id", Tier.identity, Absence.never, Stage.stamp,
       "joins a turn to its run", reconstructable=True),
    _f("turn_id", Tier.identity, Absence.never, Stage.stamp,
       "the upsert key. Derived per invoke, never at build time: a reused graph "
       "deriving it once wrote every question to the same id and the idempotent "
       "upsert overwrote each row with the next"),
    _f("thread_id", Tier.identity, Absence.never, Stage.stamp, "multi-turn grouping"),
    _f("question_id", Tier.identity, Absence.never, Stage.stamp, "joins to gold"),
    _f("db_id", Tier.identity, Absence.never, Stage.stamp,
       "schema-qualified. A pooled corpus repeats table names across schemas, so a "
       "bare name would credit a table from the wrong schema"),
    _f("attempt_id", Tier.identity, Absence.never, Stage.stamp,
       "which resume attempt served this row. Without it a ladder blends attempts at "
       "different worker counts into one file, and worker count saturates the shared "
       "quota the crash and degradation gates read"),

    # ── treatment ───────────────────────────────────────────────────────────
    _f("evicted", Tier.treatment, Absence.not_applicable, Stage.assemble,
       "what the char budget dropped before the model saw it: bodies, whole tables, and by "
       "how much the block still overran. Null means the block fit, which the assemble node "
       "distinguishes from an empty mapping. Treatment, not outcome, because it changes what "
       "was served: `table_coverage` is computed over `licensed` and is therefore a LICENSING "
       "figure -- a table can be routed, licensed, counted as covered, and then evicted here. "
       "First measured on the 2026-08-09 v3-fold arm: the budget bit on 19 of 1 351 turns "
       "(1.4%), dropping bodies only and never a whole table. So the 80 000-char budget is "
       "NOT the binding constraint an offline reconstruction had suggested -- that estimate "
       "built the context from every licensed table's every column and ignored the per-type "
       "budgets pass two applies. `DeliveryTracker.merge_into` destroyed this field mid-turn "
       "for its whole life before it was carried, which is why no earlier arm can report it"),

    _f("context_hash", Tier.treatment, Absence.not_applicable, Stage.assemble,
       "the delivery gate. Deterministic: a pure function of corpus content and "
       "pipeline decisions. Null on paths that skip assemble, and NEVER the string "
       "'unknown' — v1's sentinel compared equal to itself and let two runs with no "
       "recorded treatment pass comparability",
       gate="both arms recorded a context_hash on every shared question. Distinctness was "
            "the condition until audit D9: it measured retrieval nondeterminism rather than "
            "treatment change and passed at 0.9993 on a seed-only pair, so the treatment "
            "judgement moved to knobs_resolved"),
    _f("delivery_hash", Tier.treatment, Absence.not_applicable, Stage.stamp,
       "context plus every tool-delivered body. NOT deterministic -- it depends on "
       "which read_body calls the model chose -- so it is a diagnostic, never a gate. "
       "The only field that answers whether curated bodies reached the model"),
    _f("tool_delivered", Tier.treatment, Absence.not_applicable, Stage.agent_core,
       "call_id -> sha256 of every corpus- or database-derived tool return. Not just "
       "read_body: real database values are the largest source of arm-to-arm variation "
       "in what the model sees. Null when the agent loop did not run"),
    _f("corpus_content_hash", Tier.treatment, Absence.never, Stage.stamp,
       "the corpus IS the treatment. Gated from 2026-08-10 (audit D7): the sentence above was "
       "written and nothing enforced it, so two arms measured over two different corpora passed "
       "all six gates and compared as one treatment. The two runs the power analysis designates "
       "as its null replicate carry this field as null on all 1351 rows, which is how a corpus "
       "identity of `unknown` passing a comparability gate happened a second time",
       gate="every row in one arm carries the same non-null corpus_content_hash"),
    _f("prompt_set_hash", Tier.treatment, Absence.never, Stage.stamp,
       "hashes the prompt TEXT, not the variant id — editing a variant in place "
       "must change the digest, or an edited prompt is indistinguishable from the "
       "one it replaced"),
    _f("knobs_resolved", Tier.treatment, Absence.never, Stage.stamp,
       "the resolved value of every comparability knob. Without it a turn cannot be "
       "joined against the configuration it ran under, and a manifest-only record lets "
       "two runs over different corpora on different splits compare as one",
       gate="every row in one arm agrees on knobs.resume_drift_keys(); across two arms the "
            "declared treatment differs and every other knobs.comparability_keys() entry is "
            "recorded on both and equal"),

    # ── decision ────────────────────────────────────────────────────────────
    _f("facet_hits", Tier.decision, Absence.not_applicable, Stage.route,
       "per facet: asset_id, asset_type, queries, lexical, semantic. Counts alone "
       "cannot attribute a finding to an asset, so no feedback loop is possible. Null "
       "when the fan-out did not run"),
    _f("facet_channels", Tier.health, Absence.not_applicable, Stage.route,
       "per facet per channel: ran / not_configured / failed, as explicit values never "
       "inferred from scores. If the extractor is rate-limited every facet falls back "
       "to the raw question, the run completes and grades normally, and the arm quietly "
       "IS single-pass retrieval. Null when the fan-out did not run -- which the gate "
       "must not read as clean",
       gate="on turns where the fan-out ran, no channel state differs from its "
            "declared expectation; the observed count is published beside the rate"),
    _f("facet_degraded", Tier.decision, Absence.not_applicable, Stage.route,
       "true when some facet ran on fewer channels than FACET_CHANNELS declares -- "
       "`register.facets.is_degraded` over the field above, computed once and stamped "
       "rather than re-derived by each reader. Unregistered, `project()` cannot write "
       "it and the degradation gate published `[pass] facet_channels 0.0000` on an arm "
       "with no index. Null when the fan-out did not run. Tier is `decision`, not "
       "`health`: the quotability condition over this evidence is declared on "
       "`facet_channels`, and a second gate here would be two verdicts on one comparison",
       reconstructable=True),
    _f("schema_ranking", Tier.decision, Absence.not_applicable, Stage.route,
       "ALL scored schemas pre-truncation. Without it, 'the gold schema was not a "
       "candidate' and 'it ranked 4th' are the same observation, which published a "
       "documented failure bucket at a perfect score over 2030 rows"),
    _f("schemas", Tier.decision, Absence.not_applicable, Stage.route,
       "the selected top-N. The only falsifiable check on the routing formula"),
    _f("pulled_in", Tier.decision, Absence.not_applicable, Stage.connect,
       "asset_id -> resolve|connect. Answers what expand_hops is worth"),
    _f("licensed", Tier.decision, Absence.not_applicable, Stage.connect,
       "what the turn may reach. Deliberately not the post-budget table list: "
       "budgets shape what is rendered, licensing what is reachable"),
    _f("crossings", Tier.decision, Absence.not_applicable, Stage.connect,
       "cross-schema Steiner points, so 'how often does connect cross, and what is "
       "accuracy on those turns' is a query rather than a guess"),
    _f("lexical_coverage", Tier.decision, Absence.not_measured, Stage.route,
       "feeds weak_retrieval. With an embedder every asset scores above zero, so an "
       "out-of-corpus question still returns top_k tables and a clean run stamps "
       "confidence"),
    _f("rewrite", Tier.decision, Absence.not_applicable, Stage.rewrite,
       "before / after / outcome. Null means the node did not run (single turn); "
       "'failed' is a distinct outcome value, because a nullable string cannot tell "
       "those apart and any rate built on it reads 0.0 on a run where every rewrite "
       "failed. Free text: it holds the user's question"),
    _f("guard", Tier.decision, Absence.never, Stage.guard,
       "total record, written every turn including clear. A gate that leaves a trace "
       "only when it fires cannot afterwards be told from one never wired up. The "
       "rule_id is closed-vocabulary; the detail is free text and dropped"),
    _f("negative", Tier.decision, Absence.not_applicable, Stage.negative_gate,
       "total record: hit | clear | disabled | error_failed_open. The last must be "
       "countable, and a nullable hit cannot express it. Null only when guard "
       "blocked first",
       gate="no negative_gate error_failed_open"),
    _f("execution", Tier.decision, Absence.never, Stage.stamp,
       "attempts, per-attempt verdict layer, terminal, guardrail_errors "
       "(ADR 0006 section 12). Total, including the 'no SQL was attempted' case"),
    _f("guardrail_errors", Tier.health, Absence.never, Stage.stamp,
       "exceptions swallowed by check(). Zero is a measured zero, including on a turn "
       "where check never ran. Without the counter, a NameError there turns every turn "
       "in an arm into a refusal while crash_rate stays 0 and the run reads as quotable",
       gate="guardrail_errors == 0"),

    # ── outcome ─────────────────────────────────────────────────────────────
    _f("outcome", Tier.outcome, Absence.never, Stage.stamp,
       "stamped at the source; classify_row prefers it over re-derivation so a row "
       "scored under a newer classifier is not re-derived under an older one",
       gate="no turn classified crashed"),
    _f("terminal_reason", Tier.outcome, Absence.not_applicable, Stage.stamp,
       "WHY a turn declined: missing_join_path / no_schema_matched / "
       "over_connect_bounds / no_sql are four different engineering problems and "
       "`outcome: declined` is one value for all of them. Held only in graph state, "
       "'routing found nothing' and 'the join graph is disconnected' are the same row"),
    _f("failed_stage", Tier.outcome, Absence.not_applicable, Stage.stamp,
       "null when the turn did not fail"),
    _f("error_type", Tier.outcome, Absence.not_applicable, Stage.stamp,
       "exception CLASS only. Tracebacks echo SQL and row values"),
    _f("generated_sql", Tier.outcome, Absence.not_applicable, Stage.stamp,
       "null when no SQL was produced, which is not the same as empty. **On an answered "
       "turn this is the statement the engine SENT** -- canonicalised, quoted and "
       "row-limited, read from the ledger's `executed_sql`. On a refused turn nothing "
       "was sent, so it falls back to the last statement the model *proposed*, which "
       "may not execute at all; a capped turn is either. A consumer that re-runs this "
       "field must gate on `outcome == 'answered'`, or it reports a refusal as a broken "
       "statement -- which is how 14 capped turns looked like an engine defect on "
       "2026-08-04"),
    # `final_sql_source` was here and is gone (audit §10): zero writers, permanently null.
    # There is one rule for selecting the final statement -- `agent_core._last_executed_sql`
    # reads the ledger, not the tool arguments -- so a field naming a choice between
    # alternatives that do not exist records nothing.

    _f("reflect_verdict", Tier.decision, Absence.not_measured, Stage.reflect,
       "what the post-hoc observer made of the turn: {verdict, reason, model, "
       "prompt_sha256, signals}, verdict one of answered / wrong / unsure. **Null means "
       "reflection did not run** -- knob off, no model wired, no statement produced, or "
       "the turn ended before the node -- and never 'the turn looked fine'. Absence is "
       "not_measured for that reason, and a judge that ran and could not decide writes "
       "verdict: null beside why_unmeasured rather than a label.\n"
       "**No gate, deliberately:** this is a model's opinion whose agreement with gold "
       "is unmeasured, and gating on an uncalibrated judge refuses runs for a reason "
       "nobody can defend. tools/score_reflector.py decides whether it earns one.\n"
       "Tier is decision, not outcome: `outcome` is the stamped fact of how the turn "
       "ended, and a guess about whether that fact is right is evidence for attribution "
       "-- filing them together would make this field the answer key it must never be"),

    # ── cost ────────────────────────────────────────────────────────────────
    _f("usage", Tier.cost, Absence.never, Stage.stamp,
       "one record per model call including facet and rewrite calls. An empty list is "
       "a measured zero -- a guard-blocked turn made no model calls"),
    _f("cache_read_tokens", Tier.cost, Absence.not_measured, Stage.stamp,
       "null means the provider did not report it, NOT zero"),
    _f("cache_write_tokens", Tier.cost, Absence.not_measured, Stage.stamp,
       "billed at 1.25x, so it cannot be folded into the input count"),
    _f("latency_sec", Tier.cost, Absence.not_measured, Stage.stamp, "wall clock"),

    # ── frozen witness (not health: health fields must gate quotability) ────
    _f("n_re_served", Tier.decision, Absence.never, Stage.stamp,
       "always 0 on every production path: Session.turn seeds it and nothing increments. "
       "Retained so historical rows and the schema stay comparable. It was meant to catch "
       "LangGraph node RetryPolicy re-serving a crashed draw; that policy is banned, and "
       "provider SDK retries (llm_max_retries) are invisible here. Demoted from Tier.health "
       "so it cannot sit as an always-pass quotability gate"),
)

#: Quotability preconditions derived from the register. Refuse; do not warn.
GATE_CONDITIONS: Mapping[str, str] = {
    f.name: f.gate for f in RECORD_REGISTER if f.gate is not None
}


def record_keys() -> frozenset[str]:
    """Every declared field name."""
    return frozenset(f.name for f in RECORD_REGISTER)


def required_keys() -> frozenset[str]:
    """Fields that must carry a real value on every terminal path."""
    return frozenset(f.name for f in RECORD_REGISTER if f.absence is Absence.never)


def gate_keys() -> frozenset[str]:
    """Fields a quotability precondition reads. Gates are declared on fields."""
    return frozenset(GATE_CONDITIONS)


def live_capture_keys() -> frozenset[str]:
    """Fields that must be captured at production time or are lost."""
    return frozenset(f.name for f in RECORD_REGISTER if not f.reconstructable)


def missing_required(record: Mapping[str, Any]) -> frozenset[str]:
    """Required keys absent, null, or an unmeasured :class:`~.quantity.Measured`.

    Only :attr:`Absence.never` fields are checked; null is legal for the other two.
    """
    out: set[str] = set()
    for f in RECORD_REGISTER:
        if f.absence is not Absence.never:
            continue
        if f.name not in record:
            out.add(f.name)
            continue
        value = record[f.name]
        if value is None:
            out.add(f.name)
        elif isinstance(value, Measured) and not value.is_measured:
            out.add(f.name)
    return frozenset(out)


def undeclared_keys(record: Mapping[str, Any]) -> frozenset[str]:
    """Keys in ``record`` the register does not declare."""
    return frozenset(record) - record_keys()


def project(
    state: Mapping[str, Any], *, extract: Callable[[Mapping[str, Any], str], Any]
) -> dict[str, Any]:
    """Build a turn record from serve state. ``None`` is written, not omitted.

    ``extract`` is injected so this module does not import serve types.
    """
    return {f.name: extract(state, f.name) for f in RECORD_REGISTER}


def _assert_register_is_coherent() -> None:
    """Import-time: unique names, Stage owners, every health field gated, no facet owners."""
    names = [f.name for f in RECORD_REGISTER]
    dupes = sorted({n for n in names if names.count(n) > 1})
    if dupes:  # pragma: no cover - import-time guard
        raise AssertionError(f"duplicate record fields: {dupes}")

    bad_owner = [f.name for f in RECORD_REGISTER if not isinstance(f.owner, Stage)]
    if bad_owner:  # pragma: no cover - import-time guard
        raise AssertionError(f"fields whose owner is not a Stage: {bad_owner}")

    ungated_health = sorted(
        f.name for f in RECORD_REGISTER if f.tier is Tier.health and f.gate is None
    )
    if ungated_health:  # pragma: no cover - import-time guard
        raise AssertionError(
            "health-tier fields with no gate reading them: "
            f"{ungated_health}. Every health field must be a quotability input."
        )

    per_facet = sorted(f.name for f in RECORD_REGISTER if f.owner in FACET_STAGES)
    if per_facet:  # pragma: no cover - import-time guard
        raise AssertionError(
            f"fields owned by a facet stage: {per_facet}. `owner` is the stage after "
            "which the value is final; facet evidence is finalised by `route`."
        )


_assert_register_is_coherent()
