"""The declared abstention policy — rules, evidence and verdict (ADR 0013).

**What this is for.** The project's headline is a system that answers with confidence and
declines on purpose. On the v4 arm it declines by accident: 19 of 20 refusals end on
``r_table_not_licensed`` and all four clarifications licensed nothing, so nothing *decided* to
withhold — retrieval missed and Layer 6 mechanically blocked five statements later. This module
is the decision, written down: a named policy, a closed vocabulary of reasons, and the evidence
behind each one, evaluated **before** the agent spends its ``run_query`` budget.

**Where the line is, and which side this is on.** It computes no score. There is no
``confidence``, no ``certainty``, no threshold on a signal, and there will not be: a learned
abstainer was measured and failed (OOF AUC 0.597, worse than counting the agent's output tokens,
and its "unsure" bucket as likely to be right as its "correct" one — open-work.md §3.11), every
risk-coverage curve reads 0.7144 at the engine's own coverage, and ADR 0007 forbids a trust field
on the answer card. Reporting *why* the engine withheld is the ledger. Scoring *how sure it is*
is theatre, and ADR 0013 already named it that.

So every rule here is a **deterministic predicate over state the turn already recorded**. Each
one can be re-checked by a person reading the artifact, which is the property a score does not
have.

**Off by default.** ``abstention_policy_enabled`` ships ``False``, so v4 stays the control and
the change costs one paired arm to measure. The verdict is written on **every** turn including
the disabled ones, which is ``negative``'s argument one gate over: a gate that leaves a trace
only when it fires cannot afterwards be told from one that was never wired up.

**Not a node, and that is the point.** The graph adapter is
:func:`~governed_bi.serve.nodes.abstain.abstain_node`, in its own module. Everything here takes
:class:`AbstentionInputs` — a typed read-view of the eight facts the policy needs — and never
sees ``ServeState``'s 47-channel dict, so "which channels does this decision read" is answerable
by reading a class instead of a node body. :meth:`AbstentionInputs.from_state` is the one reader
of that dict in the whole policy, and the adapter names the two channels it keeps for itself.

**Why this is a separate file from the adapter.** Both halves fitted in
``serve/nodes/abstain.py`` at 470 lines, over ADR 0005 §6's soft cap of 400, and the split
that pays that back is the one the seam already drew: a pure decision over named facts on this
side, a translation to and from LangGraph's ``(state, config) -> dict`` on the other. The cost
of splitting is that the forbidden-word scan in
``tests/serve/test_the_abstention_policy_is_declared.py`` had to stop naming one file — it
derives its file set from where the policy's own symbols live now, so a further split moves the
scan with it rather than silently narrowing it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Callable, Literal

from governed_bi.register.facets import ChannelState
from governed_bi.register.stages import ABSTENTION_REASONS
from governed_bi.serve.context import EMPTY_CONTEXT
from governed_bi.serve.runtime import bool_knob

__all__ = [
    "ABSTENTION_POLICY",
    "ABSTENTION_RULES",
    "AbstentionInputs",
    "AbstentionPatch",
    "AbstentionRule",
    "abstention_evidence",
    "apply_policy",
    "decide",
]

#: The policy's name **and version**, carried on every verdict.
#:
#: Versioned because the rule set is the treatment: adding a fifth rule changes which turns are
#: delivered, and two arms whose verdicts both said ``abstention_policy`` would compare as one.
#: ``knobs_resolved`` carries the enabling knob; this says *which policy* the knob enabled.
ABSTENTION_POLICY = "context_sufficiency_v1"


@dataclass(frozen=True, slots=True)
class AbstentionInputs:
    """**Every channel the policy reads, and the whole of it.** The node's real interface.

    The enumeration, because it was previously nowhere. This node reads **eight** of
    ``ServeState``'s 47 channels. Six of them become the eight fields below — ``licensed``,
    ``delivery`` (both its ``context_block`` and its ``evicted`` counts), ``facets``,
    ``retrieved``, ``schemas``, and ``knobs_resolved``, from which
    :func:`~governed_bi.serve.runtime.bool_knob` resolves ``abstention_policy_enabled``. That
    knob is also probed as a *state key of its own*, which ``ServeState`` does not declare: on
    the ``accept`` graph ``input_schema`` drops it, so it can only arrive on the in-process path,
    and it is named here because an undeclared read is exactly the kind that goes unnoticed.

    The remaining two stay at the seam in
    :func:`~governed_bi.serve.nodes.abstain.abstain_node`, which names them: ``path_kind``,
    the already-ended short-circuit every node in this graph has — routing, not policy input,
    and read *before* the projection so that a turn which already ended cannot be crashed by
    this node's knob — and ``turn_id``, which
    :func:`~governed_bi.serve.events.rail_event_id` keys the rail row on.

    **Written:** ``abstention`` on every turn the node runs, plus ``path_kind`` and
    ``terminal_reason`` on a withhold. Nothing else; ``tests/serve`` asserts the update's shape
    rather than inferring it from a green turn.

    **Every field has a default**, so a test states the two or three facts its case is about
    rather than assembling a turn. That is the property being probed here: a rule is a predicate
    over a handful of named facts, and the 47-channel dict it used to be handed was an interface
    an order of magnitude wider than any of the implementations behind it.

    Not a ``TypedDict`` beside ``AbstentionVerdict`` in ``serve/state.py``: this is a *read*
    view, so the projection has to travel with it or there are two places that know how to pull
    ``licensed`` out of a state dict — and the projection calls ``bool_knob``, which would make
    ``state.py`` import ``serve/runtime.py`` and through it the whole of ``retrieve/``.
    """

    #: Table ids Layer 6 will accept for this turn, in the order ``connect`` left them.
    licensed: tuple[str, ...] = ()
    #: The rendered context block exactly as the model will be handed it.
    context_block: str = ""
    #: ``("facet_schema.semantic", ...)``, sorted. ``failed`` only — :func:`_failed_channels` says
    #: why ``not_configured`` is not one.
    failed_channels: tuple[str, ...] = ()
    #: Whole tables the char budget dropped from the block after they were licensed.
    tables_evicted: int = 0
    #: Column bodies the same budget dropped. Evidence only; no rule reads it.
    bodies_evicted: int = 0
    #: Schemas the turn worked from, stringified.
    schemas: tuple[str, ...] = ()
    #: ``retrieved["lexical_coverage"]``. **Evidence and never a rule** — see
    #: :func:`abstention_evidence` for why nothing may threshold it.
    question_terms_in_corpus: float | None = None
    #: The resolved ``abstention_policy_enabled`` knob. ``False`` ships, so this is the default.
    policy_enabled: bool = False

    @classmethod
    def from_state(cls, state: Mapping[str, Any]) -> AbstentionInputs:
        """**The only reader of the state dict in the policy.**

        One projection, so there is one answer to "where does ``licensed`` come from". Two
        functions that both knew would be the defect this file is a probe against: a second
        reader of a channel is where ``measure/gates.py`` came to witness "reached stamp" on
        ``Outcome.clarification`` and silently drop rows from a denominator.

        Eager, including on the turns where the knob is off. That is a handful of dict lookups
        for a decision nobody took, which is a real cost and a very small one — the alternative
        is a conditional projection, and a projection that runs differently depending on what it
        is about to be used for is two projections again. What the knob-off turn still does not
        pay for, and must not, is the *verdict*: :func:`_disabled` carries no evidence.
        """
        return cls(
            licensed=_licensed(state),
            context_block=_context_block(state),
            failed_channels=_failed_channels(state),
            tables_evicted=_evicted(state, "tables_dropped"),
            bodies_evicted=_evicted(state, "bodies_dropped"),
            schemas=tuple(str(s) for s in (state.get("schemas") or ())),
            question_terms_in_corpus=_lexical_coverage(state),
            policy_enabled=bool_knob(state, "abstention_policy_enabled"),
        )


@dataclass(frozen=True, slots=True)
class AbstentionPatch:
    """What the decision asks the graph to do — one field per observable effect.

    Three, because a node has exactly three ways to affect a turn and each is a separate claim:
    the channels it writes, the route it forces, and the row it puts on the timeline.
    :func:`~governed_bi.serve.nodes.abstain.abstain_node` translates this into LangGraph's dict
    and emits the row; it decides nothing, so no branch of the policy is reachable only by
    invoking a graph.

    ``rail_status`` could be derived from ``verdict["outcome"]`` instead, and is not on purpose:
    a node's effect on the timeline is a claim of its own — ``reflect`` and this node are both
    registered ``stream=False`` precisely because a disabled gate that still emitted two rows
    would have changed the event stream of every arm measured so far — and the adapter deriving
    it would mean the seam, not the policy, deciding what the timeline says.
    """

    #: Goes to the ``abstention`` channel, on **every** turn the node judges or declines to.
    verdict: dict[str, Any]
    #: The terminal this decision imposes, or ``None`` to leave the turn running. Written into
    #: the same channel ``route`` and ``connect`` write their declines into.
    path_kind: Literal["decline"] | None = None
    #: Status for the one rail row a *judged* turn emits. ``None`` means no row at all, which is
    #: what a disabled policy leaves behind — it is registered ``stream=False``, so
    #: ``wrap_node`` emits neither the start nor the resolve row either.
    rail_status: Literal["ok", "declined"] | None = None


@dataclass(frozen=True, slots=True)
class AbstentionRule:
    """One named reason to withhold, and the argument for it."""

    #: A member of :data:`~governed_bi.register.stages.ABSTENTION_REASONS`. Reaches
    #: ``terminal_reason``, so the refusal histogram separates it from every other terminal.
    reason: str
    #: Why withholding is the right answer here — the sentence a person gets when they ask why
    #: the engine did not answer. Not shown to the model: refusal copy is ``terminal.py``'s.
    why: str
    #: Reads the turn's projected facts and nothing else. No model, no threshold, no fitted
    #: parameter — and no state dict, so a rule cannot quietly grow a fifth input that the
    #: node's declared interface does not carry.
    fires: Callable[[AbstentionInputs], bool]


def _failed_channels(state: Mapping[str, Any]) -> tuple[str, ...]:
    """``["facet_schema.semantic", ...]`` for every channel that ran and errored.

    **``failed`` only, never ``not_configured``.** A lexical-only deployment reports
    ``not_configured`` on every semantic channel by design — ``register/facets.is_degraded``
    counts that as degradation, correctly, for a *health* gate — and abstaining on it would
    withhold every turn on a laptop with no embedder. The distinction is exactly the one
    ``ChannelState`` exists to carry: "there is nothing to embed with" is a deployment, "it
    should have run and did not" is a failure.
    """
    out: list[str] = []
    for facet, result in (state.get("facets") or {}).items():
        if not isinstance(result, Mapping):
            continue
        for channel, channel_state in (result.get("channels") or {}).items():
            value = getattr(channel_state, "value", channel_state)
            if str(value) == ChannelState.failed.value:
                out.append(f"{facet}.{channel}")
    return tuple(sorted(out))


def _evicted(state: Mapping[str, Any], field: str) -> int:
    delivery = state.get("delivery")
    if not isinstance(delivery, Mapping):
        return 0
    evicted = delivery.get("evicted")
    if not isinstance(evicted, Mapping):
        return 0
    value = evicted.get(field)
    return int(value) if isinstance(value, int) and not isinstance(value, bool) else 0


def _context_block(state: Mapping[str, Any]) -> str:
    delivery = state.get("delivery")
    if not isinstance(delivery, Mapping):
        return ""
    return str(delivery.get("context_block") or "")


def _licensed(state: Mapping[str, Any]) -> tuple[str, ...]:
    raw = state.get("licensed")
    return (
        tuple(str(x) for x in raw)
        if isinstance(raw, Sequence) and not isinstance(raw, str)
        else ()
    )


def _lexical_coverage(state: Mapping[str, Any]) -> float | None:
    """``None`` and not ``0.0`` when the turn did not measure it: an unmeasured share and a
    measured zero want opposite readings, and ``0`` is a measurement."""
    retrieved = state.get("retrieved")
    coverage = retrieved.get("lexical_coverage") if isinstance(retrieved, Mapping) else None
    if isinstance(coverage, (int, float)) and not isinstance(coverage, bool):
        return float(coverage)
    return None


#: The rules, **in evaluation order**, first match wins.
#:
#: The order is causes before consequences, so the reason a person is given names the thing they
#: can fix. A turn whose semantic channel errored will often also license nothing; reporting
#: ``nothing_licensed`` there sends someone to the corpus for what is a provider outage. Within
#: the remaining three the order is widest-first: no tables, then no context, then a table
#: dropped for space.
ABSTENTION_RULES: tuple[AbstentionRule, ...] = (
    AbstentionRule(
        reason="retrieval_channel_failed",
        why=(
            "a retrieval channel that was configured to run errored, so the tables this turn "
            "worked from were chosen by a retriever that is not the declared one. Answering "
            "would record the declared treatment and deliver a different one"
        ),
        fires=lambda inputs: bool(inputs.failed_channels),
    ),
    AbstentionRule(
        reason="nothing_licensed",
        why=(
            "retrieval licensed no table, so every statement the agent can write names a "
            "relation Layer 6 will refuse. The five run_query attempts would end where connect "
            "already ended, and the refusal would be attributed to the layer stack rather than "
            "to the empty shortlist that caused it"
        ),
        fires=lambda inputs: not inputs.licensed,
    ),
    AbstentionRule(
        reason="empty_context",
        why=(
            "the rendered context is empty, so the model has been handed the question and "
            "nothing else. Any statement it writes is invented rather than grounded"
        ),
        fires=lambda inputs: inputs.context_block.strip() in ("", EMPTY_CONTEXT),
    ),
    AbstentionRule(
        reason="licensed_table_evicted",
        why=(
            "the character budget dropped a whole licensed table before the model saw it, so "
            "this turn is asking for SQL over a relation it did not show. A table can be "
            "routed, licensed, counted as covered and then evicted; `evicted` is the only "
            "record that it happened"
        ),
        fires=lambda inputs: inputs.tables_evicted > 0,
    ),
)


def abstention_evidence(inputs: AbstentionInputs) -> dict[str, Any]:
    """The facts behind the decision, in a form a person can check against the record.

    Three questions, which is what open-work.md §4.1 asks an abstention to be able to answer:
    **what was licensed** (``licensed`` / ``n_licensed`` / ``schemas``), **what was missing**
    (``failed_channels``, ``tables_evicted``, ``bodies_evicted``, ``context_chars``), and **what
    the question asked for that the context could not supply** (``question_terms_in_corpus``).

    That last one is ``lexical_coverage`` — the share of the question's terms the corpus
    vocabulary has — and it is **evidence and never a rule**. No threshold reads it. A
    thresholded refusal gate is precisely what ``negative_tau`` ships ``UNSET`` rather than
    guess at ("an uncalibrated refusal gate is worse than none"), and putting one here under a
    different name would be the same uncalibrated gate with a better story.

    ``licensed`` is capped: it is evidence, and a 40-table list on every row is an artifact
    cost. The count beside it is not capped, so the cap cannot hide the size.
    """
    return {
        "n_licensed": len(inputs.licensed),
        "licensed": sorted(inputs.licensed)[:_EVIDENCE_LIST_CAP],
        "schemas": list(inputs.schemas),
        "context_chars": len(inputs.context_block),
        "tables_evicted": inputs.tables_evicted,
        "bodies_evicted": inputs.bodies_evicted,
        "failed_channels": list(inputs.failed_channels),
        "question_terms_in_corpus": inputs.question_terms_in_corpus,
    }


#: How many licensed keys the evidence carries. Evidence, not the record's ``licensed`` field —
#: that one is complete and is what a reader joins on.
_EVIDENCE_LIST_CAP = 12


def decide(inputs: AbstentionInputs) -> dict[str, Any]:
    """Run the policy over ``inputs``. **The whole decision, and a pure function of them.**

    Pure so the same verdict can be recomputed offline from an artifact's own fields, which is
    what makes "the engine can say why it withheld, in terms a person can check" true rather
    than asserted. Nothing here reads a model, a clock, the environment or the network — and,
    since the signature changed, nothing here reads the graph's state dict either: what it can
    see is exactly the eight fields of :class:`AbstentionInputs`.
    """
    for rule in ABSTENTION_RULES:
        if rule.fires(inputs):
            return {
                "policy": ABSTENTION_POLICY,
                "outcome": "withhold",
                "reason": rule.reason,
                # Every rule up to and including the one that fired. The ones after it were
                # never asked, and listing them would claim a check that did not happen.
                "rules_evaluated": [
                    r.reason
                    for r in ABSTENTION_RULES[: ABSTENTION_RULES.index(rule) + 1]
                ],
                "evidence": abstention_evidence(inputs),
            }
    return {
        "policy": ABSTENTION_POLICY,
        "outcome": "answer",
        "reason": None,
        "rules_evaluated": [r.reason for r in ABSTENTION_RULES],
        "evidence": abstention_evidence(inputs),
    }


#: The verdict written when the knob is off. **No evidence**: a verdict nobody took carrying the
#: facts behind it would be a cost with no reader, and an empty mapping cannot be mistaken for a
#: judgement. (The *projection* still runs on those turns — :meth:`AbstentionInputs.from_state`
#: says why that is the cheaper of the two mistakes — so what this saves is the payload, which is
#: the part that is written to every artifact row.)
def _disabled() -> dict[str, Any]:
    return {
        "policy": ABSTENTION_POLICY,
        "outcome": "disabled",
        "reason": None,
        "rules_evaluated": [],
        "evidence": {},
    }


def apply_policy(inputs: AbstentionInputs) -> AbstentionPatch:
    """The knob, then the policy. **Everything this node decides, and no state dict in sight.**

    Here rather than in :func:`~governed_bi.serve.nodes.abstain.abstain_node` so that the
    adapter has no branch of its own: a condition at the seam is a condition only a graph
    invocation can reach, and this one — "the knob is off, so record that a policy existed and
    let the turn through" — is half of what makes the knob honest.
    """
    if not inputs.policy_enabled:
        return AbstentionPatch(verdict=_disabled())
    verdict = decide(inputs)
    if verdict["outcome"] != "withhold":
        return AbstentionPatch(verdict=verdict, rail_status="ok")
    return AbstentionPatch(verdict=verdict, path_kind="decline", rail_status="declined")


def _assert_the_policy_speaks_the_declared_vocabulary() -> None:
    """Import-time: every rule's reason is declared, and the set is exactly the register's.

    Both directions. A rule with an undeclared reason writes a ``terminal_reason`` nothing can
    attribute; a declared reason with no rule is the machinery-with-no-wire this repository
    keeps auditing for, and it would read to anyone grepping the vocabulary as a decision the
    engine can take.

    Here and not in the adapter, so that importing the policy without the node still runs it —
    every test of the rules does exactly that.
    """
    reasons = [rule.reason for rule in ABSTENTION_RULES]
    if len(reasons) != len(set(reasons)):  # pragma: no cover - import-time guard
        raise AssertionError(f"two abstention rules share a reason: {sorted(reasons)}")
    if set(reasons) != ABSTENTION_REASONS:  # pragma: no cover - import-time guard
        raise AssertionError(
            "the policy's rules and register.stages.ABSTENTION_REASONS disagree: "
            f"rules-only={sorted(set(reasons) - ABSTENTION_REASONS)}, "
            f"declared-only={sorted(ABSTENTION_REASONS - set(reasons))}"
        )


_assert_the_policy_speaks_the_declared_vocabulary()
