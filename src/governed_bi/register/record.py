"""Turn-record schema: projection, presence, quotability gates, redaction.

Adding a row records, gates, and redacts a field by default. Gates are declared
on fields; :func:`missing_required` treats ``None`` and unmeasured
:class:`~.quantity.Measured` as absent for :attr:`Absence.never`. Not the knob
register (:mod:`.knobs`).
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
    "Redaction",
    "RecordField",
    "RECORD_REGISTER",
    "GATE_CONDITIONS",
    "record_keys",
    "required_keys",
    "gate_keys",
    "live_capture_keys",
    "redaction_of",
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
    #: How the turn ended. ``stages.classify_row`` reads these.
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


class Redaction(str, Enum):
    """Durable-projection policy per field (ADR 0006 §11). Deny by shape."""

    #: Enum or bounded identifier. Kept verbatim.
    closed_vocabulary = "closed_vocabulary"
    #: Numeric. Kept verbatim.
    numeric = "numeric"
    #: Asset ids or hashes. Kept — they name things without quoting them.
    reference = "reference"
    #: SQL: digest plus literal-elided fingerprint, never raw text.
    statement = "statement"
    #: Free text. Dropped.
    free_text = "free_text"

@dataclass(frozen=True, slots=True)
class RecordField:
    """One declared field of a turn record."""

    name: str
    tier: Tier
    absence: Absence
    redaction: Redaction

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
    redaction: Redaction,
    owner: Stage,
    why: str,
    *,
    gate: str | None = None,
    reconstructable: bool = False,
) -> RecordField:
    return RecordField(
        name, tier, absence, redaction, owner, why, gate=gate, reconstructable=reconstructable
    )


_ID = Redaction.closed_vocabulary
_N = Redaction.numeric
_REF = Redaction.reference

#: The register. **Adding a row here is the only way to add a recorded field.**
RECORD_REGISTER: tuple[RecordField, ...] = (
    # ── identity ────────────────────────────────────────────────────────────
    _f("run_id", Tier.identity, Absence.never, _ID, Stage.stamp,
       "joins a turn to its run", reconstructable=True),
    _f("turn_id", Tier.identity, Absence.never, _ID, Stage.stamp,
       "the upsert key. Derived per invoke, never at build time: a reused graph "
       "deriving it once wrote every question to the same id and the idempotent "
       "upsert overwrote each row with the next"),
    _f("thread_id", Tier.identity, Absence.never, _ID, Stage.stamp, "multi-turn grouping"),
    _f("question_id", Tier.identity, Absence.never, _ID, Stage.stamp, "joins to gold"),
    _f("db_id", Tier.identity, Absence.never, _ID, Stage.stamp,
       "schema-qualified. A pooled corpus repeats table names across schemas, so a "
       "bare name would credit a table from the wrong schema"),
    _f("attempt_id", Tier.identity, Absence.never, _ID, Stage.stamp,
       "which resume attempt served this row. One v1 ladder blended four attempts "
       "at 16/6/6/3 workers into one file with nothing on a row to separate them — "
       "and worker count is exactly what saturates the shared quota that the crash "
       "and degradation gates read"),

    # ── treatment ───────────────────────────────────────────────────────────
    _f("context_hash", Tier.treatment, Absence.not_applicable, _REF, Stage.assemble,
       "the delivery gate. Deterministic: a pure function of corpus content and "
       "pipeline decisions. Null on paths that skip assemble, and NEVER the string "
       "'unknown' — v1's sentinel compared equal to itself and let two runs with no "
       "recorded treatment pass comparability",
       gate="context_hash distinct across arms on >= 95% of shared questions where "
            "both arms assembled a context"),
    _f("delivery_hash", Tier.treatment, Absence.not_applicable, _REF, Stage.stamp,
       "context plus every tool-delivered body. NOT deterministic — it depends on "
       "which read_body calls the model chose — so it is a diagnostic, never a "
       "gate. It is also the only field that answers whether curated bodies "
       "actually reached the model, which context_hash alone cannot"),
    _f("tool_delivered", Tier.treatment, Absence.not_applicable, _REF, Stage.agent_core,
       "call_id -> sha256 of every corpus- or database-derived tool return. Not "
       "just read_body: real database values are the largest source of arm-to-arm "
       "variation in what the model sees. Null when the agent loop did not run"),
    _f("corpus_content_hash", Tier.treatment, Absence.never, _REF, Stage.stamp,
       "the corpus IS the treatment"),
    _f("prompt_set_hash", Tier.treatment, Absence.never, _REF, Stage.stamp,
       "hashes the prompt TEXT, not the variant id — editing a variant in place "
       "must change the digest, or an edited prompt is indistinguishable from the "
       "one it replaced"),
    _f("knobs_resolved", Tier.treatment, Absence.never, _ID, Stage.stamp,
       "the resolved value of every comparability knob. Without it a turn cannot "
       "be joined against the configuration it ran under, and v1's manifest-only "
       "record is why two runs over different corpora on different splits compared "
       "as the same configuration"),

    # ── decision ────────────────────────────────────────────────────────────
    _f("facet_hits", Tier.decision, Absence.not_applicable, _REF, Stage.route,
       "per facet: asset_id, asset_type, queries, lexical, semantic. v1 recorded "
       "counts only, so no finding could be attributed to an asset and no feedback "
       "loop was possible. Null when the fan-out did not run"),
    _f("facet_channels", Tier.health, Absence.not_applicable, _ID, Stage.route,
       "per facet per channel: ran / not_configured / failed, as explicit values "
       "never inferred from scores. If the extractor is rate-limited every facet "
       "falls back to the raw question, the run completes, grades normally, and the "
       "arm quietly IS v1's single-pass retrieval. Null when the fan-out did not "
       "run — which the gate must not read as clean",
       gate="on turns where the fan-out ran, no channel state differs from its "
            "declared expectation; the observed count is published beside the rate"),
    _f("facet_degraded", Tier.decision, Absence.not_applicable, _ID, Stage.route,
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
    _f("schema_ranking", Tier.decision, Absence.not_applicable, _REF, Stage.route,
       "ALL scored schemas pre-truncation. Without it, 'the gold schema was not a "
       "candidate' and 'it ranked 4th' are the same observation — v1's collapse "
       "published a documented failure bucket at a perfect score over 2030 rows"),
    _f("schemas", Tier.decision, Absence.not_applicable, _ID, Stage.route,
       "the selected top-N. The only falsifiable check on the routing formula"),
    _f("pulled_in", Tier.decision, Absence.not_applicable, _REF, Stage.connect,
       "asset_id -> resolve|connect. Answers what expand_hops is worth"),
    _f("licensed", Tier.decision, Absence.not_applicable, _REF, Stage.connect,
       "what the turn may reach. Deliberately not the post-budget table list: "
       "budgets shape what is rendered, licensing what is reachable"),
    _f("crossings", Tier.decision, Absence.not_applicable, _REF, Stage.connect,
       "cross-schema Steiner points, so 'how often does connect cross, and what is "
       "accuracy on those turns' is a query rather than a guess"),
    _f("lexical_coverage", Tier.decision, Absence.not_measured, _N, Stage.route,
       "feeds weak_retrieval. With an embedder every asset scores above zero, so an "
       "out-of-corpus question still returns top_k tables and a clean run stamps "
       "confidence"),
    _f("rewrite", Tier.decision, Absence.not_applicable, Redaction.free_text, Stage.rewrite,
       "before / after / outcome. Null means the node did not run (single turn); "
       "'failed' is a distinct outcome value, because a nullable string cannot tell "
       "those apart and any rate built on it reads 0.0 on a run where every rewrite "
       "failed. Free text: it holds the user's question"),
    _f("guard", Tier.decision, Absence.never, _ID, Stage.guard,
       "total record, written every turn including clear. A gate that leaves a "
       "trace only when it fires cannot afterwards be told from a gate that was "
       "never wired up. The rule_id is closed-vocabulary; the detail is free text "
       "and dropped"),
    _f("negative", Tier.decision, Absence.not_applicable, _ID, Stage.negative_gate,
       "total record: hit | clear | disabled | error_failed_open. The last must be "
       "countable, and a nullable hit cannot express it. Null only when guard "
       "blocked first",
       gate="no negative_gate error_failed_open"),
    _f("execution", Tier.decision, Absence.never, _ID, Stage.stamp,
       "attempts, per-attempt verdict layer, terminal, guardrail_errors "
       "(ADR 0006 section 12). Total, including the 'no SQL was attempted' case"),
    _f("guardrail_errors", Tier.health, Absence.never, _N, Stage.stamp,
       "exceptions swallowed by check(). Zero is a measured zero, including on a "
       "turn where check never ran. Without the counter, a NameError there turns "
       "every turn in an arm into a refusal while crash_rate stays 0, every "
       "register key is present, and the run reads as quotable",
       gate="guardrail_errors == 0"),

    # ── outcome ─────────────────────────────────────────────────────────────
    _f("outcome", Tier.outcome, Absence.never, _ID, Stage.stamp,
       "stamped at the source; classify_row prefers it over re-derivation so a row "
       "scored under a newer classifier is not re-derived under an older one",
       gate="no turn classified crashed"),
    _f("terminal_reason", Tier.outcome, Absence.not_applicable, _ID, Stage.stamp,
       "WHY a turn declined: missing_join_path / no_schema_matched / over_connect_bounds "
       "/ no_sql are four different engineering problems and `outcome: declined` is one "
       "value for all of them. It lived in graph state only, so the reason never reached "
       "the record and a declined turn was unattributable after the fact -- which made "
       "'routing found nothing' and 'the join graph is disconnected' the same row"),
    _f("failed_stage", Tier.outcome, Absence.not_applicable, _ID, Stage.stamp,
       "null when the turn did not fail"),
    _f("error_type", Tier.outcome, Absence.not_applicable, _ID, Stage.stamp,
       "exception CLASS only. Tracebacks echo SQL and row values"),
    _f("generated_sql", Tier.outcome, Absence.not_applicable, Redaction.statement,
       Stage.stamp,
       "null when no SQL was produced, which is not the same as empty. **On an answered turn "
       "this is the statement the engine SENT** — canonicalised, quoted and row-limited, read "
       "from the ledger's `executed_sql`. On a refused or capped turn nothing was sent, so it "
       "falls back to the last statement the model *proposed*, which may not execute at all: a "
       "consumer that re-runs this field must gate on `outcome == 'answered'`, or it reports a "
       "refusal as a broken statement — which is how 14 capped turns looked like an engine "
       "defect on 2026-08-04"),
    _f("final_sql_source", Tier.outcome, Absence.not_applicable, _ID, Stage.stamp,
       "which rule selected it. v1 took the last passing query, so a turn that ran "
       "a sanity check after its real answer delivered the count while the correct "
       "query sat earlier in the same ledger"),

    # ── cost ────────────────────────────────────────────────────────────────
    _f("usage", Tier.cost, Absence.never, _N, Stage.stamp,
       "one record per model call including facet and rewrite calls. An empty list "
       "is a measured zero — a guard-blocked turn made no model calls. v1 could not "
       "price the curator at all, the largest unpriced line in a run"),
    _f("cache_read_tokens", Tier.cost, Absence.not_measured, _N, Stage.stamp,
       "null means the provider did not report it, NOT zero"),
    _f("cache_write_tokens", Tier.cost, Absence.not_measured, _N, Stage.stamp,
       "billed at 1.25x and not modelled in v1 at all"),
    _f("latency_sec", Tier.cost, Absence.not_measured, _N, Stage.stamp, "wall clock"),

    # ── health ──────────────────────────────────────────────────────────────
    _f("n_re_served", Tier.health, Absence.never, _N, Stage.stamp,
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


def redaction_of(name: str) -> Redaction:
    """Durable-projection policy for one field. Raises ``KeyError`` if undeclared."""
    for f in RECORD_REGISTER:
        if f.name == name:
            return f.redaction
    raise KeyError(f"{name!r} is not a declared record field")


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
