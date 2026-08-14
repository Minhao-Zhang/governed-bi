"""Walk ``facet_channels`` against the declared channel table.

Judgement lives in ``register.facets``; this module produces the triples.
:func:`facets_degraded` for quotability (no ``extra_channel``);
:func:`channel_anomalies` for diagnostics (includes ``extra_channel``).
"""


from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..register.facets import Channel, ChannelState, channel_anomaly, is_degraded
from ..register.stages import Stage

__all__ = ["channel_observations", "facets_degraded", "channel_anomalies"]


def channel_observations(
    facet_channels: Any,
) -> tuple[tuple[Stage, Channel, ChannelState], ...]:
    """Every ``(facet, channel, observed)`` triple a recorded ``facet_channels`` names.

    Empty for ``None`` or ``{}`` — the fan-out did not run, which is a stage-conditional
    absence and **not** "no channel differed"; ``gates.py`` restricts the population first.

    Raises ``ValueError`` on an undeclared facet or channel name: the record and the
    register disagreeing about what a facet is cannot be answered with "nothing wrong here".
    """
    if not isinstance(facet_channels, Mapping):
        return ()
    out: list[tuple[Stage, Channel, ChannelState]] = []
    for facet_name, channels in facet_channels.items():
        if not isinstance(channels, Mapping):
            raise ValueError(
                f"facet_channels[{facet_name!r}] is {type(channels).__name__}, not a "
                "channel -> state mapping, so no channel state can be judged"
            )
        facet = _member(Stage, facet_name, "facet")
        for channel_name, observed in channels.items():
            out.append(
                (
                    facet,
                    _member(Channel, channel_name, "channel"),
                    _member(ChannelState, observed, "channel state"),
                )
            )
    return tuple(out)


def facets_degraded(facet_channels: Any) -> bool:
    """True when some facet ran on fewer channels than ``FACET_CHANNELS`` declares.

    The value ``facet_degraded`` carries. ``False`` here means every declared channel of
    every facet in the record reported ``ran``; on a record with no ``facet_channels`` at
    all it also returns ``False``, which is why the field's declared absence is
    ``not_applicable`` and the stamp writes ``None`` rather than calling this on a turn
    whose fan-out never ran.
    """
    return any(
        is_degraded(facet, channel, observed)
        for facet, channel, observed in channel_observations(facet_channels)
    )


def channel_anomalies(facet_channels: Any) -> dict[str, str]:
    """``"facet.channel" -> Anomaly`` for every state that differs from its declaration.

    The diagnostic beside :func:`facets_degraded`'s verdict: ``True`` says the run is not
    quotable, this says which channel of which facet made it so — and it also names
    ``extra_channel``, which must be reported and must not refuse a run.
    """
    out: dict[str, str] = {}
    for facet, channel, observed in channel_observations(facet_channels):
        anomaly = channel_anomaly(facet, channel, observed)
        if anomaly is not None:
            out[f"{facet.value}.{channel.value}"] = anomaly.value
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
