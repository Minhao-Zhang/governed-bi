"""``abstain`` — the declared abstention policy (ADR 0013).

**What this is for.** The project's headline is a system that answers with confidence and
declines on purpose. On the v4 arm it declines by accident: 19 of 20 refusals end on
``r_table_not_licensed`` and all four clarifications licensed nothing, so nothing *decided* to
withhold — retrieval missed and Layer 6 mechanically blocked five statements later. This node is
the decision, written down: a named policy, a closed vocabulary of reasons, and the evidence
behind each one, evaluated **before** the agent spends its ``run_query`` budget.

**Where the line is, and which side this is on.** It computes no score. There is no
``confidence``, no ``certainty``, no threshold on a signal, and there will not be: a learned
abstainer was measured and failed (OOF AUC 0.597, worse than counting the agent's output tokens,
and its "unsure" bucket as likely to be right as its "correct" one — open-work.md §3.11), every
risk-coverage curve reads 0.7144 at the engine's own coverage, and ADR 0007 forbids a trust field
on the answer card. Reporting *why* the engine withheld is the ledger. Scoring *how sure it is*
is theatre, and `docs/analysis/strategy-checkpoint-2026-08-11.md` §5.6 already named it that.

So every rule here is a **deterministic predicate over state the turn already recorded**. Each
one can be re-checked by a person reading the artifact, which is the property a score does not
have.

**Off by default.** ``abstention_policy_enabled`` ships ``False``, so v4 stays the control and
the change costs one paired arm to measure. Registered in ``graph.py`` with ``stream=False``, so
a disabled policy adds no timeline rows; it emits its own single row when it judged something.
The verdict is written on **every** turn including the disabled ones, which is ``negative``'s
argument one gate over: a gate that leaves a trace only when it fires cannot afterwards be told
from one that was never wired up.

**A failure here is a crashed turn, not a silent answer.** No ``try`` swallows anything: unlike
``reflect``, this node decides, and a policy that fails open on its own exception is a policy
that stops being one exactly when something is wrong. ``wrap_node`` records the crash.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Callable

from langchain_core.runnables import RunnableConfig

from governed_bi.register.facets import ChannelState
from governed_bi.register.stages import ABSTENTION_REASONS, Stage
from governed_bi.serve.context import EMPTY_CONTEXT
from governed_bi.serve.events import emit, rail_event_id
from governed_bi.serve.runtime import bool_knob
from governed_bi.serve.state import TERMINAL_PATH_KINDS

__all__ = [
    "ABSTENTION_POLICY",
    "ABSTENTION_RULES",
    "AbstentionRule",
    "abstention_evidence",
    "decide",
    "abstain_node",
]

#: The policy's name **and version**, carried on every verdict.
#:
#: Versioned because the rule set is the treatment: adding a fifth rule changes which turns are
#: delivered, and two arms whose verdicts both said ``abstention_policy`` would compare as one.
#: ``knobs_resolved`` carries the enabling knob; this says *which policy* the knob enabled.
ABSTENTION_POLICY = "context_sufficiency_v1"


@dataclass(frozen=True, slots=True)
class AbstentionRule:
    """One named reason to withhold, and the argument for it."""

    #: A member of :data:`~governed_bi.register.stages.ABSTENTION_REASONS`. Reaches
    #: ``terminal_reason``, so the refusal histogram separates it from every other terminal.
    reason: str
    #: Why withholding is the right answer here — the sentence a person gets when they ask why
    #: the engine did not answer. Not shown to the model: refusal copy is ``terminal.py``'s.
    why: str
    #: Reads the turn's state and nothing else. No model, no threshold, no fitted parameter.
    fires: Callable[[Mapping[str, Any]], bool]


def _failed_channels(state: Mapping[str, Any]) -> list[str]:
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
    return sorted(out)


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


def _licensed(state: Mapping[str, Any]) -> list[str]:
    raw = state.get("licensed")
    return [str(x) for x in raw] if isinstance(raw, Sequence) and not isinstance(raw, str) else []


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
        fires=lambda state: bool(_failed_channels(state)),
    ),
    AbstentionRule(
        reason="nothing_licensed",
        why=(
            "retrieval licensed no table, so every statement the agent can write names a "
            "relation Layer 6 will refuse. The five run_query attempts would end where connect "
            "already ended, and the refusal would be attributed to the layer stack rather than "
            "to the empty shortlist that caused it"
        ),
        fires=lambda state: not _licensed(state),
    ),
    AbstentionRule(
        reason="empty_context",
        why=(
            "the rendered context is empty, so the model has been handed the question and "
            "nothing else. Any statement it writes is invented rather than grounded"
        ),
        fires=lambda state: _context_block(state).strip() in ("", EMPTY_CONTEXT),
    ),
    AbstentionRule(
        reason="licensed_table_evicted",
        why=(
            "the character budget dropped a whole licensed table before the model saw it, so "
            "this turn is asking for SQL over a relation it did not show. A table can be "
            "routed, licensed, counted as covered and then evicted; `evicted` is the only "
            "record that it happened"
        ),
        fires=lambda state: _evicted(state, "tables_dropped") > 0,
    ),
)


def abstention_evidence(state: Mapping[str, Any]) -> dict[str, Any]:
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
    licensed = _licensed(state)
    retrieved = state.get("retrieved")
    coverage = retrieved.get("lexical_coverage") if isinstance(retrieved, Mapping) else None
    return {
        "n_licensed": len(licensed),
        "licensed": sorted(licensed)[:_EVIDENCE_LIST_CAP],
        "schemas": [str(s) for s in (state.get("schemas") or ())],
        "context_chars": len(_context_block(state)),
        "tables_evicted": _evicted(state, "tables_dropped"),
        "bodies_evicted": _evicted(state, "bodies_dropped"),
        "failed_channels": _failed_channels(state),
        "question_terms_in_corpus": (
            float(coverage) if isinstance(coverage, (int, float)) and not isinstance(coverage, bool)
            else None
        ),
    }


#: How many licensed keys the evidence carries. Evidence, not the record's ``licensed`` field —
#: that one is complete and is what a reader joins on.
_EVIDENCE_LIST_CAP = 12


def decide(state: Mapping[str, Any]) -> dict[str, Any]:
    """Run the policy over ``state``. **The whole decision, and a pure function of state.**

    Pure so the same verdict can be recomputed offline from an artifact's own fields, which is
    what makes "the engine can say why it withheld, in terms a person can check" true rather
    than asserted. Nothing here reads a model, a clock, the environment or the network.
    """
    for rule in ABSTENTION_RULES:
        if rule.fires(state):
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
                "evidence": abstention_evidence(state),
            }
    return {
        "policy": ABSTENTION_POLICY,
        "outcome": "answer",
        "reason": None,
        "rules_evaluated": [r.reason for r in ABSTENTION_RULES],
        "evidence": abstention_evidence(state),
    }


#: The verdict written when the knob is off. No evidence: gathering it for a decision nobody
#: took is a cost with no reader, and an empty mapping cannot be mistaken for a judgement.
def _disabled() -> dict[str, Any]:
    return {
        "policy": ABSTENTION_POLICY,
        "outcome": "disabled",
        "reason": None,
        "rules_evaluated": [],
        "evidence": {},
    }


def abstain_node(state: dict, config: RunnableConfig) -> dict:
    """Decide whether this turn should be answered, before the agent spends an attempt.

    Declares ``config`` so :func:`~governed_bi.serve.wrap.wrap_node` forwards it — the knob is
    read through :func:`~governed_bi.serve.runtime.bool_knob`, whose precedence is state, then
    ``knobs_resolved``, then the register.
    """
    if state.get("path_kind") in TERMINAL_PATH_KINDS:
        return {}
    if not bool_knob(state, "abstention_policy_enabled"):
        return {"abstention": _disabled()}

    verdict = decide(state)
    emit(
        kind="rail",
        step=Stage.abstain.value,
        status="declined" if verdict["outcome"] == "withhold" else "ok",
        event_id=rail_event_id(Stage.abstain.value, state),
        detail={"policy": verdict["policy"], "reason": verdict["reason"]},
    )
    if verdict["outcome"] != "withhold":
        return {"abstention": verdict}
    return {
        "abstention": verdict,
        "path_kind": "decline",
        # The reason **is** the terminal reason. One string, in one vocabulary, in the channel
        # `route` and `connect` already write their declines into — rather than a second field
        # only a new reader would know to open. Its reader is
        # `eval/report.py::refusal_histogram`, which had to be built: ADR 0013 §2 claimed three
        # existing ones and none of them read the vocabulary.
        "terminal_reason": verdict["reason"],
    }


def _assert_the_policy_speaks_the_declared_vocabulary() -> None:
    """Import-time: every rule's reason is declared, and the set is exactly the register's.

    Both directions. A rule with an undeclared reason writes a ``terminal_reason`` nothing can
    attribute; a declared reason with no rule is the machinery-with-no-wire this repository
    keeps auditing for, and it would read to anyone grepping the vocabulary as a decision the
    engine can take.
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
