"""Arm 3 — the gold semantic layer: a deterministic de-obfuscation oracle (D4).

**Not an AI build.** The gold layer is the de-obfuscation key read back into the
*same* asset schema:

- rename map      → real column/table names (Inference ``description`` / naming)
- decoy manifest  → ``reliability.status = suspect`` on every manifest decoy
- original schema → the withheld FK graph (``join`` assets)

``provenance.source = gold``, ``confidence = 1.0``. No AI, no owner, cannot
drift. Skills (Markdown) are curator-only, so Arm 3 has none — which is why Arm 2
can *exceed* this reference line on skill-sensitive questions. Facts are
identical across arms; the gold-diff compares the Inference tier only.

BIRD-only: dropped for the enterprise fork (no ground-truth gold exists there).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..corpus import Corpus


def build_gold_corpus(db: str, manifests_dir: Path) -> "Corpus":
    """Derive the Arm-3 gold corpus deterministically from the BIRD manifests
    (rename map + decoy manifest + original schema). No LLM involved."""
    raise NotImplementedError("gold oracle pending; reads BIRD-Obfuscation manifests")
