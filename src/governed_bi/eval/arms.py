"""The three-arm eval harness (Architecture §8; D4).

Runs the held-out ``test_final.jsonl`` questions through the server against each
of the three corpus arms and scores EX. Also collects the free behavioral
signals from the manifest + logs:

- **decoy-touch rate** — share of questions where the agent used a
  manifest-flagged fake column/table (Server §"three points" #1 drives this → 0
  in dev via suspect hard-block).
- **governed-path adherence** — share resolved via the semantic layer.

Reports Arm2/Arm3-vs-Arm1 (the moat proof) and Arm2-vs-Arm3 (curator quality).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Arm(str, Enum):
    no_layer = "no_layer"  # Arm 1
    curator = "curator"  # Arm 2
    gold = "gold"  # Arm 3


@dataclass(frozen=True)
class ArmResult:
    arm: Arm
    ex: float
    decoy_touch_rate: float
    governed_path_adherence: float
    n: int


def run_arms(db: str) -> dict[Arm, ArmResult]:
    """Run all three arms on a DB's test split and return per-arm results."""
    raise NotImplementedError("three-arm harness pending; needs server + gold + DB")
