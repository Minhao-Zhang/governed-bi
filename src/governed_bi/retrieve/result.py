"""Retrieval hit attribution — facet, channels, and the queries that produced it.

``lexical`` / ``semantic`` are ``None`` when that channel did not run for the
facet (e.g. ``example`` has no lexical). A missing channel is never a zero score.
"""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = ["Hit"]


@dataclass(frozen=True)
class Hit:
    """One asset found by one facet, with both channel components and provenance."""

    facet: str
    asset_id: str
    asset_type: str
    lexical: float | None
    semantic: float | None
    queries: list[str] = field(default_factory=list)
    score: float | None = None
