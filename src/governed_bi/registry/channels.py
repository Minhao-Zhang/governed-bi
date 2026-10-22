"""``ChannelState`` is three-valued so absence can be ``not_configured`` or ``failed``.

Observed versus declared state is judged only in ``channel_anomaly``.
"""

from __future__ import annotations

from enum import Enum
from typing import Mapping

from .assets import INDEXED_TYPES, AssetType
from .stages import CHANNEL_STAGES, Stage

__all__ = [
    "Channel",
    "ChannelState",
    "Anomaly",
    "DECLARED_CHANNELS",
    "CHANNEL_EXTRACTS",
    "CHANNEL_TARGETS",
    "CHECK_CONSUMED_TYPES",
    "SCORING_CHANNELS",
    "expected_channel_state",
    "channel_anomaly",
    "is_degraded",
]


class Channel(str, Enum):
    """``extraction`` shares this vocabulary and is not a scoring channel."""

    #: Saturating normalisation: the score is absolute, not relative to this query's best hit.
    lexical = "lexical"
    semantic = "semantic"
    extraction = "extraction"


#: Scoring channels only. Fuse renormalises over consulted channels, and the ran set
#: also contains ``extraction``, whose weights are absent, so it would look like a
#: channel that retrieved nothing. Named here rather than filtered at each call site.
SCORING_CHANNELS: frozenset[Channel] = frozenset({Channel.lexical, Channel.semantic})


class ChannelState(str, Enum):
    #: Says nothing about hits: ran and scored zero is a measurement.
    ran = "ran"
    #: This channel does not use this channel. Correct when declared.
    not_configured = "not_configured"
    #: Should have run and did not.
    failed = "failed"


class Anomaly(str, Enum):
    """``failed`` and ``unconfigured`` are degradation. ``extra_channel`` is drift: report it, do not refuse the run."""

    failed = "failed"
    unconfigured = "unconfigured"
    extra_channel = "extra_channel"


DECLARED_CHANNELS: Mapping[Stage, frozenset[Channel]] = {
    Stage.channel_schema: frozenset({Channel.lexical, Channel.semantic}),
    Stage.channel_term: frozenset({Channel.lexical, Channel.semantic}),
    Stage.channel_metric: frozenset({Channel.lexical, Channel.semantic}),
    Stage.channel_entity: frozenset({Channel.lexical, Channel.semantic}),
    # No lexical: term frequency between questions rewards shared function words.
    Stage.channel_example: frozenset({Channel.semantic}),
}

#: ``channel_schema`` is absent: rewriting buys nothing measurable there.
CHANNEL_EXTRACTS: frozenset[Stage] = frozenset(
    {
        Stage.channel_term,
        Stage.channel_metric,
        Stage.channel_entity,
        Stage.channel_example,
    }
)

#: ``column``, ``table`` and ``join`` share one channel: splitting them overlaps extraction.
CHANNEL_TARGETS: Mapping[Stage, frozenset[AssetType]] = {
    Stage.channel_schema: frozenset({AssetType.schema}),
    Stage.channel_term: frozenset({AssetType.term}),
    Stage.channel_metric: frozenset({AssetType.metric}),
    Stage.channel_entity: frozenset({AssetType.table, AssetType.column, AssetType.join}),
    Stage.channel_example: frozenset({AssetType.few_shot}),
}

#: Indexed, but matched by a check: a refuse decision, not a ranking.
CHECK_CONSUMED_TYPES: frozenset[AssetType] = frozenset({AssetType.negative_example})


def expected_channel_state(stage: Stage, channel: Channel) -> ChannelState:
    if stage not in DECLARED_CHANNELS:
        raise KeyError(f"{stage!r} is not a retrieval channel; see CHANNEL_STAGES")
    if channel is Channel.extraction:
        return ChannelState.ran if stage in CHANNEL_EXTRACTS else ChannelState.not_configured
    return ChannelState.ran if channel in DECLARED_CHANNELS[stage] else ChannelState.not_configured


def channel_anomaly(stage: Stage, channel: Channel, observed: ChannelState) -> Anomaly | None:
    """The single site for the three-way distinction."""
    expected = expected_channel_state(stage, channel)
    if observed is expected:
        return None
    if observed is ChannelState.failed:
        return Anomaly.failed
    if observed is ChannelState.not_configured:
        return Anomaly.unconfigured
    return Anomaly.extra_channel


def is_degraded(stage: Stage, channel: Channel, observed: ChannelState) -> bool:
    """``extra_channel`` is drift, not degradation."""
    return channel_anomaly(stage, channel, observed) in (Anomaly.failed, Anomaly.unconfigured)


def _assert_tables_cover_every_channel() -> None:
    missing_channels = set(CHANNEL_STAGES) - set(DECLARED_CHANNELS)
    missing_targets = set(CHANNEL_STAGES) - set(CHANNEL_TARGETS)
    if missing_channels or missing_targets:  # pragma: no cover - import-time guard
        raise AssertionError(
            "retrieval-channel tables incomplete: channels missing "
            f"{sorted(s.value for s in missing_channels)}, targets missing "
            f"{sorted(s.value for s in missing_targets)}"
        )
    stray = (set(DECLARED_CHANNELS) | set(CHANNEL_TARGETS) | CHANNEL_EXTRACTS) - set(CHANNEL_STAGES)
    if stray:  # pragma: no cover - import-time guard
        raise AssertionError(
            f"retrieval-channel tables name stages that are not retrieval channels: {sorted(s.value for s in stray)}"
        )


def _assert_every_indexed_type_has_a_consumer() -> None:
    retrieved: set[AssetType] = set()
    for targets in CHANNEL_TARGETS.values():
        retrieved |= targets
    reachable = retrieved | CHECK_CONSUMED_TYPES

    orphaned = INDEXED_TYPES - reachable
    if orphaned:  # pragma: no cover - import-time guard
        raise AssertionError(
            f"indexed asset types no retrieval channel retrieves and no check consumes: "
            f"{sorted(t.value for t in orphaned)}"
        )
    phantom = reachable - INDEXED_TYPES
    if phantom:  # pragma: no cover - import-time guard
        raise AssertionError(
            f"a retrieval channel or a check targets asset types that are not indexed: "
            f"{sorted(t.value for t in phantom)}"
        )
    overlap = retrieved & CHECK_CONSUMED_TYPES
    if overlap:  # pragma: no cover - import-time guard
        raise AssertionError(
            f"asset types both retrieved and check-consumed: "
            f"{sorted(t.value for t in overlap)}. A type whose hit is a decision "
            "must not also be ranked into context."
        )


_assert_tables_cover_every_channel()
_assert_every_indexed_type_has_a_consumer()
