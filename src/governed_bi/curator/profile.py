"""Curator loop step 1 — Profile (Facts, programmatic).

Read the catalog + sample data and emit the **Facts tier** for every table and
column: physical_name, physical_type, logical_type, nullable, is_unique,
sample_values, row_count. Deterministic; no LLM; correct in every eval arm.
These are never proposed and never checked by the adversary (D10).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..corpus import TableAsset
    from ..gateway import Gateway


def profile_database(gateway: "Gateway", db: str) -> list["TableAsset"]:
    """Emit Facts-only table assets (Inference tier left empty for the proposer)."""
    raise NotImplementedError("Facts profiling pending the gateway catalog reader")
