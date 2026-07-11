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
from .clarify_loop import (
    Responder,
    StaticResponder,
    default_parse,
    emit_clarifications,
    resolve_clarifications,
)
from .llm_proposer import LlmProposer
from .loop import CurationResult, curate
from .profile import profile_database
from .proposer import HeuristicProposer, Proposer

__all__ = [
    "CurationResult",
    "HeuristicProposer",
    "LlmProposer",
    "Proposer",
    "Responder",
    "StaticResponder",
    "curate",
    "default_parse",
    "emit_clarifications",
    "profile_database",
    "resolve_clarifications",
    "review",
]
