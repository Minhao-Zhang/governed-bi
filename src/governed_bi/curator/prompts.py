"""Curator Phase A / Phase B system prompts (no deepagents import).

Both texts live in ``governed_bi.prompts`` (the versioned registry) and are
re-exported here under their historical names, so a curator run stamps the same
identity the serve path does and neither copy can drift from the other.
"""

from .. import prompts

_PHASE_A_PROMPT = prompts.get("curator_phase_a").text
_PHASE_B_PROMPT = prompts.get("curator_phase_b").text

# Back-compat alias.
_CURATOR_PROMPT = _PHASE_A_PROMPT
