"""Curator loop steps 4-5 — self-eval & repair, then propose corpus.

4. **Self-eval & repair (inner loop, capped).** Assemble the draft layer → run
   the server pipeline on the DB's **train** questions → measure EX → diagnose
   failures → proposer patches (a failed question often *becomes* the gotcha
   skill that fixes it) → adversary re-checks → repeat until train-EX plateaus
   or the iteration/budget cap hits. Train-only.
5. **Propose corpus.** ``CI green ∧ (train-EX plateaued ∨ cap)`` → emit. Dev
   auto-accepts (``config.Settings.auto_accept_corpus``); prod opens a PR (D6).

**Done-enough criterion:** ``CI green ∧ (train-EX plateaued ∨ cap)`` — the CI
reference-integrity pass (``corpus.validate``) is the machine-checkable half.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..config import Settings
    from ..gateway import Gateway


def curate(gateway: "Gateway", db: str, settings: "Settings") -> None:
    """Run the full per-DB curation loop: profile → propose → adversary →
    self-eval/repair → propose corpus. Writes ``corpus/<db>/`` on completion."""
    raise NotImplementedError("curator loop pending; orchestrates profile/proposer/adversary")
