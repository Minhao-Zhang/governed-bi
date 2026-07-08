"""Server step 10 — answer assembly + reliability stamp (D5).

Best-effort tiering with a **reliability stamp** that has teeth (not just a
footer). Uncertainty aggregates into the stamp: low-confidence join used ·
fenced-raw fallback · Corrective-RAG triggered · suspect column in scope →
lower tier → differential handling. High-stakes (leadership / PII) → human
sign-off or SQL-only.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ReliabilityTier(str, Enum):
    governed = "governed"  # high stamp
    lineage = "lineage"  # medium stamp
    fenced_raw = "fenced_raw"  # low stamp
    refused = "refused"  # fail-closed


@dataclass(frozen=True)
class Answer:
    tier: ReliabilityTier
    text: str | None
    sql: str | None
    provenance: dict  # source tier + confidence + which uncertainty flags fired
    escalation: str | None = None  # populated on refuse (canned blob)


def assemble(*args, **kwargs) -> Answer:
    """Build the answer + reliability stamp from the executed result and the
    accumulated uncertainty signals."""
    raise NotImplementedError("answer assembly pending")
