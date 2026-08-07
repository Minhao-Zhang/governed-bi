"""Turn-record schema: projection, presence, quotability gates.

Adding a row records and gates a field by default. Gates are declared on fields;
:func:`missing_required` treats ``None`` and unmeasured
:class:`~.quantity.Measured` as absent for :attr:`Absence.never`. Not the knob
register (:mod:`.knobs`).

**There was a fifth column, ``redaction``, and it is gone** (audit §8.1 / §10). Every one
of these rows declared a durable-projection policy, ``redaction_of()`` had **zero callers**
anywhere in the repository, the ``Redaction`` enum was read by nothing, and the ``Sink``
port that promised "every record is redacted before write" had no implementation. What
actually reached disk was ``api/trace_store.append_turn`` writing the question, the answer
and the whole record verbatim, and ``attempt_record`` carrying ``executed_sql`` raw --
verified on a live log holding a full ``generated_sql`` and a ``WHERE`` clause with its
literals.

Deleted rather than wired, deliberately. This is a local-first single-user tool and the log
is the user's own transcript on the user's own disk; the honest thing is to say so. A
declaration with no enforcer is worse than no declaration, because a reader takes the
declaration for the behaviour -- which is what happened here for the whole of v2. If
redaction is ever needed, it needs a threat model first, and the threat model decides the
vocabulary rather than the other way round.
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
    """What a missing or null value means, declared per field.

    All three encode as JSON ``null``; this column distinguishes them.
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
       "which resume attempt served this row. One v1 ladder blended four attempts "
       "at 16/6/6/3 workers into one file with nothing on a row to separate them — "
       "and worker count is exactly what saturates the shared quota that the crash "
       "and degradation gates read"),

    # ── treatment ───────────────────────────────────────────────────────────
    _f("context_hash", Tier.treatment, Absence.not_applicable, Stage.assemble,
       "the delivery gate. Deterministic: a pure function of corpus content and "
       "pipeline decisions. Null on paths that skip assemble, and NEVER the string "
       "'unknown' — v1's sentinel compared equal to itself and let two runs with no "
       "recorded treatment pass comparability",
       gate="context_hash distinct across arms on >= 95% of shared questions where "
            "both arms assembled a context"),
    _f("delivery_hash", Tier.treatment, Absence.not_applicable, Stage.stamp,
       "context plus every tool-delivered body. NOT deterministic — it depends on "
       "which read_body calls the model chose — so it is a diagnostic, never a "
       "gate. It is also the only field that answers whether curated bodies "
       "actually reached the model, which context_hash alone cannot"),
    _f("tool_delivered", Tier.treatment, Absence.not_applicable, Stage.agent_core,
       "call_id -> sha256 of every corpus- or database-derived tool return. Not "
       "just read_body: real database values are the largest source of arm-to-arm "
       "variation in what the model sees. Null when the agent loop did not run"),
    _f("corpus_content_hash", Tier.treatment, Absence.never, Stage.stamp,
       "the corpus IS the treatment"),
    _f("prompt_set_hash", Tier.treatment, Absence.never, Stage.stamp,
       "hashes the prompt TEXT, not the variant id — editing a variant in place "
       "must change the digest, or an edited prompt is indistinguishable from the "
       "one it replaced"),
    _f("knobs_resolved", Tier.treatment, Absence.never, Stage.stamp,
       "the resolved value of every comparability knob. Without it a turn cannot "
       "be joined against the configuration it ran under, and v1's manifest-only "
       "record is why two runs over different corpora on different splits compared "
       "as the same configuration",
       gate="every row in one arm agrees on knobs.resume_drift_keys()"),

    # ── decision ────────────────────────────────────────────────────────────
    _f("facet_hits", Tier.decision, Absence.not_applicable, Stage.route,
       "per facet: asset_id, asset_type, queries, lexical, semantic. v1 recorded "
       "counts only, so no finding could be attributed to an asset and no feedback "
       "loop was possible. Null when the fan-out did not run"),
    _f("facet_channels", Tier.health, Absence.not_applicable, Stage.route,
       "per facet per channel: ran / not_configured / failed, as explicit values "
       "never inferred from scores. If the extractor is rate-limited every facet "
       "falls back to the raw question, the run completes, grades normally, and the "
       "arm quietly IS v1's single-pass retrieval. Null when the fan-out did not "
       "run — which the gate must not read as clean",
       gate="on turns where the fan-out ran, no channel state differs from its "
            "declared expectation; the observed count is published beside the rate"),
    _f("facet_degraded", Tier.decision, Absence.not_applicable, Stage.route,
       "true when some facet ran on fewer channels than FACET_CHANNELS declares — "
       "`register.facets.is_degraded` over the field above, computed once and stamped, "
       "not re-derived by each reader. It had no row here at all, so `project()` could "
       "not write it, `harness.py` read it as `bool(... or False)`, and the degradation "
       "gate published `[pass] facet_channels 0.0000` on an arm with no index. Null when "
       "the fan-out did not run, for the same reason `facet_channels` is. Tier is "
       "`decision` and not `health` deliberately: the quotability condition reading this "
       "is declared on `facet_channels`, one gate over one comparison, and a second gate "
       "declared here would be two verdicts on the same evidence",
       reconstructable=True),
    _f("schema_ranking", Tier.decision, Absence.not_applicable, Stage.route,
       "ALL scored schemas pre-truncation. Without it, 'the gold schema was not a "
       "candidate' and 'it ranked 4th' are the same observation — v1's collapse "
       "published a documented failure bucket at a perfect score over 2030 rows"),
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
       "total record, written every turn including clear. A gate that leaves a "
       "trace only when it fires cannot afterwards be told from a gate that was "
       "never wired up. The rule_id is closed-vocabulary; the detail is free text "
       "and dropped"),
    _f("negative", Tier.decision, Absence.not_applicable, Stage.negative_gate,
       "total record: hit | clear | disabled | error_failed_open. The last must be "
       "countable, and a nullable hit cannot express it. Null only when guard "
       "blocked first",
       gate="no negative_gate error_failed_open"),
    _f("execution", Tier.decision, Absence.never, Stage.stamp,
       "attempts, per-attempt verdict layer, terminal, guardrail_errors "
       "(ADR 0006 section 12). Total, including the 'no SQL was attempted' case"),
    _f("guardrail_errors", Tier.health, Absence.never, Stage.stamp,
       "exceptions swallowed by check(). Zero is a measured zero, including on a "
       "turn where check never ran. Without the counter, a NameError there turns "
       "every turn in an arm into a refusal while crash_rate stays 0, every "
       "register key is present, and the run reads as quotable",
       gate="guardrail_errors == 0"),

    # ── outcome ─────────────────────────────────────────────────────────────
    _f("outcome", Tier.outcome, Absence.never, Stage.stamp,
       "stamped at the source; classify_row prefers it over re-derivation so a row "
       "scored under a newer classifier is not re-derived under an older one",
       gate="no turn classified crashed"),
    _f("terminal_reason", Tier.outcome, Absence.not_applicable, Stage.stamp,
       "WHY a turn declined: missing_join_path / no_schema_matched / over_connect_bounds "
       "/ no_sql are four different engineering problems and `outcome: declined` is one "
       "value for all of them. It lived in graph state only, so the reason never reached "
       "the record and a declined turn was unattributable after the fact -- which made "
       "'routing found nothing' and 'the join graph is disconnected' the same row"),
    _f("failed_stage", Tier.outcome, Absence.not_applicable, Stage.stamp,
       "null when the turn did not fail"),
    _f("error_type", Tier.outcome, Absence.not_applicable, Stage.stamp,
       "exception CLASS only. Tracebacks echo SQL and row values"),
    _f("generated_sql", Tier.outcome, Absence.not_applicable, Stage.stamp,
       "null when no SQL was produced, which is not the same as empty. **On an answered turn "
       "this is the statement the engine SENT** — canonicalised, quoted and row-limited, read "
       "from the ledger's `executed_sql`. On a refused or capped turn nothing was sent, so it "
       "falls back to the last statement the model *proposed*, which may not execute at all: a "
       "consumer that re-runs this field must gate on `outcome == 'answered'`, or it reports a "
       "refusal as a broken statement — which is how 14 capped turns looked like an engine "
       "defect on 2026-08-04"),
    # `final_sql_source` was here and is gone (audit §10): zero writers, permanently null. It
    # was to record *which rule* selected the final statement -- and there is one rule, in
    # `agent_core._last_executed_sql`: the last statement the answering path executed. A field
    # naming a choice between alternatives that do not exist records nothing. The v1 defect its
    # justification cited (taking the last *passing* query, so a sanity check after the real
    # answer delivered the count) is prevented by that function reading the ledger rather than
    # the tool arguments, which is a mechanism and not a field.

    # ── cost ────────────────────────────────────────────────────────────────
    _f("usage", Tier.cost, Absence.never, Stage.stamp,
       "one record per model call including facet and rewrite calls. An empty list "
       "is a measured zero — a guard-blocked turn made no model calls. v1 could not "
       "price the curator at all, the largest unpriced line in a run"),
    _f("cache_read_tokens", Tier.cost, Absence.not_measured, Stage.stamp,
       "null means the provider did not report it, NOT zero"),
    _f("cache_write_tokens", Tier.cost, Absence.not_measured, Stage.stamp,
       "billed at 1.25x and not modelled in v1 at all"),
    _f("latency_sec", Tier.cost, Absence.not_measured, Stage.stamp, "wall clock"),

    # ── health ──────────────────────────────────────────────────────────────
    _f("n_re_served", Tier.health, Absence.never, Stage.stamp,
       "re-serving a crashed turn resamples that draw AFTER failure, laundering "
       "crash_rate back to zero and conditioning the arm's EX on a re-roll",
       gate="n_re_served == 0"),
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

    Only :attr:`Absence.never` fields are checked; null is legal for the other
    two absence kinds.
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
