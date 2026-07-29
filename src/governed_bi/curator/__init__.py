"""Curator: the build harness (``deepagents``).

The offline agent that *produces* the corpus, per-DB and independently. Not a
one-shot bootstrapper but a **permanent maintainer** (cold-start + drift-repair;
untended corpora rot ~95%→65%/month).

Proposer + adversary (D10): the proposer hypothesizes Inference-tier assets +
notes; an independent adversary tries to **refute** each before it commits
(``proposed -> draft``). **Facts** are generated programmatically and never
checked; the adversary boundary *is* the Facts/Inference boundary.

Modules map to the per-DB loop (``docs/curator.md``):

- ``profile``   - step 1: Facts tier, programmatic, no LLM.
- ``deep_agent`` - step 2: hypothesize Inference assets (Phase A/B).
- ``adversary`` - step 3: refute each proposed asset.
- ``pipeline``  - steps 4-5: self-eval & repair, then write the corpus.
"""

from __future__ import annotations

from .adversary import review
from .clarifications import (
    ClarificationRecord,
    Responder,
    StaticResponder,
    load_clarifications,
    quarantine_agent_answers,
    upsert_clarification_record,
    write_clarifications,
)
from .pipeline import build_baseline_corpus, build_curated_corpus, build_curated_corpus_with_sme
from .profile import profile_database
from .sme import SimulatedSme, assert_brief_no_leakage, build_sme_brief

__all__ = [
    "ClarificationRecord",
    "Responder",
    "SimulatedSme",
    "StaticResponder",
    "assert_brief_no_leakage",
    "build_baseline_corpus",
    "build_curated_corpus",
    "build_curated_corpus_with_sme",
    "build_sme_brief",
    "load_clarifications",
    "quarantine_agent_answers",
    "profile_database",
    "review",
    "upsert_clarification_record",
    "write_clarifications",
]
