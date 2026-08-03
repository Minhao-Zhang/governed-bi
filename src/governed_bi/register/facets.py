"""What each retrieval facet is configured to do, declared rather than assumed.

A facet is **one query-construction strategy + one set of target asset types + one
channel configuration**. The five of them fan out concurrently in a single
LangGraph super-step, so latency is ``max(branches)`` while cost is
``sum(branches)`` — fan-out buys latency, not money. That is why the two facets
calling no model are the cheap ones, and why extraction runs on a small model.

**The reason this file exists is** :class:`ChannelState`. Whether a channel ran has
to be three-valued, and a boolean cannot carry it:

* The ``example`` facet has **no lexical channel by design** — term-frequency
  matching between two natural-language questions rewards shared function words.
  So ``lexical`` not running there is *correct*.
* The same absence on ``entity`` means the BM25 index died and that arm is now
  running on one channel.

Under a boolean, a gate reading "did any channel not run" either fails on every
run or acquires a special case exempting ``example`` — and **that special case is
where the next silent degradation hides.** v1's version of this incident had no
field at all: a rate-limited embedder published a schema-pick accuracy
that re-measured 21 points higher once quota was free. [retired]

And ``not_configured`` is judged **against this table**, never taken on the
producer's word. :func:`channel_anomaly` is the single place that judgement is
made, because "is this absence a problem?" answered independently at three call
sites is how two of them get it wrong.
"""

from __future__ import annotations

from enum import Enum
from typing import Mapping

from .assets import INDEXED_TYPES, AssetType
from .stages import FACET_STAGES, Stage

__all__ = [
    "Channel",
    "ChannelState",
    "Anomaly",
    "FACET_CHANNELS",
    "FACET_EXTRACTS",
    "FACET_TARGETS",
    "GATE_CONSUMED_TYPES",
    "expected_channel_state",
    "channel_anomaly",
    "is_degraded",
]


class Channel(str, Enum):
    """A way of scoring a candidate, or the step that produces the queries.

    ``extraction`` is here even though it is not a scoring channel, because its
    failure mode is identical in shape — it either ran, was never configured for
    this facet, or should have run and did not — and a separate two-valued field
    for it would be the "not measured is not zero" defect in a different variable.
    One three-valued vocabulary covering all three is worth the category stretch.
    """

    #: BM25. Saturating normalisation so the score is absolute rather than relative
    #: to the current query's best hit.
    lexical = "lexical"
    #: Embedding cosine. Already bounded, so comparable across queries.
    semantic = "semantic"
    #: The model call that turns a question into query phrases.
    extraction = "extraction"


class ChannelState(str, Enum):
    """Whether a channel ran for one facet. Three-valued on purpose."""

    #: Executed and returned. Says nothing about whether it found anything — "ran
    #: and scored zero" is a measurement, and not this field's job.
    ran = "ran"
    #: This facet does not use this channel. Correct behaviour when declared.
    not_configured = "not_configured"
    #: Should have run and did not: rate limit, dead endpoint, index build failure,
    #: unparseable extraction.
    failed = "failed"


class Anomaly(str, Enum):
    """Why an observed :class:`ChannelState` differs from the declared expectation.

    Three distinct facts, and collapsing them was the earlier draft's mistake —
    ADR 0005 §2.3 says "only ``failed`` is degradation", which is right about
    ``failed`` and silent about the other two:

    * :attr:`failed` and :attr:`unconfigured` are **degradation**: the arm is now
      running on fewer channels than it claims. Both feed the quotability gate.
    * :attr:`extra_channel` is **configuration drift**, not degradation: a channel
      ran that this facet does not declare. It should be reported and it must not
      refuse a run, because more retrieval is not a broken run — but a table and
      a producer disagreeing is exactly the shape that gave v1 two definitions of
      "excluded".
    """

    #: Expected ``ran``, observed ``failed``.
    failed = "failed"
    #: Expected ``ran``, observed ``not_configured`` — the channel silently stopped
    #: being wired up. A gate that only looked for ``failed`` would pass this.
    unconfigured = "unconfigured"
    #: Expected ``not_configured``, observed ``ran``.
    extra_channel = "extra_channel"


#: Which scoring channels each facet uses.
FACET_CHANNELS: Mapping[Stage, frozenset[Channel]] = {
    Stage.facet_schema: frozenset({Channel.lexical, Channel.semantic}),
    Stage.facet_term: frozenset({Channel.lexical, Channel.semantic}),
    Stage.facet_metric: frozenset({Channel.lexical, Channel.semantic}),
    Stage.facet_entity: frozenset({Channel.lexical, Channel.semantic}),
    # No lexical channel — see the module docstring.
    Stage.facet_example: frozenset({Channel.semantic}),
}

#: Facets whose queries come from model extraction. The others use the raw question.
#:
#: That the two non-extracting facets are also the cheapest is what lets the
#: pre-fan-out gates cost about 10ms instead of a model call.
FACET_EXTRACTS: frozenset[Stage] = frozenset(
    {Stage.facet_term, Stage.facet_metric, Stage.facet_entity}
)

#: Which asset types each facet retrieves over.
#:
#: ``column``, ``table`` and ``join`` share one facet because in a real question
#: they arrive together: "which customers have the highest order amount" is a
#: customer table, an order table, an amount column and the join between them, as
#: one thought. Splitting them produces three highly overlapping extraction calls.
FACET_TARGETS: Mapping[Stage, frozenset[AssetType]] = {
    Stage.facet_schema: frozenset({AssetType.schema}),
    Stage.facet_term: frozenset({AssetType.term}),
    Stage.facet_metric: frozenset({AssetType.metric}),
    Stage.facet_entity: frozenset({AssetType.table, AssetType.column, AssetType.join}),
    Stage.facet_example: frozenset({AssetType.few_shot}),
}

#: Asset types that enter the index but are consumed by a gate rather than a facet.
#:
#: Exactly one: ``negative_example`` is matched by the pre-fan-out
#: ``negative_gate``, whose hit is a *decision* (refuse) rather than a *ranking*.
#: Declared so that :func:`_assert_every_indexed_type_has_a_consumer` can close the
#: loop — an indexed type nobody retrieves and nobody gates is precisely how v1
#: made this same type structurally unreachable, with a budget lookup that
#: defaulted to zero.
GATE_CONSUMED_TYPES: frozenset[AssetType] = frozenset({AssetType.negative_example})


def expected_channel_state(facet: Stage, channel: Channel) -> ChannelState:
    """What ``channel``'s state must be for ``facet`` when nothing went wrong.

    Raises ``KeyError`` for a stage that is not a facet, deliberately: a caller
    asking this about ``Stage.route`` has a bug, and a plausible answer would hide
    it.
    """
    if facet not in FACET_CHANNELS:
        raise KeyError(f"{facet!r} is not a facet; see FACET_STAGES")
    if channel is Channel.extraction:
        return ChannelState.ran if facet in FACET_EXTRACTS else ChannelState.not_configured
    return ChannelState.ran if channel in FACET_CHANNELS[facet] else ChannelState.not_configured


def channel_anomaly(facet: Stage, channel: Channel, observed: ChannelState) -> Anomaly | None:
    """``None`` when ``observed`` matches the declared expectation, else why not.

    Centralised so the three-way distinction is made once. A caller deciding for
    itself whether a given ``not_configured`` is fine needs both this table and the
    reasoning, and v1's evidence is that the second copy of that reasoning is
    where the divergence happens.
    """
    expected = expected_channel_state(facet, channel)
    if observed is expected:
        return None
    if observed is ChannelState.failed:
        return Anomaly.failed
    if observed is ChannelState.not_configured:
        return Anomaly.unconfigured
    return Anomaly.extra_channel


def is_degraded(facet: Stage, channel: Channel, observed: ChannelState) -> bool:
    """True when the arm is running on fewer channels than it declares.

    The quotability input. ``extra_channel`` is deliberately **not** degradation —
    it is configuration drift, reported separately, because refusing a run for
    having done more retrieval than declared would be a gate that punishes the
    wrong thing.
    """
    return channel_anomaly(facet, channel, observed) in (Anomaly.failed, Anomaly.unconfigured)


def _assert_tables_cover_every_facet() -> None:
    """Import-time closure check.

    A facet missing from one of these tables would otherwise surface as a
    ``KeyError`` on the one question that reached it, in production, rather than at
    import in every environment.
    """
    missing_channels = set(FACET_STAGES) - set(FACET_CHANNELS)
    missing_targets = set(FACET_STAGES) - set(FACET_TARGETS)
    if missing_channels or missing_targets:  # pragma: no cover - import-time guard
        raise AssertionError(
            "facet tables incomplete: channels missing "
            f"{sorted(s.value for s in missing_channels)}, targets missing "
            f"{sorted(s.value for s in missing_targets)}"
        )
    stray = (set(FACET_CHANNELS) | set(FACET_TARGETS) | FACET_EXTRACTS) - set(FACET_STAGES)
    if stray:  # pragma: no cover - import-time guard
        raise AssertionError(
            f"facet tables name non-facet stages: {sorted(s.value for s in stray)}"
        )


def _assert_every_indexed_type_has_a_consumer() -> None:
    """Import-time closure check across two modules.

    Every type that enters the index must be reachable by something. v1's
    ``NegativeExampleAsset`` was in the index and in no budget, so
    ``budgets.get(cls, 0)`` dropped it from every ranked pass — it existed, it was
    embedded, and nothing could ever retrieve it. This is the assertion that
    absence could not have survived.
    """
    retrieved: set[AssetType] = set()
    for targets in FACET_TARGETS.values():
        retrieved |= targets
    reachable = retrieved | GATE_CONSUMED_TYPES

    orphaned = INDEXED_TYPES - reachable
    if orphaned:  # pragma: no cover - import-time guard
        raise AssertionError(
            f"indexed asset types no facet retrieves and no gate consumes: "
            f"{sorted(t.value for t in orphaned)}"
        )
    phantom = reachable - INDEXED_TYPES
    if phantom:  # pragma: no cover - import-time guard
        raise AssertionError(
            f"facet or gate targets asset types that are not indexed: "
            f"{sorted(t.value for t in phantom)}"
        )
    overlap = retrieved & GATE_CONSUMED_TYPES
    if overlap:  # pragma: no cover - import-time guard
        raise AssertionError(
            f"asset types both retrieved and gate-consumed: "
            f"{sorted(t.value for t in overlap)}. A type whose hit is a decision "
            "must not also be ranked into context."
        )


_assert_tables_cover_every_facet()
_assert_every_indexed_type_has_a_consumer()
