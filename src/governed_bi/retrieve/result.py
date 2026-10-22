"""A channel that did not run is ``None``. A missing channel is never stored as zero."""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = ["Hit"]


@dataclass(frozen=True)
class Hit:
    channel: str
    asset_id: str
    asset_type: str
    lexical: float | None
    semantic: float | None
    queries: list[str] = field(default_factory=list)
    score: float | None = None
