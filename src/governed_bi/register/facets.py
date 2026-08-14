"""Facet channel configuration: targets, channels, and extraction.

Declares which scoring channels and asset types each facet uses, and whether
extraction runs. :class:`ChannelState` is three-valued so absence can be
``not_configured`` (correct) or ``failed`` (degradation). Judgement of
observed vs declared state is centralised in :func:`channel_anomaly`.
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
    "SCORING_CHANNELS",
    "expected_channel_state",
    "channel_anomaly",
    "is_degraded",
]


class Channel(str, Enum):
    """Scoring channel, or the extraction step that produces queries.

    ``extraction`` shares the three-valued vocabulary though it is not a scoring
    channel: it either ran, was not configured, or should have run.
    """

    #: BM25. Saturating normalisation so the score is absolute rather than relative to
    #: the current query's best hit.
    lexical = "lexical"
    #: Embedding cosine. Already bounded, so comparable across queries.
    semantic = "semantic"
    #: The model call that turns a question into query phrases.
    extraction = "extraction"


#: The members that actually score documents, so ``extraction`` cannot be fused.
#:
#: ``retrieve.fuse.fuse`` renormalises over the channels a caller says were consulted, and
#: the nearest source of that is the ``ran`` set — to which ``_rewritten_query`` also adds
#: ``extraction``. The weight mapping it is handed comes from
#: ``serve/runtime.py::channel_scale`` and carries only the two scoring channels, so passing
#: ``extraction`` through as consulted surfaces as a facet that retrieved nothing. Named here
#: rather than filtered at three call sites.
SCORING_CHANNELS: frozenset[Channel] = frozenset({Channel.lexical, Channel.semantic})


class ChannelState(str, Enum):
    """Whether a channel ran for one facet. Three-valued on purpose."""

    #: Executed and returned. Says nothing about whether it found anything: "ran and
    #: scored zero" is a measurement, not this field's job.
    ran = "ran"
    #: This facet does not use this channel. Correct behaviour when declared.
    not_configured = "not_configured"
    #: Should have run and did not: rate limit, dead endpoint, index build failure,
    #: unparseable extraction.
    failed = "failed"


class Anomaly(str, Enum):
    """Why an observed :class:`ChannelState` differs from the declared expectation.

    ``failed`` and ``unconfigured`` are degradation (fewer channels than claimed).
    ``extra_channel`` is configuration drift: report it, do not refuse the run.
    """

    #: Expected ``ran``, observed ``failed``.
    failed = "failed"
    #: Expected ``ran``, observed ``not_configured``.
    unconfigured = "unconfigured"
    #: Expected ``not_configured``, observed ``ran``.
    extra_channel = "extra_channel"


#: Which scoring channels each facet uses.
FACET_CHANNELS: Mapping[Stage, frozenset[Channel]] = {
    Stage.facet_schema: frozenset({Channel.lexical, Channel.semantic}),
    Stage.facet_term: frozenset({Channel.lexical, Channel.semantic}),
    Stage.facet_metric: frozenset({Channel.lexical, Channel.semantic}),
    Stage.facet_entity: frozenset({Channel.lexical, Channel.semantic}),
    # No lexical — term-frequency matching between NL questions rewards shared
    # function words.
    Stage.facet_example: frozenset({Channel.semantic}),
}

#: Facets whose queries come from model extraction. ``facet_schema`` is absent because
#: rewriting buys nothing measurable there (see :mod:`.citations`); its prompt stays in
#: ``PROMPT_REGISTRY`` as an unsent baseline.
FACET_EXTRACTS: frozenset[Stage] = frozenset(
    {
        Stage.facet_term,
        Stage.facet_metric,
        Stage.facet_entity,
        Stage.facet_example,
    }
)

#: Which asset types each facet retrieves over. ``column``, ``table`` and ``join``
#: share one facet: they arrive together in a real question, and splitting them
#: produces overlapping extraction calls.
FACET_TARGETS: Mapping[Stage, frozenset[AssetType]] = {
    Stage.facet_schema: frozenset({AssetType.schema}),
    Stage.facet_term: frozenset({AssetType.term}),
    Stage.facet_metric: frozenset({AssetType.metric}),
    Stage.facet_entity: frozenset({AssetType.table, AssetType.column, AssetType.join}),
    Stage.facet_example: frozenset({AssetType.few_shot}),
}

#: Asset types indexed but consumed by a gate rather than a facet:
#: ``negative_example`` is matched by ``negative_gate`` (a refuse decision, not a
#: ranking). Declared so :func:`_assert_every_indexed_type_has_a_consumer` closes.
GATE_CONSUMED_TYPES: frozenset[AssetType] = frozenset({AssetType.negative_example})


def expected_channel_state(facet: Stage, channel: Channel) -> ChannelState:
    """What ``channel``'s state must be for ``facet`` when nothing went wrong.

    Raises ``KeyError`` for a stage that is not a facet.
    """
    if facet not in FACET_CHANNELS:
        raise KeyError(f"{facet!r} is not a facet; see FACET_STAGES")
    if channel is Channel.extraction:
        return ChannelState.ran if facet in FACET_EXTRACTS else ChannelState.not_configured
    return ChannelState.ran if channel in FACET_CHANNELS[facet] else ChannelState.not_configured


def channel_anomaly(facet: Stage, channel: Channel, observed: ChannelState) -> Anomaly | None:
    """``None`` when ``observed`` matches the declared expectation, else why not.

    The single site for the three-way distinction.
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
    """True when the arm is running on fewer channels than it declares. A quotability
    input; ``extra_channel`` is drift, not degradation.
    """
    return channel_anomaly(facet, channel, observed) in (Anomaly.failed, Anomaly.unconfigured)


def _assert_tables_cover_every_facet() -> None:
    """Import-time: every facet appears in channel and target tables; no strays."""
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
    """Import-time: every indexed type is retrieved by a facet or gate-consumed."""
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
