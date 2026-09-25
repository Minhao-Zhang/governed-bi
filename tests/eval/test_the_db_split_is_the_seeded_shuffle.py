"""``docs/data/db-split.csv`` is the seeded shuffle it claims to be, so nobody can hand-move a db.

Anything designed from failures uses the ``dev`` half; confirmation uses ``holdout``
(``docs/measurement.md``). A split edited after looking at results would defeat that.
"""

from __future__ import annotations

import csv
import random
from pathlib import Path

SPLIT = Path(__file__).resolve().parents[2] / "docs" / "data" / "db-split.csv"
SEED = 20260925


def test_the_split_is_reproducible_from_its_seed() -> None:
    with SPLIT.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    ids = [row["db_id"] for row in rows]
    assert len(ids) == len(set(ids)) == 57
    order = sorted(ids)
    random.Random(SEED).shuffle(order)
    assert ids == order
    assert [row["half"] for row in rows] == ["dev"] * 28 + ["holdout"] * 29
