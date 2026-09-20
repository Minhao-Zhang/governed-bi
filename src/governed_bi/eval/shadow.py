"""What the gates prod enforces would have done to an arm that ran without them.

**The measured configuration is not the served one, and that is now on purpose.**
``tools/run_datalake_eval.py`` builds ``GovernancePolicy(guard_rules_enabled={})`` while
``api/graph_app.py`` enables all six rules, so every number in ``runs/eval/`` describes an
engine with the input guard off. Turning the guard *on* in the benchmark would close the
mismatch and destroy the more valuable half of the measurement: a turn the guard refuses is a
turn whose answer nobody ever sees, so an enforced arm can report what governance costs in
**coverage** and can never report what it cost in **right answers**.

So the arm runs permissive and the gates are replayed over it afterwards. Enforcement destroys
the counterfactual; replay preserves it. This module is the replay, and it is the general form
of something this repository already does for one gate: ``computed_fingerprint`` /
``computed_correct`` exist solely to reconstruct what enforcing abstention would have hidden.

**Post-hoc by design, not by expedience.** Every gate below is a pure function of facts the row
already carries — a property engineered on purpose and stated outright by
``serve/abstention.py`` ("the verdict is a pure function of state, so a reader can recompute it
from the row"). Replaying is therefore exact rather than approximate; it needs no change to the
measured path, so an arm scored this way stays comparable to one scored before this module
existed; and it runs against the seven arms already on disk. An in-graph shadow channel would
have bought none of those three, and would have made every future arm a new treatment.

**The one gate that is not a function of the row is** :data:`BI_SCOPE_GATE`, which asks a model. It
reads only the question, so it runs as its own pass over the question set
(``tools/bi_scope_probe.py``) and joins here on ``question_id``.

Three tiers, and the tier travels with the number because it says what the number may be read
as:

* :data:`Counterfactual.replay` — the gate's inputs are fixed before the model acts and are on the row.
  The projection is **exact**: prod would have ended the turn there, so everything observed
  downstream is precisely what prod would have thrown away.
* :data:`Counterfactual.probe` — exact in the same sense, but the verdict came from a separate pass.
* :data:`Counterfactual.bound` — the gate changes what the model *sees* (a restrictive grant, a denied
  column), so "prod would have refused this statement" is observable and "what prod would have
  answered instead" is not. Counted, never folded into a projected score.

**What this cannot tell you.** It measures what governance *costs*, never what it *buys*. The
BIRD question set carries no attack traffic, so a gate that fires zero times here is cheap and
not thereby useless; its value is measured against the 16 ``[[guard_case]]`` tables that
``govern/adversarial_run.py`` drives. Two instruments, and a report printing them in one table
will be read as one.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any

from governed_bi.govern.guard import guard
from governed_bi.govern.policy import GUARD_RULE_IDS, GovernancePolicy
from governed_bi.measure.population import Population, TurnRow
from governed_bi.measure.selective import (
    DeliveryPolicy,
    NestedPolicies,
    compare_policies,
    engine_policy,
)
from governed_bi.serve.abstention import ABSTENTION_POLICY, AbstentionInputs, decide
from governed_bi.serve.context import EMPTY_CONTEXT

__all__ = [
    "ABSTENTION",
    "BI_SCOPE_GATE",
    "DETERMINISTIC_GUARD",
    "ShadowGate",
    "ShadowProjection",
    "Counterfactual",
    "abstention_gate",
    "bi_scope_gate",
    "deterministic_guard_gate",
    "prod_projection",
]


class Counterfactual(Enum):
    """What kind of counterfactual a gate's verdict supports. See the module docstring."""

    #: Exact, from facts on the row.
    replay = "replay"
    #: Exact, from a separate pass that read only the question.
    probe = "probe"
    #: Observable as a refusal, not as a projected score.
    bound = "bound"


#: Gate ids, spelled from the register's own vocabulary where there is one. A second spelling
#: of a rule id is how two consumers of one mapping came to disagree — the 2026-09-18 review
#: §1.1, where ``guard_rules_enabled={BI_SCOPE_RULE_ID: True}`` read as "the guard is on" and
#: meant five of six were off.
ABSTENTION = f"abstention:{ABSTENTION_POLICY}"
DETERMINISTIC_GUARD = "guard:deterministic"
BI_SCOPE_GATE = "guard:g_bi_scope"


@dataclass(frozen=True)
class ShadowGate:
    """One gate prod enforces, replayed over an arm that ran without it.

    ``fires`` is a set of unit ids and not a count, for :class:`DeliveryPolicy`'s reason: two
    policies are comparable only if each names the exact turns it withheld.

    ``unevaluable`` is the safety property of the whole module. A gate whose inputs are missing
    from the rows must not report an empty ``fires`` — "the check did not happen" and "the
    check passed" are the distinction ``measure/gates.py`` spends a third verdict on, and
    collapsing it is how an uninstrumented arm once passed every gate in the suite. A non-empty
    string here makes :func:`prod_projection` refuse.
    """

    gate_id: str
    tier: Counterfactual
    fires: frozenset[str]
    #: What the gate is, in one line, for a reader of a projection they did not run.
    why: str
    #: Non-empty when the gate could not be evaluated. Blocks the projection.
    unevaluable: str = ""
    #: Turns whose stage the arm never reached, so the gate could not have fired on them.
    #:
    #: **Not the same absence as** :attr:`unevaluable`, and the two are kept apart for the
    #: reason ``measure/gates.py`` keeps ``cannot_evaluate`` apart from ``pass``. A turn that
    #: ended in a clarification before the facet fan-out legitimately carries no
    #: ``facet_channels`` — the served path writes ``abstention: null`` on exactly those turns,
    #: because the node never ran — while an arm where *no* row carries the field is an arm
    #: this gate cannot be replayed over at all. Counting the first as the second refuses every
    #: projection; counting the second as the first certifies a configuration nothing observed.
    #: Rendered, never silent.
    not_reached: frozenset[str] = frozenset()


def abstention_gate(rows: Sequence[TurnRow]) -> ShadowGate:
    """Replay the declared abstention policy (ADR 0013) over rows measured with it off.

    The policy is four deterministic predicates over ``licensed``, the rendered context block,
    the facet channel states and the eviction counts. Three of those are on the row verbatim.
    The fourth — the block itself — is not, and is **not** guessed at: the only rule that reads
    it tests it for emptiness, and the row's ``context_hash`` answers that exactly, because an
    empty block has one of two known digests and nothing else has to be reconstructed.

    Two different absences, kept apart (see :attr:`ShadowGate.not_reached`). An arm where *no*
    row carries ``facet_channels`` predates the field and cannot be replayed at all. An arm
    where some rows carry it and others do not is an instrumented arm whose missing rows ended
    before the fan-out — on v4 those are exactly the four zero-licensed clarifications — and
    the rows themselves agree with treating those as un-judged: their ``abstention`` is
    ``null``, which the record register defines as "the turn ended before the node", and a
    different fact from ``disabled``.

    Classifying them wrongly would not move the projection in either direction — a turn that
    ended before this node did not answer, so it is not in the delivered set and subtracting
    it is arithmetic with no effect — but it would put "would refuse 4" in a report about a
    gate that never saw them. The rows are the arm's own record of what ran; the report should
    not contradict them.

    **An arm that actually ran the policy is refused rather than replayed.** Its withheld turns
    carry no answer to compare against: enforcement already spent the counterfactual this
    module exists to preserve, which is the whole argument in the module docstring, applied to
    the one gate where somebody might reach for ``--abstain`` first.
    """
    enforced = sorted(
        {
            str(row["abstention"]["outcome"])
            for row in rows
            if isinstance(row.get("abstention"), Mapping)
            and row["abstention"].get("outcome") in ("answer", "withhold")
        }
    )
    if enforced:
        return ShadowGate(
            ABSTENTION,
            Counterfactual.replay,
            frozenset(),
            _ABSTENTION_WHY,
            unevaluable=(
                f"this arm ran with the policy enabled (rows record {enforced}), so there is "
                "nothing to shadow: it already decided, the turns it withheld carry no answer "
                "to compare against, and the counterfactual this module exists to preserve was "
                "spent when the arm was measured. Read the arm's own abstention block instead"
            ),
        )

    empty_digests = {
        hashlib.sha256(text.encode("utf-8")).hexdigest() for text in ("", EMPTY_CONTEXT)
    }
    instrumented = [row for row in rows if row.get("facet_channels") is not None]
    if rows and not instrumented:
        return ShadowGate(
            ABSTENTION,
            Counterfactual.replay,
            frozenset(),
            _ABSTENTION_WHY,
            unevaluable=(
                f"no row of {len(rows)} carries facet_channels, which is a policy input. This "
                "arm predates the field; it recorded no absence of it, so there is nothing to "
                "replay"
            ),
        )
    not_reached = frozenset(
        _unit(row)
        for row in rows
        if row.get("facet_channels") is None or row.get("context_hash") is None
    )

    fires = {
        _unit(row)
        for row in instrumented
        if _unit(row) not in not_reached
        and decide(AbstentionInputs.from_state(_as_state(row, empty_digests)))["outcome"]
        == "withhold"
    }
    return ShadowGate(
        ABSTENTION, Counterfactual.replay, frozenset(fires), _ABSTENTION_WHY, not_reached=not_reached
    )


_ABSTENTION_WHY = (
    "ADR 0013 context_sufficiency_v1, evaluated before the agent spends an attempt: a failed "
    "retrieval channel, nothing licensed, an empty context block, or a licensed table evicted"
)

#: Stands in for a context block the row does not carry, on the rows whose digest says it was
#: not empty. The only rule reading the block tests it for emptiness, so any non-empty string
#: is the whole of what that rule can learn — and a placeholder that is obviously *not* a
#: context block is better than a plausible one, which would invite a fifth rule to read it.
_NON_EMPTY = "<non-empty, per context_hash>"


def deterministic_guard_gate(
    rows: Sequence[TurnRow], questions: Mapping[str, Mapping[str, Any]]
) -> ShadowGate:
    """Replay the five deterministic guard rules over the arm's own questions.

    ``questions`` maps ``question_id`` to the dataset record, because the guard reads the
    question text and the row carries only the id.

    **The question alone, and not the question plus its evidence**, because that is what the
    served path screens: ``Session.turn`` writes ``question`` and ``evidence`` into two
    channels, ``guard_node`` reads ``state["question"]``, and only ``agent_core`` composes the
    pair into the message the model sees. Screening the pair here would over-count the gate
    against a system that does not screen it. (The 2026-09-18 review measured both, correctly
    for its question — it was establishing that the rules fire on *nothing* benign, and the
    pair is the stricter of the two tests. A projection needs the exact one.)

    Not an exposure: ``evidence`` reaches the model unscreened but no client can write it.
    ``ServeInput`` is one key wide and ``accept`` derives the turn from the last human message,
    so on the served path the evidence channel is never populated from a request at all.
    """
    absent = [_unit(row) for row in rows if _unit(row) not in questions]
    if absent:
        return ShadowGate(
            DETERMINISTIC_GUARD,
            Counterfactual.replay,
            frozenset(),
            _GUARD_WHY,
            unevaluable=(
                f"{len(absent)} of {len(rows)} row(s) name a question the dataset does not "
                f"hold (first: {absent[0]}). This gate reads the question text, so an "
                "unmatched id is a turn it was not run on"
            ),
        )

    policy = GovernancePolicy(guard_rules_enabled={rule: True for rule in GUARD_RULE_IDS})
    fires = frozenset(
        _unit(row)
        for row in rows
        if guard(str(questions[_unit(row)].get("question") or ""), policy)["outcome"] == "blocked"
    )
    return ShadowGate(DETERMINISTIC_GUARD, Counterfactual.replay, fires, _GUARD_WHY)


_GUARD_WHY = (
    "the five deterministic rules of govern/guard.py::GUARD_RULES, which api/graph_app.py "
    "enables on every served turn and tools/run_datalake_eval.py enables on none"
)


def bi_scope_gate(rows: Sequence[TurnRow], verdicts: Mapping[str, str]) -> ShadowGate:
    """Join the model-backed scope gate's verdicts, from ``tools/bi_scope_probe.py``.

    ``verdicts`` maps ``question_id`` to a ``GuardVerdict`` outcome. A separate pass because
    this is the one gate that is not a function of the row: it asks a model whether the
    question is a BI task at all.

    ``error_failed_open`` counts as **not** firing, which is what the served path does with it.
    It is not silently folded away — :meth:`ShadowProjection.render` prints the count, because
    a gate that failed open on a third of the arm is a different fact from one that cleared it.
    """
    unjudged = [_unit(row) for row in rows if _unit(row) not in verdicts]
    if unjudged:
        return ShadowGate(
            BI_SCOPE_GATE,
            Counterfactual.probe,
            frozenset(),
            _BI_SCOPE_WHY,
            unevaluable=(
                f"{len(unjudged)} of {len(rows)} row(s) have no scope verdict (first: "
                f"{unjudged[0]}). Run tools/bi_scope_probe.py over this arm's question set; "
                "an unjudged turn is not a cleared one"
            ),
        )
    fires = frozenset(row for row in map(_unit, rows) if verdicts[row] == "blocked")
    return ShadowGate(BI_SCOPE_GATE, Counterfactual.probe, fires, _BI_SCOPE_WHY)


_BI_SCOPE_WHY = (
    "g_bi_scope, the model-backed scope gate: is this a BI question at all? Enabled on every "
    "served turn, run on no measured one, and the only prod gate that is not a pure function "
    "of the recorded row"
)


@dataclass(frozen=True)
class ShadowProjection:
    """An arm as measured, and the same arm with the prod gates enforced on paper.

    ``nested`` is a :class:`~governed_bi.measure.selective.NestedPolicies` and never a
    hypothesis test, for that class's own reason: a gate can only remove turns and changes no
    turn's grade, so the discordant cell one way is zero by construction and a p-value would
    only restate the subset relation. The finding is a count of right answers lost.
    """

    arm: Population
    gates: tuple[ShadowGate, ...]
    nested: NestedPolicies

    @property
    def priced(self) -> tuple[ShadowGate, ...]:
        """Gates whose refusals are folded into :attr:`nested`."""
        return tuple(g for g in self.gates if g.tier is not Counterfactual.bound)

    @property
    def bounds_only(self) -> tuple[ShadowGate, ...]:
        """Gates counted but not projected. See :data:`Counterfactual.bound`."""
        return tuple(g for g in self.gates if g.tier is Counterfactual.bound)

    def render(self) -> list[str]:
        """The projection: one line per gate, then the trade. Counts only, and no rates."""
        lines = [
            f"prod projection over {self.arm.describe()}",
            "  the arm ran permissive; each gate below is one api/graph_app.py enforces",
        ]
        for gate in self.gates:
            unreached = (
                f"  ({len(gate.not_reached)} turn(s) ended before its stage)"
                if gate.not_reached
                else ""
            )
            lines.append(
                f"  [{gate.tier.value:6}] {gate.gate_id:38} would refuse "
                f"{len(gate.fires)}{unreached}"
            )
        lines.append(f"  {self.nested.render()}")
        for gate in self.bounds_only:
            lines.append(
                f"  NOTE {gate.gate_id} is tier 'bound': it changes what the model sees, so "
                "its refusals are counted and its cost in right answers is not projected"
            )
        return lines


def prod_projection(arm: Population, gates: Sequence[ShadowGate]) -> ShadowProjection:
    """Enforce ``gates`` over ``arm`` on paper. Raises on a gate that could not be evaluated.

    Raising rather than reporting: a projection missing a gate is a claim about the served
    configuration built from a proper subset of it, and it reads identically to one where that
    gate never fires. The caller is told which gate and why, and can drop it from the list
    deliberately if that is really what it means.
    """
    unevaluable = [g for g in gates if g.unevaluable]
    if unevaluable:
        detail = "; ".join(f"{g.gate_id}: {g.unevaluable}" for g in unevaluable)
        raise ValueError(
            f"{len(unevaluable)} of {len(gates)} gate(s) could not be evaluated over "
            f"{arm.describe()}, so there is no projection: {detail}"
        )
    withheld: set[str] = set()
    for gate in gates:
        if gate.tier is not Counterfactual.bound:
            withheld |= gate.fires
    permissive = engine_policy(arm)
    prod = DeliveryPolicy(
        label="prod (the served guard and abstention policy, enforced on paper)",
        population=arm,
        delivered=permissive.delivered - withheld,
    )
    nested = compare_policies(permissive, prod)
    if not isinstance(nested, NestedPolicies):  # pragma: no cover - arithmetic guarantees it
        raise AssertionError(
            "a shadow projection withholds turns and adds none, so the two policies are "
            f"nested by construction; compare_policies returned {type(nested).__name__}"
        )
    return ShadowProjection(arm=arm, gates=tuple(gates), nested=nested)


def _unit(row: TurnRow) -> str:
    return str(row.get("question_id"))


def _as_state(row: TurnRow, empty_digests: frozenset[str] | set[str]) -> dict[str, Any]:
    """An eval row, in the shape the abstention policy's own projection reads.

    **The inverse of** ``eval/projection.py::project_turn``, and the reason this module has no
    rules of its own. ``AbstentionInputs.from_state`` is documented as "the only reader of the
    state dict in the policy" precisely so that there is one answer to "where does ``licensed``
    come from", and a replay that re-derived ``failed_channels`` and the eviction counts here
    would be the second reader that docstring forbids — one that drifts the first time a fifth
    rule reads a sixth channel. ``tools/check_one_implementation.py`` refused the copy, which
    is the gate working.

    Three of the four channels are renamed rather than recomputed: ``facets`` is flattened to
    ``facet_channels`` on the row and has to be nested back, ``delivery.evicted`` is projected
    as ``context_evicted``, and ``retrieved.lexical_coverage`` as ``lexical_coverage``.

    ``context_block`` is the exception, because the row does not carry it. The only rule that
    reads it tests it for emptiness, so the digest answers the whole of what that rule can
    learn, and the placeholder is deliberately not a plausible context block — a realistic one
    would invite a fifth rule to read it and quietly get an answer about this function.
    """
    channels = row.get("facet_channels")
    facets = (
        {facet: {"channels": dict(states)} for facet, states in channels.items()}
        if isinstance(channels, Mapping)
        else {}
    )
    return {
        "licensed": list(row.get("licensed") or ()),
        "delivery": {
            "context_block": "" if row.get("context_hash") in empty_digests else _NON_EMPTY,
            "evicted": row.get("context_evicted") or {},
        },
        "facets": facets,
        "schemas": list(row.get("schemas") or ()),
        "retrieved": {"lexical_coverage": row.get("lexical_coverage")},
        "abstention_policy_enabled": True,
    }
