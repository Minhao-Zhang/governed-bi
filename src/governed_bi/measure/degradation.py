"""``channels_degraded`` omits ``extra_channel``. ``channel_anomalies`` includes it and must not refuse a run."""


from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..registry.channels import Channel, ChannelState, channel_anomaly, is_degraded
from ..registry.stages import Stage

__all__ = ["channel_observations", "channels_degraded", "channel_anomalies"]


def channel_observations(
    channel_scoring: Any,
) -> tuple[tuple[Stage, Channel, ChannelState], ...]:
    """Empty means the fan-out did not run, not that no channel differed.

    An undeclared name raises: the record and the register disagreeing is not "nothing wrong".
    """
    if not isinstance(channel_scoring, Mapping):
        return ()
    out: list[tuple[Stage, Channel, ChannelState]] = []
    for channel_name, channels in channel_scoring.items():
        if not isinstance(channels, Mapping):
            raise ValueError(
                f"channel_scoring[{channel_name!r}] is {type(channels).__name__}, not a "
                "channel -> state mapping, so no channel state can be judged"
            )
        channel = _member(Stage, channel_name, "channel")
        for channel_name, observed in channels.items():
            out.append(
                (
                    channel,
                    _member(Channel, channel_name, "channel"),
                    _member(ChannelState, observed, "channel state"),
                )
            )
    return tuple(out)


def channels_degraded(channel_scoring: Any) -> bool:
    """Also ``False`` when ``channel_scoring`` is missing, so finish writes ``None`` instead of calling this on a turn
    whose fan-out never ran."""
    return any(
        is_degraded(stage, channel, observed)
        for stage, channel, observed in channel_observations(channel_scoring)
    )


def channel_anomalies(channel_scoring: Any) -> dict[str, str]:
    """Names ``extra_channel`` too. Report it; do not refuse the run."""
    out: dict[str, str] = {}
    for stage, channel, observed in channel_observations(channel_scoring):
        anomaly = channel_anomaly(stage, channel, observed)
        if anomaly is not None:
            out[f"{stage.value}.{channel.value}"] = anomaly.value
    return out


def _member(enum: Any, raw: Any, what: str) -> Any:
    try:
        return enum(raw)
    except ValueError:
        raise ValueError(
            f"{raw!r} is not a declared {what}: the record and "
            f"{enum.__module__}.{enum.__name__} disagree, so this channel state cannot be "
            "compared to an expectation. Declared: "
            f"{sorted(m.value for m in enum)}"
        ) from None
