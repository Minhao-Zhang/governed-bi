"""Stage and outcome vocabulary for where a turn went and how it ended.

Text and pure functions only: no I/O, settings, or non-stdlib imports. Two axes:
:class:`Outcome` (turn ending) and :class:`Stage` (pipeline position).
Gradeability is orthogonal and does not live here. ADR 0005 and ADR 0006 stages
share one enum.
"""

from __future__ import annotations

from enum import Enum
from typing import Mapping

__all__ = [
    "Stage",
    "Outcome",
    "FACET_STAGES",
    "TERMINAL_STAGES",
    "REFUSED_BY_TO_STAGE",
    "ABSTENTION_REASONS",
    "CRASH_REFUSED_BY",
    "ATTEMPT_CAP_REFUSED_BY",
    "INFRA_ERROR_PREFIX",
    "GRADER_ANSWER_PREFIXES",
    "classify_outcome",
]


class Stage(str, Enum):
    """A position in the pipeline, named as the live event stream names it.

    Declared-but-unemitted members are kept so instrumentation does not invent a
    second name. Absent keys in ``by_failed_stage`` mean not observed, not zero.
    """

    # ── Graph nodes, in execution order (ADR 0005 §3.1) ──
    #: Derives a turn from the conversation when the caller sends only a message.
    #: Optional: `START -> accept -> guard` on a server; `START -> guard` otherwise.
    accept = "accept"
    #: Rule-based input gate. ADR 0006 §6 owns the rules; this owns the name.
    guard = "guard"
    #: Follow-up rewritten into a self-contained question (multi-turn only).
    rewrite = "rewrite"
    #: Gate over ``NegativeExampleAsset``.
    negative_gate = "negative_gate"

    # ── The five facets, concurrent ──
    # Named individually: "which facet degraded" is a quotability input.
    facet_schema = "facet_schema"
    facet_term = "facet_term"
    facet_metric = "facet_metric"
    facet_entity = "facet_entity"
    facet_example = "facet_example"

    # ── Post-fan-in, deterministic ──
    #: Schema selection plus the second retrieval pass.
    route = "route"
    #: Reference closure. A total function of the hit set.
    resolve = "resolve"
    #: Steiner connectivity. A choice among paths, and therefore bounded.
    connect = "connect"
    #: Context rendering only.
    assemble = "assemble"

    # ── The agent loop ──
    # No `run_query` member: a passing query already emits `check` + `execute`.
    agent_core = "agent_core"
    #: Retrieve ``body`` of assets that rendered structure-only.
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
    #: Attempt cap terminated the turn. Distinct from crash and model refusal.
    cap = "cap"

    #: The declared abstention policy (ADR 0013), between ``assemble`` and ``agent_core``.
    #: It **decides**, unlike ``reflect``, so it appears in the refusal table below — and it
    #: sits before the agent because deciding after five ``run_query`` attempts is not a
    #: decision, it is a report on one.
    abstain = "abstain"

    #: Post-hoc judgement of whether the statement answered the question. An observer:
    #: it decides nothing, so it appears in no refusal table and ends no turn.
    reflect = "reflect"

    #: Turns the turn into a sentence. Adopts the agent's closing text when present;
    #: generates only when the loop ended without prose.
    narrate = "narrate"

    # ── Terminals ──
    refuse = "refuse"
    decline = "decline"
    #: Writes an ``Answer``. Every terminal path funnels here.
    stamp = "stamp"

    # ── Attributed after the fact by the offline analyser ──
    table_select = "table_select"
    sql_generate = "sql_generate"

    # ── Declared, not yet emitted ──
    repair = "repair"


#: Facet stages in fan-out order.
FACET_STAGES: tuple[Stage, ...] = (
    Stage.facet_schema,
    Stage.facet_term,
    Stage.facet_metric,
    Stage.facet_entity,
    Stage.facet_example,
)

#: Stages that end a turn. ``stamp`` is not one: every terminal passes through it.
TERMINAL_STAGES: frozenset[Stage] = frozenset({Stage.refuse, Stage.decline, Stage.cap})


class Outcome(str, Enum):
    """How a turn ended, from the measurement's point of view.

    ``crashed`` vs ``refused`` must stay separate: a crash is our bug, a refusal is
    the product working, and ``capped`` is neither (ADR 0006 §5). Graded delivery is
    not an outcome — the turn ``answered``.

    :attr:`no_sql` is the fourth thing none of those three is, and it must stay apart from
    :attr:`answered` for the reason the class exists: **the turn executed no governed
    statement.** For an engine whose claim is grounded answers that is the distinction that
    matters — an answer with no auditable statement is not a governed answer — and folding it
    into ``answered`` is what made a model declining in prose ("these terms are not defined in
    the provided schemas") record ``outcome: answered`` beside ``ledger: no_sql`` and
    ``generated_sql: null``, live, on 2026-08-18.

    It is deliberately **not** narrower than that. Three live paths produce exactly these
    signals and the record cannot separate them: the no-model stub
    (``agent_core._stub``), a genuine answer read off the delivered context ("which tables are
    available?"), and a prose decline. Naming the member after the one property all three share
    is the only claim the ledger can back; a "does this prose look like a refusal" heuristic
    would be a declaration with no enforcer, which is what this register exists to refuse.

    :attr:`clarification` is **one member over two endings**, and that is a deliberate limit on
    what it may be used for. The engine asked the reader something and got no usable answer,
    either because the turn paused on ``ask_user`` and nothing resumed it — no node ever stamped
    that turn — or because the reader declined or cancelled and ``ask_user`` failed closed, which
    ends the agent loop and *does* reach ``stamp`` with a full record. Both are the same fact
    about delivery (no governed answer ran, and the reader owes one), so they are counted together
    by ``measure/selective.DECLINED``, ``eval/grade.grade_turn`` and ``eval/projection``'s
    ``clarified`` — splitting the member would split those three counts for no measurement
    question, and would put a value in the artifacts that ``ui/lib/schemas.ts``'s closed outcome
    enum drops on the floor. What the member therefore **cannot** answer is whether the row was
    stamped. A consumer needing that must read a field ``stamp`` writes;
    ``measure/gates._paused_before_stamp`` is the one that does, and carries the write-up of what
    reading the outcome instead cost. The two writers are named in :func:`classify_outcome`.
    """

    answered = "answered"
    refused = "refused"
    clarification = "clarification"
    capped = "capped"
    crashed = "crashed"
    #: Spelled exactly as ``govern.ledger.ExecutionRecord``'s ``terminal`` word, and
    #: ``govern/ledger.py`` asserts the two agree at import. ``stamp`` derives this member
    #: *from* that field rather than from ``path_kind``, the same way it reads the ledger's
    #: verdict for ``capped``, so ``outcome`` and ``ledger`` cannot disagree; a second spelling
    #: here would be a second answer to one question.
    no_sql = "no_sql"


#: The abstention policy's closed vocabulary — **the reason the engine decided to withhold**,
#: as opposed to the rule a layer happened to refuse under (ADR 0013).
#:
#: Declared here rather than in ``serve/`` because that is what makes it *closed*, and the
#: mechanism is worth naming exactly — this comment used to claim three readers of
#: :data:`REFUSED_BY_TO_STAGE` and on 2026-08-12 there were none (ADR 0013 §2 records the
#: measurement). Two things hold it closed:
#:
#: * :func:`_assert_refusal_tables_are_closed` below and
#:   ``serve/nodes/abstain.py::_assert_the_policy_speaks_the_declared_vocabulary``, which are
#:   **bidirectional** and run at import. They are what stops a fifth reason arriving as a string
#:   in a node. What they cannot see is a value neither side declares: they compare declarations
#:   to declarations, never to a row.
#: * ``eval/report.py::refusal_histogram``, which is the reader, and whose ``unattributed`` bucket
#:   is the half the guards cannot cover — a reason in no register is counted **by name** and
#:   outside ``by_stage``, so a histogram that stops adding up says which string is why.
#:
#: Order is **not** encoded here — that is the policy's, in ``serve/nodes/abstain.py``, because
#: which rule wins when two fire is a judgement about what a person should be told, not a fact
#: about the vocabulary.
#:
#: Every member reads state that already exists on the turn and needs no model, no threshold and
#: no fitted parameter. That is the constraint the reasons were chosen under: AUC 0.597 for
#: the reflector and an OOF-AUC ceiling of 0.721 for everything that does not read meaning
#: (open-work.md §3.11) say a *learned* abstainer is not available, and every risk-coverage curve
#: reading 0.7144 at the engine's own coverage says a *thresholded* one buys nothing. What was
#: missing was never a score; it was that the decision is nowhere declared.
ABSTENTION_REASONS: frozenset[str] = frozenset({
    #: A facet channel that was configured to run, ran, and errored. The shortlist this turn
    #: worked from was produced by a retriever that is not the declared one.
    "retrieval_channel_failed",
    #: The turn licensed no table. No statement it writes can clear Layer 6, so the five
    #: ``run_query`` attempts are spent discovering what ``connect`` already knew.
    "nothing_licensed",
    #: The rendered context is empty. The model has been handed nothing to work from.
    "empty_context",
    #: The char budget dropped a whole licensed table before the model saw it, so the turn is
    #: asking for SQL over a relation it did not show.
    "licensed_table_evicted",
})

#: Where each ``refused_by`` value was decided. Inventory of legal values for
#: :func:`classify_outcome`.
REFUSED_BY_TO_STAGE: Mapping[str, Stage] = {
    "guard": Stage.guard,
    "negative_example": Stage.negative_gate,
    "no_schema_matched": Stage.route,
    "missing_join_path": Stage.connect,
    "over_connect_bounds": Stage.connect,
    "guardrail": Stage.check,
    "guardrail_error": Stage.check,
    "attempt_cap": Stage.cap,
    "model_error": Stage.agent_core,
    **{reason: Stage.abstain for reason in sorted(ABSTENTION_REASONS)},
}

#: ``refused_by`` values that mean our bug (crash wearing a refusal stamp).
#:
#: ``guardrail_error`` was added by the 2026-08-10 audit (C3). A turn whose every attempt ended
#: in a swallowed exception inside ``check()`` rather than in a verdict is *ours*, and
#: :class:`Outcome` says the two must stay apart — yet it recorded ``refused``.
#: ``govern.layers.GUARDRAIL_ERROR``'s own docstring names the consequence ("presents as an arm
#: that refuses everything, with ``crash_rate == 0`` and every register key present"): the count
#: was there, ``outcome`` contradicted it, and only the count was gated.
#:
#: Spelled as a literal for the same reason ``"guardrail"`` is — ``register`` sits below
#: ``govern``, so the named constant cannot be imported here, and ``layers.py`` asserts at import
#: that the two agree.
#:
#: ``model_error`` has no producer in ``src/`` and stays a declared value.
CRASH_REFUSED_BY: frozenset[str] = frozenset({"model_error", "guardrail_error"})

#: Attempt-cap ``refused_by``. Named so rename stays in step with the table.
ATTEMPT_CAP_REFUSED_BY = "attempt_cap"

#: Grader could not finish — harness failure; turn is ``crashed``.
INFRA_ERROR_PREFIX = "infra_error:"

#: Grader error prefixes that describe an answer, not a crash.
GRADER_ANSWER_PREFIXES: frozenset[str] = frozenset({"gold_unusable:", "exec_error:"})


def classify_outcome(
    *,
    error: str | None,
    refused_by: str | None,
    has_sql: bool,
    clarification_requested: bool = False,
    terminal: str | None = None,
) -> Outcome:
    """Map terminal signals onto exactly one :class:`Outcome`.

    Precedence: infra error → crash refused_by → attempt cap → clarification →
    other refused_by → SQL present ⇒ answered → the ledger's own ``no_sql`` ⇒
    :attr:`Outcome.no_sql`, else crashed.

    ``clarification_requested`` reaches this function on exactly one path — a reader who declined
    or cancelled a question ``ask_user`` asked — and the branch below traces the writer, says why
    it sits where it does, and names the one ``refused_by`` it outranks. A turn that *paused* on
    ``ask_user`` never calls this function.

    ``terminal`` is ``govern.ledger.ExecutionRecord``'s field, and it is read in the **last two
    lines only**. That ordering is load-bearing: a guard-blocked or declined turn also carries
    an empty ledger, so ``terminal == "no_sql"`` is true of it, and reading the ledger any
    earlier would relabel a refusal as a turn that merely ran no statement.
    :func:`_assert_refusal_tables_are_closed` asserts the precedence over the whole declared
    vocabulary rather than leaving it to this docstring.

    An **absent** ``terminal`` still falls through to ``crashed``. A turn with no statement whose
    ledger says nothing at all has not been observed ending, and that is what a crash is; the
    caller has to hand over the ledger's verdict to get :attr:`Outcome.no_sql`.
    """
    if error and error.startswith(INFRA_ERROR_PREFIX):
        return Outcome.crashed
    if refused_by in CRASH_REFUSED_BY:
        return Outcome.crashed
    if refused_by == ATTEMPT_CAP_REFUSED_BY:
        return Outcome.capped
    if clarification_requested:
        # **Live, and only ever on the stamped path.** This branch was dead, and the comment here
        # said so ("Serve never sets this True: ask_user pauses via GraphInterrupt before stamp").
        # It is now written: ``serve/tools.py::ask_user`` returns ``clarification_requested=True``
        # when ``parse_resume`` reads a decline or a ranking cancel, ``_ClarificationEndsTheTurn``
        # ends the inner loop on it, ``agent_core`` lifts it onto ``ServeState``, and ``stamp``
        # hands it here. So reaching this line means *the engine asked, the reader refused to
        # answer, and the turn ended at ``stamp`` with a full record* — including the two
        # treatment identities. The pause itself never reaches this function at all: it raises
        # ``GraphInterrupt``, and the ``clarification`` on such a row is written by the transport
        # (``api/``) or by ``eval/projection.py`` when no ``answer`` exists.
        #
        # Which is why :attr:`Outcome.clarification` is **not** a witness of "never reached
        # ``stamp``" and must not be read as one. ``measure/gates.py`` was reading it as exactly
        # that — its corpus-hash denominator filter was labelled "reached stamp" — and so dropped
        # these rows, which do carry the hash, out of the population that checks it.
        #
        # **Tested before the general ``refused_by``, and the combination is reachable.**
        # ``clarification_requested`` is only ever true with ``path_kind == "answered"``
        # (``agent_core`` is its one writer, and a crash there writes ``crashed`` instead), and on
        # that path ``stamp::_path_signals`` returns a ``refused_by`` whenever no answering
        # attempt passed. Of the three values it can return there, two already outrank this branch
        # above — ``guardrail_error`` is our bug and ``attempt_cap`` is the cap — so the single
        # masked pair is ``guardrail``: a layer refused every attempt, then the model asked and
        # the reader declined. The decline wins deliberately. It is a decision something took on
        # this turn, while ``guardrail`` here is a summary derived from "nothing passed", and this
        # register puts a decision above a derived summary — the same rule that makes ``terminal``
        # the last thing read. What that costs is worth naming: the row keeps ``refused_by`` and
        # its per-attempt ``attempts`` trace, so the layer refusal is readable, but it leaves
        # ``eval/report.refusal_histogram``, which counts rows classified ``refused``.
        return Outcome.clarification
    if refused_by:
        return Outcome.refused
    if has_sql:
        return Outcome.answered
    if terminal == Outcome.no_sql.value:
        return Outcome.no_sql
    return Outcome.crashed


# ``classify_row`` was here and is gone (audit §10): zero callers, and it was a second
# entry point to one decision (read the stamped ``outcome`` if it parses, else re-derive)
# that would have disagreed exactly when the stamp was wrong. One derivation, no fallback:
# a row whose ``outcome`` does not parse is a broken row, not a row to guess about.


def _assert_refusal_tables_are_closed() -> None:
    """Import-time: refusal tables closed; cap key maps to Stage.cap."""
    bad_stage = sorted(k for k, v in REFUSED_BY_TO_STAGE.items() if not isinstance(v, Stage))
    if bad_stage:  # pragma: no cover - import-time guard
        raise AssertionError(f"refused_by values mapping to a non-Stage: {bad_stage}")

    unmapped_crash = sorted(CRASH_REFUSED_BY - set(REFUSED_BY_TO_STAGE))
    if unmapped_crash:  # pragma: no cover - import-time guard
        raise AssertionError(
            f"crash refusals with no stage: {unmapped_crash}."
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

    unmapped = sorted(ABSTENTION_REASONS - set(REFUSED_BY_TO_STAGE))
    if unmapped:  # pragma: no cover - import-time guard
        raise AssertionError(
            f"abstention reasons with no stage: {unmapped}. A reason outside "
            "REFUSED_BY_TO_STAGE is a terminal nothing can attribute, which is the "
            "free-text refusal the closed vocabulary exists to replace."
        )
    misplaced = sorted(r for r in ABSTENTION_REASONS if REFUSED_BY_TO_STAGE[r] is not Stage.abstain)
    if misplaced:  # pragma: no cover - import-time guard
        raise AssertionError(
            f"abstention reasons attributed to another stage: {misplaced}. A decision the "
            "abstention policy took must not be filed under the node it happens to be about, "
            "or the refusal histogram credits `route` with a policy's judgement."
        )
    crashing = sorted(ABSTENTION_REASONS & CRASH_REFUSED_BY)
    if crashing:  # pragma: no cover - import-time guard
        raise AssertionError(
            f"abstention reasons classified as crashes: {crashing}. Declining on purpose is "
            "the product working; `Outcome` requires it stay apart from our own bugs."
        )

    # Every declared refusal outranks an empty ledger. A guard-blocked turn, a decline and an
    # abstention all reach `stamp` with `attempts: []`, so `execution.terminal` is `"no_sql"` on
    # each of them -- and `Outcome.no_sql` is the one member that reads that field. Tested over
    # the whole vocabulary rather than trusted to `classify_outcome`'s line order, because the
    # failure is silent: the refusal keeps its `refused_by`, its `terminal_reason` and its stage,
    # and only `outcome` stops saying a decision was taken. `refusal_histogram` counts `refused`
    # rows, so the reasons would simply leave the histogram.
    leaked = sorted(
        reason
        for reason in REFUSED_BY_TO_STAGE
        if reason not in CRASH_REFUSED_BY
        and reason != ATTEMPT_CAP_REFUSED_BY
        and classify_outcome(
            error=None, refused_by=reason, has_sql=False, terminal=Outcome.no_sql.value
        ) is not Outcome.refused
    )
    if leaked:  # pragma: no cover - import-time guard
        raise AssertionError(
            f"declared refusals reclassified once the ledger was consulted: {leaked}. "
            "`Outcome.no_sql` means nothing decided and no statement ran; a turn something "
            "refused is a decision, and it carries an empty ledger too."
        )


_assert_refusal_tables_are_closed()
