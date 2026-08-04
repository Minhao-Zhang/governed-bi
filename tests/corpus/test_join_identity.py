"""Join identity: on_digest / join_id (ADR 0005 §1.2, decision #36)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from contracts import needs  # noqa: E402

pytestmark = needs("D")


def test_operand_order_inside_a_predicate_does_not_change_the_digest() -> None:
    from governed_bi.corpus.identity import on_digest

    assert on_digest("a.x = b.y") == on_digest("b.y = a.x")


def test_conjunct_order_does_not_change_the_digest() -> None:
    from governed_bi.corpus.identity import on_digest

    left = on_digest("a.x = b.y AND c.z = d.w")
    right = on_digest("d.w = c.z AND b.y = a.x")
    assert left == right


def test_case_and_whitespace_are_irrelevant() -> None:
    from governed_bi.corpus.identity import on_digest

    assert on_digest("A.X=B.Y") == on_digest("a.x = b.y")


def test_different_on_clauses_between_the_same_tables_differ() -> None:
    from governed_bi.corpus.identity import join_id

    a = join_id("s", "left", "right", "left.id = right.left_id")
    b = join_id("s", "left", "right", "left.code = right.code")
    assert a != b
    assert a.startswith("join_s_left_right_")
    assert len(a.rsplit("_", 1)[-1]) == 8
