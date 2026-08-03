"""The record register: one declaration of what every served turn records.

Four artifacts derive from this one table — the recorded projection, the presence
test, the quotability preconditions, and the durable redaction policy. Adding a
row makes a field recorded, gated and redacted by default. That is the design, and
it exists because every hand-maintained field list in v1 eventually produced an
incident:

* The provenance relay between runtime and measurement was an allow-list that
  never named ``schema_route_channel`` or ``schema_route_degraded``, so two fields
  existed **for a year** and reached no artifact.
* ``COMPARABILITY_KEYS`` derived correctly from the knob list while the ledger
  *record* was built from a hand-written subset — eight gates dead, because an
  absent key cannot make a diff and this system's own rule reads absence as
  agreement.
* Two sinks for one record with **different** redaction policies, and the
  anonymously-reachable one used the weaker.

**How a gate is prevented from reading a field nothing writes.** The gate is
declared *on the field*. So :func:`gate_keys` cannot name an undeclared key by
construction, and the ``health`` tier — whose definition is "every one of these is
a quotability input" — is checked against that at import. That check caught a real
omission on its first run: ``schema_route_degraded``, carried over from v1 as
``health`` with no gate reading it, which is *verbatim* the v1 incident.

**How "written as null" is prevented from passing the presence test.**
:func:`project` writes every declared key, so a producer cannot omit one — but
that alone makes the presence test a rubber stamp, because a record of twenty
nulls has every key. So :func:`missing_required` treats ``None`` as absent for a
field declared :attr:`Absence.never`. The register is what makes this legible: for
a ``never`` field ``None`` is a bug, and for the other two it is a value whose
meaning is declared. That is the whole point of having the column.

**And an unmeasured** :class:`~.quantity.Measured` **counts as absent as well.**
Introducing that type reopened the same hole a third time:
``Measured.unmeasured("provider reported no token count")`` is not ``None``, so a
null check alone passed a required field that was carrying an explicit
non-measurement. The value was honest; the gate reading it was not. This module
imports :class:`~.quantity.Measured` to recognise that, which is the concrete reason
that type lives in this layer — a presence test cannot check a type it cannot
import.

**What this register is not.** Not the *knob* register (:mod:`.knobs`).

.. code-block:: text

    record register  ->  what every turn records         ->  presence test
    knob register    ->  what the run was configured to  ->  comparability keys
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
    #: The treatment. The delivery gate reads these; an arm comparison is void
    #: without them.
    treatment = "treatment"
    #: What the retrieval and governance machinery decided. Attribution reads these.
    decision = "decision"
    #: How the turn ended. ``stages.classify_row`` reads these.
    outcome = "outcome"
    #: Cost and latency.
    cost = "cost"
    #: Degradation counters. **Every one of these is a quotability input** — a
    #: counter no gate reads is the defect this register exists to prevent, and
    #: it is enforced at import.
    health = "health"


class Absence(str, Enum):
    """What a missing or null value means, declared per field.

    The most-repeated defect in v1, at least 25 independent recurrences, was
    conflating "not measured" with "measured zero":

    .. code-block:: python

        len(ledger or [])       # "no ledger recorded" -> "empty ledger"
        round(x or 0.0, n)      # unrecorded field -> measured zero
        sum_token_usage([])     # -> a dict of zeros, which priced a run as free
        not r.get(key)          # ABSENT lands in the FALSE stratum
        s.get(k) or 0           # a gate never computed -> passes

    Declaring the meaning per field is how a reader stops guessing, and it is what
    makes ``x or 0`` a lint error rather than a judgement call.

    **On-the-wire encoding.** All three encode as JSON ``null``. They are told
    apart by *this column*, not by a sentinel — which is why the register has to
    be in the reader's hands, and why :func:`missing_required` can be strict about
    ``never`` while saying nothing about the other two.
    """

    #: Always written with a real value, on **every** terminal path including
    #: refusals and crashes. ``None`` here is a bug and fails the presence test.
    never = "never"
    #: ``None`` means the producing stage did not run. A value, not a gap.
    #:
    #: This is the correct declaration for anything a refusal path skips: a
    #: guard-blocked turn funnels through ``stamp`` without ever reaching the
    #: facets, so demanding a value would force an empty-collection encoding —
    #: and an empty ``facet_channels`` would then read as *clean* to a gate
    #: looking for degradation. Absence reading as agreement, in the field added
    #: to stop it.
    not_applicable = "not_applicable"
    #: ``None`` means the provider or upstream did not report it. Also not a value.
    not_measured = "not_measured"


class Redaction(str, Enum):
    """What the durable projection keeps, per ADR 0006 §11.

    Declared here rather than in the sink because v1 had **two** sinks for one
    record with different policies. One table, both sinks read it.

    The rule is **deny by shape, not by key name**: a per-key whitelist cannot
    tell a closed vocabulary from a question echo, and ``detail`` is free-form at
    the source. It stopped being hypothetical when search and grep records began
    carrying the model's own search string.
    """

    #: Enum or bounded identifier. Kept verbatim.
    closed_vocabulary = "closed_vocabulary"
    #: Numeric. Kept verbatim.
    numeric = "numeric"
    #: Asset ids or hashes. Kept — they name things without quoting them.
    reference = "reference"
    #: A SQL statement. Kept as a digest plus a literal-elided structural
    #: fingerprint, never as text: libpq embeds the offending statement in error
    #: text (``LINE 1: SELECT ...``), so raw retention echoes question literals
    #: and PII.
    statement = "statement"
    #: Free text. **Dropped.**
    free_text = "free_text"


@dataclass(frozen=True, slots=True)
class RecordField:
    """One declared field of a turn record."""

    name: str
    tier: Tier
    absence: Absence
    redaction: Redaction

    #: The stage **after which this value exists**, named as a
    #: :class:`~governed_bi.register.stages.Stage` member.
    #:
    #: Not "the line of code that assigns it" — for a value several stages
    #: contribute to, this is the last stage that must complete before the value is
    #: final. ``facet_hits`` is owned by ``route`` for that reason: the five facet
    #: nodes produce the hits, and ``route`` dedups and budgets them, so before
    #: ``route`` the field has no final value. A facet crash therefore stamps
    #: ``failed_stage=facet_entity`` while this column says ``route``, and both are
    #: correct — they answer different questions.
    #:
    #: Naming the producer as a ``Stage`` is what lets a bottom-level declaration
    #: talk about the top of the system without importing it, and it makes "a gate
    #: that reads a field nothing writes" checkable instead of a postmortem.
    owner: Stage

    why: str

    #: The quotability precondition this field feeds, if any. ``None`` means the
    #: field is recorded for attribution or diagnosis and gates nothing.
    gate: str | None = None

    #: True when the value can be recovered from other artifacts after the run.
    #: Everything **not** marked here must be captured at production time or is
    #: lost — the whole argument for the register.
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
    _f("failed_stage", Tier.outcome, Absence.not_applicable, _ID, Stage.stamp,
       "null when the turn did not fail"),
    _f("error_type", Tier.outcome, Absence.not_applicable, _ID, Stage.stamp,
       "exception CLASS only. Tracebacks echo SQL and row values"),
    _f("generated_sql", Tier.outcome, Absence.not_applicable, Redaction.statement,
       Stage.stamp,
       "null when no SQL was produced, which is not the same as empty"),
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
    _f("cost_est_usd", Tier.cost, Absence.not_measured, _N, Stage.stamp,
       "null for an unknown model, never 0. A usage payload of all zeros is truthy, "
       "so v1's guard missed it and real two-call turns recorded as free"),
    _f("latency_sec", Tier.cost, Absence.not_measured, _N, Stage.stamp, "wall clock"),

    # ── health ──────────────────────────────────────────────────────────────
    _f("n_re_served", Tier.health, Absence.never, _N, Stage.stamp,
       "re-serving a crashed turn resamples that draw AFTER failure, laundering "
       "crash_rate back to zero and conditioning the arm's EX on a re-roll",
       gate="n_re_served == 0"),
)

#: The quotability preconditions, derived from the register.
#:
#: Refuse the comparison; do not warn. v1 computed several of these and spent the
#: knowledge on a console warning that scrolls past in a multi-hour run.
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
    """Fields a quotability precondition reads.

    A gate cannot name an undeclared key, because the gate is declared *on* the
    field. Structural, not a convention to remember.
    """
    return frozenset(GATE_CONDITIONS)


def live_capture_keys() -> frozenset[str]:
    """Fields that must be captured at production time or are lost.

    Everything except the handful marked ``reconstructable``. Worth calling when
    reviewing a new field: if it is not reconstructable and not here, it is not
    recorded.
    """
    return frozenset(f.name for f in RECORD_REGISTER if not f.reconstructable)


def redaction_of(name: str) -> Redaction:
    """The durable-projection policy for one field.

    Raises ``KeyError`` for an undeclared field, deliberately: a sink asking about
    a key nobody declared should stop, not guess a policy. v1's two sinks guessed
    differently and the anonymous one guessed weaker.
    """
    for f in RECORD_REGISTER:
        if f.name == name:
            return f.redaction
    raise KeyError(f"{name!r} is not a declared record field")


def missing_required(record: Mapping[str, Any]) -> frozenset[str]:
    """Required keys that are absent **or null**. The presence test.

    Null counts as missing, and that is the whole substance of this function. With
    key-presence alone the test is a rubber stamp: :func:`project` writes every
    declared key, so a record of twenty nulls has every key and passes. v1's
    derived gate list was theatre for the same reason one layer up — the record it
    read was built from a hand-written subset, so a gate key that was ``None`` on
    both sides read as agreement and nothing ever failed.

    Fields declared :attr:`Absence.not_applicable` or :attr:`Absence.not_measured`
    are **not** checked: null is a legal value there, and the register is how a
    reader knows which is which.

    **An unmeasured** :class:`~.quantity.Measured` **counts as missing too**, and that
    clause is the third appearance of this same rubber-stamp shape in this project.
    ``Measured.unmeasured("provider returned no token count")`` is not ``None``, so a
    null check alone lets a required field pass while carrying an explicit
    non-measurement — the value is honest and the gate reading it is not. The first
    instance was v1's ``corpus_content_hash == "unknown"`` comparing equal to itself;
    the second was this function checking key-presence only; this one was introduced
    by adding :class:`~.quantity.Measured` and would have shipped with it.

    That import is also the concrete reason :mod:`.quantity` is in this layer rather
    than one above: a presence test cannot recognise a type it cannot import.

    ``tests/conformance`` asserts this returns empty **for a record produced by a
    real turn on every terminal path** — not for a fixture. That is the half v1
    skipped, and a refusal path is exactly where the eight stage-conditional fields
    above would otherwise fail.
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
    """Keys in ``record`` the register does not declare.

    The other direction of the same closure. An emitted key nobody declared is how
    instrumentation ends up somewhere no gate and no analysis will ever look — v1
    lost two fields that way for a year.
    """
    return frozenset(record) - record_keys()


def project(
    state: Mapping[str, Any], *, extract: Callable[[Mapping[str, Any], str], Any]
) -> dict[str, Any]:
    """Build a turn record from serve state, using the register as the schema.

    ``extract`` is injected rather than imported so this module stays free of any
    dependency on the serve types — it must import in a bare interpreter from
    either side.

    A field whose extractor yields ``None`` is written as ``None``, **not
    omitted**. Omission and null are different facts, and only one of them is
    legible to a reader holding the schema. :func:`missing_required` is what makes
    the distinction enforceable rather than decorative.
    """
    return {f.name: extract(state, f.name) for f in RECORD_REGISTER}


def _assert_register_is_coherent() -> None:
    """Import-time invariants. Three.

    None of them is a tautology: each can fail on a plausible edit, and the third
    already has.
    """
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
            f"{ungated_health}. The tier's definition is 'every one of these is a "
            "quotability input'; a health field no gate reads is the v1 incident "
            "this register exists to prevent."
        )

    # A field owned by a facet stage would be per-facet rather than aggregated, and
    # nothing downstream is shaped for that yet. Asserted so the intended reading
    # of `owner` (see RecordField.owner) stays true rather than drifting.
    per_facet = sorted(f.name for f in RECORD_REGISTER if f.owner in FACET_STAGES)
    if per_facet:  # pragma: no cover - import-time guard
        raise AssertionError(
            f"fields owned by a facet stage: {per_facet}. `owner` is the stage after "
            "which the value is final; facet evidence is finalised by `route`."
        )


_assert_register_is_coherent()
