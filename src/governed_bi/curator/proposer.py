"""Curator loop step 2 — Propose (Inference + skills).

The proposer hypothesizes the Inference tier and authors skills, grounding each
claim by probing the DB (free exploration confined to this pocket):

- descriptions for cryptic physical names (the physical↔meaning bridge)
- joins (value-overlap + seed-SQL join patterns)
- reliability caveats (execute-and-observe against the traps; general
  data-quality anomalies, not BIRD-specific detectors — P2, transfers to enterprise deployments)
- terms / synonyms (paraphrase-robust retrieval; the obfuscation rewrite dim)
- metrics / rules (from BIRD ``evidence`` + recurring computations)
- routing / gotcha / pattern skills (the highest-value, curator-only output)

Distillation discipline: *select and distill, never dump* (per-pattern few-shot
cap; skills are distilled routing/gotchas, not transcripts).

Every proposed asset starts at ``provenance.status = proposed``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..corpus import Asset, TableAsset


def propose(facts: list["TableAsset"], seed_queries: list[dict]) -> list["Asset"]:
    """Propose Inference-tier assets + skills from Facts + train seed queries.

    ``seed_queries`` are the DB's ``train_final.jsonl`` rows (question + gold SQL
    + BIRD ``evidence``). **Train only — never test (the leakage wall).**
    """
    raise NotImplementedError("proposer pending; runs on the deepagents harness")
