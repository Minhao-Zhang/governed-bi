"""A recorded ``facet_channels`` judged against the declared channel table.

**This module does not decide anything.** ``register.facets`` owns both judgements —
:func:`~governed_bi.register.facets.channel_anomaly` for *how* an observation differs from
its declaration and :func:`~governed_bi.register.facets.is_degraded` for *which* of those
differences makes an arm unquotable. Both had **zero call sites outside tests**: nothing
wrote ``facet_degraded``, so ``measure/gates.py`` reported ``[pass] facet_channels 0.0000
over 'stub' n=3 (fan-out ran)`` on an arm with no index, and a gate whose input nobody
produces is worse than an absent gate because the summary says the run was checked.

What lives here is the **walk**: turning one record's ``{facet: {channel: state}}`` into
the triples those two functions take. It lives in ``measure/`` and not in ``serve/``
because both sides need it — ``serve.stamp`` writes the boolean onto the record and
``measure.gates`` names the drift when the gate fails — and ``measure/`` is a layer both
can import. Two walks would be two answers to "which channels did this turn run", which is
the shape ``tools/check_one_implementation.py`` exists to prevent.

The two entry points are deliberately not one:

* :func:`facets_degraded` is the quotability input, and ``extra_channel`` is **not** in it.
  A run that retrieved on more channels than it declared is drift, not degradation, and a
  gate that refused it would punish the wrong thing (ADR 0005 §2.3).
* :func:`channel_anomalies` is the diagnostic, and ``extra_channel`` **is** in it, because
  a table and a producer disagreeing is the shape that gave v1 two definitions of
  "excluded".
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
    absence and **not** a clean turn. The caller must not read the empty tuple as "no
    channel differed"; ``gates.py`` handles that by restricting the population first.

    Raises ``ValueError`` for a facet or channel name the register does not declare.
    Skipping it silently would be this system's own defect: an unrecognised key means the
    record and the register disagree about what a facet is, and answering "nothing wrong
    here" is the answer that cannot be right.
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
