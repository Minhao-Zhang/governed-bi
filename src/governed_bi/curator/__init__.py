"""Curator: the build harness (``deepagents``).

The offline agent that *produces* the corpus, per-DB and independently. Not a
one-shot bootstrapper but a **permanent maintainer** (cold-start + drift-repair;
untended corpora rot ~95%→65%/month).

Proposer + adversary (D10): the proposer hypothesizes Inference-tier assets +
skills; an independent adversary tries to **refute** each before it commits
(``proposed -> draft``). **Facts** are generated programmatically and never
checked; the adversary boundary *is* the Facts/Inference boundary.

Modules map to the per-DB loop (``docs/curator.md``):

- ``profile``   - step 1: Facts tier, programmatic, no LLM.
- ``proposer``  - step 2: hypothesize Inference assets + skills.
- ``adversary`` - step 3: refute each proposed asset.
- ``loop``      - steps 4-5: self-eval & repair, then propose corpus.
"""

from __future__ import annotations

from .adversary import review
from .build import build_facts_corpus
from .enrich import enrich_table
from .clarify_loop import (
    Responder,
    StaticResponder,
    default_parse,
    emit_clarifications,
    resolve_clarifications,
)
from .llm_proposer import LlmProposer
from .loop import CurationResult, curate
from .pipeline import build_curated_corpus, build_curated_corpus_with_sme
from .profile import profile_database
from .proposer import HeuristicProposer, Proposer
from .sme import SimulatedSme, assert_brief_no_leakage, build_sme_brief

__all__ = [
    "CurationResult",
    "HeuristicProposer",
    "LlmProposer",
    "Proposer",
    "Responder",
    "SimulatedSme",
    "StaticResponder",
    "assert_brief_no_leakage",
    "build_curated_corpus",
    "build_curated_corpus_with_sme",
    "build_facts_corpus",
    "build_sme_brief",
    "curate",
    "default_parse",
    "emit_clarifications",
    "enrich_table",
    "profile_database",
    "resolve_clarifications",
    "review",
]
