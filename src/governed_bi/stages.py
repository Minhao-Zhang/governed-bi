"""One vocabulary for *where* a turn went and *how* it ended.

The system had nine of these. Ledger verdicts, guardrail layers, the two-axis
reliability stamp, free-text ``refused_by``, curator verdicts, validator finding
codes, the grader's own error strings, note-gate pass/skip, and the offline
analyser's attribution buckets all described failure in their own words, with no
mapping between them. The cost was not untidiness — it was that
"which part of the system is breaking?" had no answer you could compute.

This module is the answer's key space. It holds text and pure functions only: no
I/O, no settings, no model. Both the serve path and the eval harness import it,
so it must stay dependency-free in both directions.

Two axes, deliberately kept apart:

* :class:`Outcome` — what happened to the *turn*. Did the system answer, refuse
  on purpose, ask for clarification, hit a cap, or break?
* :class:`Stage` — *where* in the pipeline it happened.

Gradeability is a third, orthogonal thing (was there a usable gold answer to
compare against?) and deliberately does **not** live here: a question with no
gold hash was still answered or still refused, and conflating the two is how a
grading gap starts reading as a model failure.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Mapping

__all__ = [
    "Stage",
    "Outcome",
    "REFUSED_BY_TO_STAGE",
    "CRASH_REFUSED_BY",
    "INFRA_ERROR_PREFIX",
    "classify_outcome",
    "classify_row",
]


class Stage(str, Enum):
    """A position in the serve pipeline, named as the live event stream names it.

    The first seven are the graph's own rails, so these strings match what
    ``GovEventStream.rail`` already emits and what
    ``tests/test_agent_step_events.py`` already pins — reusing them rather than
    inventing parallel names is the whole point of this module.

    The rest are sub-stages inside ``assemble`` and ``agent_core``, where the
    interesting failures actually live: a schema-pick miss and a guardrail block
    are both "``assemble``/``agent_core`` failed" to the graph, and telling them
    apart is the difference between fixing retrieval and fixing generation.

    Producers differ by stage and that is fine. ``schema_pick``, ``guardrail`` and
    ``execute`` are stamped live by the serve path, as are the agent's own tool
    calls — ``search_corpus``, ``inspect_schema``, ``read_notes``, ``grep_notes``
    and (only on the licensing denial that returns before the guardrail runs)
    ``sample_rows``, all written from ``analyst.agent._resolve_tool``. A passing
    ``run_query`` / ``sample_rows`` deliberately has **no** record of its own: the
    middleware already writes the ``guardrail`` + ``execute`` pair for it, and a
    third record would double-count an action the ledger and every rate already
    agree on. ``shortlist``, ``table_select`` and ``sql_generate`` are attributed
    *after* the fact by :mod:`governed_bi.eval.error_taxonomy`, which diffs the
    generated SQL against gold — they describe a turn that answered *wrongly*,
    which the live path cannot know because it has no gold to compare against.
    ``license`` and ``repair`` are declared but nothing emits them yet. They are
    kept because the vocabulary is the point — a stage name that only appears once
    someone instruments it is better than a second, competing name invented at that
    moment, which is how the nine vocabularies this module replaced came about.
    Anything reading ``by_failed_stage`` should treat an absent key as "not
    observed", never as zero.
    """

    # Graph rails, in execution order.
    route = "route"
    refuse_gate = "refuse_gate"
    assemble = "assemble"
    agent_core = "agent_core"
    narrate = "narrate"
    finalize = "finalize"

    # Sub-stages of assemble.
    shortlist = "shortlist"
    schema_pick = "schema_pick"
    retrieve = "retrieve"
    license = "license"

    # Sub-stages of agent_core.
    search_corpus = "search_corpus"
    inspect_schema = "inspect_schema"
    sample_rows = "sample_rows"
    # The two note tools. Named separately rather than folded into
    # ``search_corpus`` because they are a different retrieval surface (prose the
    # curator wrote, not assets) and collapsing them would make "how often does the
    # agent read notes?" unanswerable — the question the notes redesign exists to
    # answer.
    read_notes = "read_notes"
    grep_notes = "grep_notes"
    table_select = "table_select"
    sql_generate = "sql_generate"
    guardrail = "guardrail"
    execute = "execute"
    repair = "repair"


class Outcome(str, Enum):
    """How a turn ended, from the measurement's point of view.

    ``crashed`` is the member the harness could not previously express, and its
    absence is why a set of numbers had to be thrown away: a solver exception
    and a deliberate refusal both arrived as ``error="refusal"``, so
    ``refusal_rate`` absorbed the crash count and EX absorbed the loss — by a
    *different amount per arm*, since the arms do not crash at the same rate.
    A crash is a bug in us; a refusal is the product working. Any metric that
    adds them together is measuring two things and reporting one.
    """

    answered = "answered"
    refused = "refused"
    clarification = "clarification"
    capped = "capped"
    crashed = "crashed"


#: Where each ``refused_by`` value was decided. ``refused_by`` is a free-text
#: field with no central declaration, so this table is also the closest thing to
#: an inventory of its legal values — :func:`classify_outcome` counts anything
#: absent from it rather than quietly inventing a category from a typo.
REFUSED_BY_TO_STAGE: dict[str, Stage] = {
    "refuse_gate": Stage.refuse_gate,
    "missing_edge": Stage.assemble,
    "no_coverage": Stage.assemble,
    "guardrail": Stage.guardrail,
    "execution": Stage.execute,
    "exhausted": Stage.agent_core,
    "model_error": Stage.agent_core,
    "clarification_declined": Stage.finalize,
    # A no-model offline smoke run (formerly ``--skip-agent``, now what an arm
    # served with no model configured degrades to): every question refuses at the
    # top of the turn. Omitting it made the whole offline path read as an
    # unrecognised free-text typo on every single question.
    "no_model": Stage.route,
}

#: ``refused_by`` values that are **not** governed refusals. ``model_error`` is
#: what the serve path stamps when it catches an internal exception and degrades
#: to a refusal so the turn fails closed. Failing closed is correct; scoring it
#: as a refusal is not, because it is our bug, not the model declining. Reading
#: this as a refusal is exactly how a ``NameError`` in a tool-rendering helper
#: sat in the serve path looking like an intermittent model hiccup.
CRASH_REFUSED_BY: frozenset[str] = frozenset({"model_error"})

#: ``refused_by`` values that mean "the cap stopped us", not "we declined".
_CAP_REFUSED_BY: frozenset[str] = frozenset({"exhausted"})


def classify_outcome(
    *,
    generated_sql: str | None,
    exception: str | None = None,
    refused_by: str | None = None,
    recursion_exhausted: bool | None = None,
) -> tuple[Outcome, Stage | None, bool]:
    """Classify one turn.

    Returns ``(outcome, failed_stage, refused_by_recognised)``. ``failed_stage``
    is ``None`` for a turn that succeeded, and for a failure whose stage genuinely
    cannot be determined — a guess there would be worse than an absence, because
    it would put weight in a ``by_failed_stage`` bucket that nothing actually
    observed.

    The third element is the honesty flag: ``False`` means ``refused_by`` carried
    a value this module has never heard of. Callers should count those. A typo in
    a free-text field otherwise mints a new failure category that no report will
    ever mention.

    ``exception`` takes precedence over everything: a turn that raised did not
    refuse, whatever else its metadata says.
    """
    if exception:
        return Outcome.crashed, None, True

    if generated_sql:
        return Outcome.answered, None, True

    # No SQL and no exception: the turn stopped on purpose, or thinks it did.
    if refused_by is None:
        # Nothing produced and nothing recorded about why. Genuinely unknown, and
        # worth surfacing as such rather than defaulting into `refused` — a
        # silent no-op is a different bug from a considered refusal.
        if recursion_exhausted:
            return Outcome.capped, Stage.agent_core, True
        return Outcome.refused, None, True

    key = str(refused_by)
    stage = REFUSED_BY_TO_STAGE.get(key)
    recognised = stage is not None

    if key in CRASH_REFUSED_BY:
        return Outcome.crashed, stage, recognised
    if key in _CAP_REFUSED_BY or recursion_exhausted:
        return Outcome.capped, stage or Stage.agent_core, recognised
    if key == "clarification_declined":
        return Outcome.clarification, stage, recognised
    return Outcome.refused, stage, recognised


def classify_row(row: Mapping[str, Any]) -> tuple[Outcome, Stage | None, bool]:
    """Classify a scored eval row, from disk or in flight.

    Prefers the ``outcome`` / ``failed_stage`` the scorer already stamped, so a
    row scored under a newer classifier is not silently re-derived under an older
    one. Falls back to inferring from the raw fields, which is what makes rows
    written before this module existed still analysable.

    The inference has one unavoidable soft spot. ``error`` holds *either* a
    grader verdict (``"refusal"``, ``"missing_gold_hash"``,
    ``"gold_unusable:..."``) *or* an arbitrary exception message, because the
    scorer overwrites the former with the latter. So an exception is recognised
    as "an ``error`` that is not one of the known grader verdicts". That is why
    the scorer should stamp ``outcome`` at the source and why this path is the
    fallback, not the mechanism.
    """
    stamped = row.get("outcome")
    if stamped is not None:
        try:
            outcome = Outcome(str(stamped))
        except ValueError:
            outcome = None  # unknown value written by a newer/other producer
        if outcome is not None:
            raw_stage = row.get("failed_stage")
            stage: Stage | None = None
            if raw_stage is not None:
                try:
                    stage = Stage(str(raw_stage))
                except ValueError:
                    stage = None
            return outcome, stage, True

    return classify_outcome(
        generated_sql=row.get("generated_sql"),
        exception=_exception_from_error(row.get("error")),
        refused_by=row.get("refused_by"),
        recursion_exhausted=row.get("recursion_exhausted"),
    )


#: Values the grader itself puts in ``error``. Anything else in that field came
#: from ``str(exception)``.
_GRADER_ERRORS: frozenset[str] = frozenset({"refusal", "missing_gold_hash"})

#: Prefixes the grader uses for a *verdict* about the answer, as opposed to a crash
#: in us. ``gold_unusable:`` — the gold side could not be used. ``exec_error:`` — the
#: model's own statement raised when the grader ran it, which is a wrong answer, not
#: our bug, and must not be counted as a crash.
#:
#: ``infra_error:`` is deliberately *not* here. Timeouts, connection deaths, and
#: truncated results are harness failures: ``_exception_from_error`` must surface
#: them so ``classify_row`` / ``crash_rate`` / ``quotable`` treat them as crashed
#: rather than answered-and-wrong (audit E4).
_GRADER_ERROR_PREFIXES: tuple[str, ...] = ("gold_unusable:", "exec_error:")

#: Prefix stamped by ``score_sql_hashes`` for infrastructure failures. Listed so
#: callers can recognise it without re-deriving the classification.
INFRA_ERROR_PREFIX: str = "infra_error:"


def _exception_from_error(error: Any) -> str | None:
    """The exception message inside a row's ``error`` field, if that is what it is."""
    if not error or not isinstance(error, str):
        return None
    if error in _GRADER_ERRORS or error.startswith(_GRADER_ERROR_PREFIXES):
        return None
    return error
