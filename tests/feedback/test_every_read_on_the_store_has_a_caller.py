"""A method on ``FeedbackStore`` that nothing calls is a declaration with no consumer.

``counts_by`` was one: its docstring said it was "for the import report and the queue's own header"
and ``grep -rn "counts_by" src tools tests ui`` returned the definition and one quotation of its
signature in ``docs/return-path.md``. Neither the import report nor the queue header called it.
Documented as used, called by nothing -- the shape ``tools/check_declared_is_consumed.py`` exists to
catch, arriving in a layer that gate does not walk.

**The floor this check measures, stated rather than implied.** Evidence is the method name followed
by ``(`` anywhere in ``src/`` or ``tools/`` outside the store itself. That launders any name a
builtin shares: ``get`` is credited by every ``dict.get`` in the tree. It is still the check that
would have caught ``counts_by``, whose count was zero, and a stricter rule -- resolve the receiver's
type -- needs a type checker rather than a test.

Tests are deliberately **not** evidence. A method whose only caller is the test that covers it is
the defect with a passing coverage number on it.
"""

from __future__ import annotations

import re
from pathlib import Path

from governed_bi.feedback.store import FeedbackStore

ROOT = Path(__file__).resolve().parents[2]
STORE = ROOT / "src" / "governed_bi" / "feedback" / "store.py"


def _public_methods() -> list[str]:
    return sorted(
        name
        for name, value in vars(FeedbackStore).items()
        if not name.startswith("_") and callable(value)
    )


def _callers(name: str) -> list[str]:
    pattern = re.compile(rf"\.{re.escape(name)}\s*\(")
    hits: list[str] = []
    for directory in ("src", "tools"):
        for path in (ROOT / directory).rglob("*.py"):
            if path.resolve() == STORE.resolve():
                continue
            if pattern.search(path.read_text(encoding="utf-8")):
                hits.append(str(path.relative_to(ROOT)))
    return hits


def test_the_store_declares_no_read_that_nothing_reads() -> None:
    methods = _public_methods()
    assert methods, "the store's public surface is empty, so this test proves nothing"

    orphans = {name: _callers(name) for name in methods}
    dead = sorted(name for name, hits in orphans.items() if not hits)
    assert dead == [], (
        f"{dead} are declared on FeedbackStore and called from nowhere in src/ or tools/. A read "
        "with no caller is either a missing wire or a method to delete; a third state, where it "
        "exists and its docstring says it is used, is what this test refuses."
    )
