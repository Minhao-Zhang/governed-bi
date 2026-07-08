"""Refuse-gate eval (Architecture §8; D5).

BIRD questions are all answerable, so they do **not** test the refuse-gate. This
needs a held-out **unanswerable** set: cross-DB + removed-coverage cases
(auto-generated) plus a small hand-built out-of-scope set. Scored on:

- **refusal accuracy** — refuses the unanswerable (recall of refusal)
- **false-refusal rate** — on the answerable test set (precision of refusal)
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RefuseGateResult:
    refusal_accuracy: float  # on the unanswerable set
    false_refusal_rate: float  # on the answerable set


def eval_refuse_gate(db: str) -> RefuseGateResult:
    """Score the refuse-gate against the held-out unanswerable + answerable sets."""
    raise NotImplementedError("refuse-gate eval pending; needs the unanswerable set")
