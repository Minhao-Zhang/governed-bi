"""Shared serve runtime knobs (config + candidate depth + fuse weights).

One home so facet / pass-two / route / assemble do not each redefine the same
helpers (ADR 0005 §6 one-implementation gate).
"""

from __future__ import annotations

from typing import Any, Mapping

__all__ = [
    "DEFAULT_CANDIDATE_DEPTH",
    "DEFAULT_CONTEXT_BUDGET",
    "FUSE_WEIGHTS",
    "candidate_depth",
    "configurable",
    "facet_hits",
]

DEFAULT_CANDIDATE_DEPTH = 50
DEFAULT_CONTEXT_BUDGET = 80_000
FUSE_WEIGHTS: Mapping[str, float] = {"lexical": 0.5, "semantic": 0.5}


def configurable(config: Mapping[str, Any] | None) -> Mapping[str, Any]:
    """``config[\"configurable\"]`` when present; else empty mapping."""
    if not config:
        return {}
    raw = config.get("configurable") if isinstance(config, Mapping) else None
    return raw if isinstance(raw, Mapping) else {}


def candidate_depth(state: Mapping[str, Any]) -> int:
    """Pass-one / pass-two candidate pool size (state, then knobs, else default)."""
    raw = state.get("candidate_depth")
    if raw is None:
        knobs = state.get("knobs_resolved") or {}
        if isinstance(knobs, Mapping):
            raw = knobs.get("candidate_depth")
    try:
        return int(raw) if raw is not None else DEFAULT_CANDIDATE_DEPTH
    except (TypeError, ValueError):
        return DEFAULT_CANDIDATE_DEPTH


def facet_hits(facet_result: Any) -> list[Any]:
    """Hits list from a FacetResult dict or object."""
    if facet_result is None:
        return []
    if isinstance(facet_result, Mapping):
        return list(facet_result.get("hits") or ())
    return list(getattr(facet_result, "hits", None) or ())
