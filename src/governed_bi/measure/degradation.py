"""Walk ``channel_scoring`` against the declared channel table.

Judgement lives in ``registry.channels``; this module produces the triples.
:func:`channels_degraded` for quotability (no ``extra_channel``);
:func:`channel_anomalies` for diagnostics (includes ``extra_channel``).
"""


from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..registry.channels import Channel, ChannelState, channel_anomaly, is_degraded
from ..registry.stages import Stage

__all__ = ["channel_observations", "channels_degraded", "channel_anomalies"]


def channel_observations(
    channel_scoring: Any,
) -> tuple[tuple[Stage, Channel, ChannelState], ...]:
    """Every ``(stage, scoring channel, observed)`` triple a recorded ``channel_scoring`` names.

    Empty for ``None`` or ``{}`` — the fan-out did not run, which is a stage-conditional
    absence and **not** "no channel differed"; ``checks.py`` restricts the population first.

    Raises ``ValueError`` on an undeclared channel or channel name: the record and the
    register disagreeing about what a channel is cannot be answered with "nothing wrong here".
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
    """True when some channel ran on fewer channels than ``DECLARED_CHANNELS`` declares.

    The value ``channel_degraded`` carries. ``False`` here means every declared channel of
    every channel in the record reported ``ran``; on a record with no ``channel_scoring`` at
    all it also returns ``False``, which is why the field's declared absence is
    ``not_applicable`` and the finish writes ``None`` rather than calling this on a turn
    whose fan-out never ran.
    """
    return any(
        is_degraded(stage, channel, observed)
        for stage, channel, observed in channel_observations(channel_scoring)
    )


def channel_anomalies(channel_scoring: Any) -> dict[str, str]:
    """``"channel.channel" -> Anomaly`` for every state that differs from its declaration.

    The diagnostic beside :func:`channels_degraded`'s result: ``True`` says the run is not
    quotable, this says which channel of which channel made it so — and it also names
    ``extra_channel``, which must be reported and must not refuse a run.
    """
    out: dict[str, str] = {}
    for stage, channel, observed in channel_observations(channel_scoring):
        anomaly = channel_anomaly(stage, channel, observed)
        if anomaly is not None:
            out[f"{stage.value}.{channel.value}"] = anomaly.value
    return out


def _member(enum: Any, raw: Any, what: str) -> Any:
    """``raw`` as a member of ``enum``, or a ``ValueError`` naming what was declared."""
    try:
        return enum(raw)
    except ValueError:
        raise ValueError(
            f"{raw!r} is not a declared {what}: the record and "
            f"{enum.__module__}.{enum.__name__} disagree, so this channel state cannot be "
            "compared to an expectation. Declared: "
            f"{sorted(m.value for m in enum)}"
        ) from None
