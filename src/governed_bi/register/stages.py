"""One vocabulary for *where* a turn went and *how* it ended.

The system once had nine of these. Ledger verdicts, guardrail layers, the
two-axis reliability stamp, free-text ``refused_by``, curator verdicts, validator
finding codes, the grader's own error strings, note-gate pass/skip, and the
offline analyser's attribution buckets all described failure in their own words,
with no mapping between them. The cost was not untidiness — it was that "which
part of the system is breaking?" had no answer you could compute.

This module is the answer's key space. **Text and pure functions only: no I/O, no
settings, no model, no imports outside stdlib.** Both the serve path and the eval
harness import it, so it must stay dependency-free in both directions.

Two axes, deliberately kept apart:

* :class:`Outcome` — what happened to the *turn*.
* :class:`Stage` — *where* in the pipeline it happened.

Gradeability is a third, orthogonal thing (was there a usable gold answer to
compare against?) and deliberately does **not** live here: a question with no
gold hash was still answered or still refused, and conflating the two is how a
grading gap starts reading as a model failure.

**Why a stage name is never quietly renamed.** :func:`classify_row` is how the
harness separates a crash from a refusal, and it reads these names. Renaming one
without updating the taxonomy is how a run becomes unquotable — which is the
reason the pre-2026-07-25 numbers were retired. ADR 0005 §4.3 therefore makes
this enum a boundary contract and a precondition of the serve-graph step, not a
follow-up.

Ownership: ADR 0005 defines the retrieval and graph stages; ADR 0006 defines the
execution-governance stages. Both are declared here, in one enum, because the
whole point is that there is exactly one vocabulary.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Mapping

__all__ = [
    "Stage",
    "Outcome",
    "FACET_STAGES",
    "TERMINAL_STAGES",
    "REFUSED_BY_TO_STAGE",
    "CRASH_REFUSED_BY",
    "ATTEMPT_CAP_REFUSED_BY",
    "INFRA_ERROR_PREFIX",
    "GRADER_ANSWER_PREFIXES",
    "classify_outcome",
    "classify_row",
]


class Stage(str, Enum):
    """A position in the pipeline, named as the live event stream names it.

    Reusing the emitted names rather than inventing parallel ones is the whole
    point of this module.

    **Declared but not yet emitted members are kept on purpose.** A stage name
    that only appears once someone instruments it is better than a second,
    competing name invented at that moment — which is exactly how the nine
    vocabularies this module replaced came about. Anything reading a
    ``by_failed_stage`` map must treat an absent key as "not observed", never as
    zero.
    """

    # ── Graph nodes, in execution order (ADR 0005 §3.1) ──
    #: Rule-based input gate. ADR 0006 §6 owns the rules; this owns the name.
    guard = "guard"
    #: Follow-up question rewritten into a self-contained one. Fires only when the
    #: thread has prior turns, so a single-turn eval question does not pay for it.
    rewrite = "rewrite"
    #: v1 called this ``refuse_gate``. Renamed because it is now one specific
    #: gate over ``NegativeExampleAsset``, not the general refusal path.
    negative_gate = "negative_gate"

    # ── The five facets, concurrent ──
    # Named individually rather than as one `facet` member: "which facet
    # degraded" is a quotability input, and a single member makes it unanswerable.
    facet_schema = "facet_schema"
    facet_term = "facet_term"
    facet_metric = "facet_metric"
    facet_entity = "facet_entity"
    facet_example = "facet_example"

    # ── Post-fan-in, deterministic ──
    #: NOTE: v1's ``route`` was the ingest rail. Here it is schema selection plus
    #: the second retrieval pass — what v1 called ``schema_pick``.
    route = "route"
    #: Reference closure. A total function of the hit set.
    resolve = "resolve"
    #: Steiner connectivity. A choice among paths, and therefore bounded.
    connect = "connect"
    #: NOTE: v1's ``assemble`` spanned retrieval *and* context build. Here it is
    #: rendering only.
    assemble = "assemble"

    # ── The agent loop ──
    #
    # Note what is deliberately absent: there is no `run_query` member. A passing
    # query already emits the `check` + `execute` pair, and a third record would
    # double-count an action the ledger and every rate already agree on — v1
    # recorded exactly that reasoning after adding, then removing, the third
    # record. ADR 0005 §3.5's "every tool call emits a stage record" is true of the
    # tools that have no other trace; `run_query` has two.
    agent_core = "agent_core"
    #: Replaces v1's ``search_corpus`` / ``read_notes`` / ``grep_notes``. Its job
    #: is retrieving the ``body`` of assets that ``resolve``/``connect`` pulled in
    #: and which therefore rendered structure-only — not re-querying the index.
    read_body = "read_body"
    inspect_schema = "inspect_schema"
    sample_rows = "sample_rows"
    ask_user = "ask_user"

    # ── Execution governance (ADR 0006) ──
    #: The layer stack. A block here is a governance decision, never a crash.
    check = "check"
    #: A statement reached the database.
    execute = "execute"
    #: Re-executed after a passing recheck, delivered marked unverified.
    graded_delivery = "graded_delivery"
    #: The attempt cap terminated the turn. Distinct from a crash and from a
    #: model refusal, because counting it as either is the inverse of the defect
    #: that retired a set of numbers.
    cap = "cap"

    # ── Terminals ──
    refuse = "refuse"
    decline = "decline"
    #: The only node that writes an ``Answer``. Every terminal path funnels here,
    #: including node exceptions, because v1's ``assemble`` was the one node with
    #: no exception handling and a crash there produced no answer, no refusal and
    #: no log row at all.
    stamp = "stamp"

    # ── Attributed after the fact by the offline analyser ──
    # These describe a turn that answered *wrongly*, which the live path cannot
    # know because it has no gold to compare against.
    table_select = "table_select"
    sql_generate = "sql_generate"

    # ── Declared, not yet emitted ──
    repair = "repair"


#: The facet stages, in fan-out order. Exported so a caller iterates the facet set
#: instead of re-listing it — a second listing is a listing that can drift.
FACET_STAGES: tuple[Stage, ...] = (
    Stage.facet_schema,
    Stage.facet_term,
    Stage.facet_metric,
    Stage.facet_entity,
    Stage.facet_example,
)

#: Stages that end a turn. ``stamp`` is not one of them: it is what every terminal
#: passes *through*.
TERMINAL_STAGES: frozenset[Stage] = frozenset({Stage.refuse, Stage.decline, Stage.cap})


class Outcome(str, Enum):
    """How a turn ended, from the measurement's point of view.

    ``crashed`` is the member the harness could not previously express, and its
    absence is why a set of numbers had to be thrown away: a solver exception and
    a deliberate refusal both arrived as ``error="refusal"``, so ``refusal_rate``
    absorbed the crash count and EX absorbed the loss — by a **different amount
    per arm**, since arms do not crash at the same rate. A crash is a bug in us;
    a refusal is the product working. Any metric that adds them together is
    measuring two things and reporting one.

    ``capped`` is separate from both for the same reason (ADR 0006 §5): a
    governance-terminated turn counted as a crash is that defect inverted.

    Note what is deliberately **not** here: **graded delivery is not an outcome.**
    A graded turn ``answered`` — it answered with low semantic assurance, which is
    a reliability-stamp axis, not a turn-ending one. ADR 0006's
    ``ExecutionRecord.terminal`` tracks the execution-layer terminal state and
    answers a different question.
    """

    answered = "answered"
    refused = "refused"
    clarification = "clarification"
    capped = "capped"
    crashed = "crashed"


#: Where each ``refused_by`` value was decided.
#:
#: ``refused_by`` is free text with no central declaration, so this table is also
#: the closest thing to an inventory of its legal values — :func:`classify_outcome`
#: counts anything absent from it rather than quietly inventing a category from a
#: typo.
REFUSED_BY_TO_STAGE: Mapping[str, Stage] = {
    "guard": Stage.guard,
    "negative_example": Stage.negative_gate,
    "no_schema_matched": Stage.route,
    "missing_join_path": Stage.connect,
    "over_connect_bounds": Stage.connect,
    "guardrail": Stage.check,
    "attempt_cap": Stage.cap,
    "model_error": Stage.agent_core,
}

#: ``refused_by`` values that mean *our bug*, not the product working.
#:
#: The serve path writes this stamp when it catches an internal exception and
#: degrades to a refusal, so a row carrying it is a crash wearing a refusal's
#: clothes. A ``NameError`` in a tool helper sat in v1's serve path for a long
#: time looking like an intermittent model hiccup for exactly this reason.
CRASH_REFUSED_BY: frozenset[str] = frozenset({"model_error"})

#: The ``refused_by`` value that means the attempt cap ended the turn.
#:
#: A named constant rather than a literal inside :func:`classify_outcome`, because
#: a bare string there duplicates a :data:`REFUSED_BY_TO_STAGE` key: rename the key
#: and a cap-terminated turn silently classifies ``refused`` instead of ``capped``,
#: which is the precise inversion ADR 0006 §5 exists to prevent. The import-time
#: guard below asserts the two stay in step.
ATTEMPT_CAP_REFUSED_BY = "attempt_cap"

#: The grader could not finish — a harness failure. The turn is ``crashed`` and
#: the run is not quotable.
INFRA_ERROR_PREFIX = "infra_error:"

#: Grader error prefixes that describe an **answer**, not a crash.
#:
#: ``gold_unusable:``
#:     The gold side was unusable, so the row is ungradeable — which is the third
#:     axis, not an outcome.
#: ``exec_error:``
#:     The model's own statement parsed and then raised. That is a wrong answer,
#:     not our bug.
#:
#: The permanent trap this split had to dodge: ``OperationalError`` is
#: deliberately **not** an infrastructure class, because SQLite wraps "no such
#: column" in it, and treating those as infrastructure hides wrong answers as
#: crashes.
GRADER_ANSWER_PREFIXES: frozenset[str] = frozenset({"gold_unusable:", "exec_error:"})


def classify_outcome(
    *,
    error: str | None,
    refused_by: str | None,
    has_sql: bool,
    clarification_requested: bool = False,
) -> Outcome:
    """Map one turn's terminal signals onto exactly one :class:`Outcome`.

    Arguments are primitives rather than the runtime record types, because this
    module may not import them — see the note on dependency direction in
    :mod:`governed_bi.register`.

    Precedence is total and deliberate:

    1. An **infra error** outranks everything: the harness broke.
    2. A ``refused_by`` in :data:`CRASH_REFUSED_BY` is a crash, whatever else the
       row says. This is the case that cost a run.
    3. The attempt cap is ``capped``, never ``crashed`` and never ``refused``.
    4. A clarification request.
    5. Any other ``refused_by`` is a genuine refusal.
    6. SQL present ⇒ ``answered``; absent ⇒ ``crashed``.

    Rule 6's fallback is ``crashed``, not ``refused``: a turn that produced no SQL
    and no refusal stamp did not *decide* anything, and calling that a refusal is
    the original defect.
    """
    if error and error.startswith(INFRA_ERROR_PREFIX):
        return Outcome.crashed
    if refused_by in CRASH_REFUSED_BY:
        return Outcome.crashed
    if refused_by == ATTEMPT_CAP_REFUSED_BY:
        return Outcome.capped
    if clarification_requested:
        return Outcome.clarification
    if refused_by:
        return Outcome.refused
    if has_sql:
        return Outcome.answered
    return Outcome.crashed


def classify_row(row: Mapping[str, Any]) -> tuple[Outcome, Stage | None]:
    """Classify a recorded row, preferring what the producer stamped.

    A row scored under a newer classifier must not be silently re-derived under an
    older one, so a stamped ``outcome`` wins over re-derivation. The
    string-sniffing path is the **fallback**, not the mechanism.

    Returns ``(outcome, failed_stage)``. ``failed_stage`` is ``None`` when the
    turn did not fail, and that ``None`` is a value — a reader must not fold it
    into a stage.
    """
    stamped = row.get("outcome")
    if isinstance(stamped, str):
        try:
            outcome = Outcome(stamped)
        except ValueError:
            outcome = None
        if outcome is not None:
            failed = row.get("failed_stage")
            stage: Stage | None = None
            if isinstance(failed, str):
                try:
                    stage = Stage(failed)
                except ValueError:
                    stage = None
            return outcome, stage

    refused_by = row.get("refused_by")
    refused_by = refused_by if isinstance(refused_by, str) else None
    outcome = classify_outcome(
        error=row.get("error") if isinstance(row.get("error"), str) else None,
        refused_by=refused_by,
        has_sql=bool(row.get("generated_sql")),
        clarification_requested=bool(row.get("clarification_requested")),
    )
    stage = REFUSED_BY_TO_STAGE.get(refused_by) if refused_by else None
    return outcome, stage


def _assert_refusal_tables_are_closed() -> None:
    """Import-time invariants. ADR 0005 implementation step 4 requires the first.

    ``stages.py`` was the one register module with no guard, which is how the bare
    ``"attempt_cap"`` literal above went unnoticed: nothing tied the string in the
    classifier to the key in the table, so renaming one would silently reclassify a
    governance-terminated turn as an ordinary refusal.
    """
    bad_stage = sorted(k for k, v in REFUSED_BY_TO_STAGE.items() if not isinstance(v, Stage))
    if bad_stage:  # pragma: no cover - import-time guard
        raise AssertionError(f"refused_by values mapping to a non-Stage: {bad_stage}")

    unmapped_crash = sorted(CRASH_REFUSED_BY - set(REFUSED_BY_TO_STAGE))
    if unmapped_crash:  # pragma: no cover - import-time guard
        raise AssertionError(
            f"crash refusals with no stage: {unmapped_crash}. A crash whose stage is "
            "unknown cannot be attributed, which is what made a NameError in a tool "
            "helper look like an intermittent model hiccup."
        )

    if ATTEMPT_CAP_REFUSED_BY not in REFUSED_BY_TO_STAGE:  # pragma: no cover
        raise AssertionError(
            f"{ATTEMPT_CAP_REFUSED_BY!r} is not in REFUSED_BY_TO_STAGE, so "
            "classify_outcome would return `capped` for a value nothing else "
            "recognises."
        )
    if REFUSED_BY_TO_STAGE[ATTEMPT_CAP_REFUSED_BY] is not Stage.cap:  # pragma: no cover
        raise AssertionError(
            f"{ATTEMPT_CAP_REFUSED_BY!r} must map to Stage.cap, not "
            f"{REFUSED_BY_TO_STAGE[ATTEMPT_CAP_REFUSED_BY]!r}"
        )

    overlap = TERMINAL_STAGES & set(FACET_STAGES)
    if overlap:  # pragma: no cover - import-time guard
        raise AssertionError(f"a facet cannot be a terminal stage: {sorted(overlap)}")


_assert_refusal_tables_are_closed()
