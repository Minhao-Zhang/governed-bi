"""The pending queue's ``source`` column has one spelling per value, in one place each.

**The defect this pins.** ``PENDING_SOURCE_INTERRUPT = "interrupt"`` was declared and exported in
``api/feedback_routes.py``, with prose saying "the client switches on it to decide which card to
draw" -- and the client does, but not through this. The real producer,
``api/thread_turns.py::_open_questions_of``, wrote a **hardcoded literal** ``"source":
"interrupt"``; the real consumer, ``ui/lib/schemas.ts``'s ``pendingClarificationSchema``, is a
``z.enum`` of literals. So a constant that claimed to be the shared spelling was shared with
nothing, and it survived the deletion of the module that minted it (``serve/raised.py``, ``4a0d11a``)
because nothing on either end referred to it.

Its old home was the wrong one either way. ``feedback_routes.py`` never emits ``interrupt``:
``_as_pending_row`` fills this column from ``obs.kind.value``, which is the *other* half of the
axis. The column is declared in ``thread_turns.PENDING_FIELDS`` and the interrupt half is the only
thing that produces this value, so the constant lives beside both.

**Neither direction of the import is a cycle**, checked both ways: ``thread_turns`` imports only
``register.quantity`` and ``feedback_routes`` imports only ``feedback/`` and ``register.assets``, so
either module could import the other and both are in the ``api`` layer
(``tools/check_imports.py::LAYERS``). The direction chosen is the cheaper one -- nothing has to
import at all, because the constant now sits in the module that writes it.

**The TypeScript side stays a literal enum** and cannot be fixed by importing: it is a different
language. What is checked instead is that the three declarations of the same three-value axis --
Python, ``docs/openapi.json`` and ``ui/lib/schemas.ts`` -- agree. The spec declared this property as
a bare ``type: string`` with the three values only in its *description*, and
``tests/api/test_the_spec_matches_the_server.py`` states in as many words that "descriptions are
unchecked". So the spec's claim about this column was prose. It is an ``enum`` now, which that test
validates against real payloads.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Any

from governed_bi.api.thread_turns import PENDING_SOURCE_INTERRUPT, _open_questions_of
from governed_bi.feedback.events import Kind

ROOT = Path(__file__).resolve().parents[2]
API_DIR = ROOT / "src" / "governed_bi" / "api"
SPEC = ROOT / "docs" / "openapi.json"
UI_SCHEMAS = ROOT / "ui" / "lib" / "schemas.ts"

#: The whole axis, assembled from its two producers rather than restated. The interrupt half is the
#: constant; the observation half is ``Kind``, which ``_as_pending_row`` writes as ``kind.value``.
AXIS: frozenset[str] = frozenset({PENDING_SOURCE_INTERRUPT, *(kind.value for kind in Kind)})


def test_the_producer_writes_the_constant_and_not_a_literal() -> None:
    """The runtime check: drive the real producer and read the column off its row.

    A value comparison against a constant in the same module is only worth something if the
    producer is the thing under test -- so this calls ``_open_questions_of`` on a thread shaped the
    way the platform hands it over, rather than re-asserting the constant against itself.
    """
    thread = {
        "thread_id": "t-1",
        "updated_at": "2026-08-24T10:00:00Z",
        "interrupts": {
            "task-1": [
                {
                    "id": "int-1",
                    "value": {
                        "kind": "clarification",
                        "clarification_id": "clar-a1b2c3d4e5f60718-0123456789ab",
                        "question": "Which listing?",
                        "why": "two tables carry a rating",
                        "basis": "data_definition",
                    },
                }
            ]
        },
    }

    (row,) = _open_questions_of(thread)
    assert row["source"] == PENDING_SOURCE_INTERRUPT, (
        f"the interrupt half of the pending queue emits {row['source']!r} and the constant says "
        f"{PENDING_SOURCE_INTERRUPT!r}. One of the two moved without the other, which is the whole "
        "reason the constant exists rather than a literal at the write site."
    )


def _literal_source_writes() -> list[str]:
    """``path:line`` for every dict literal under ``api/`` that writes a constant ``source``.

    Scoped to ``api/`` on purpose: ``serve/events.py`` writes ``{"source": "narrated"}`` and two
    siblings, which is the narration stage's own axis and has nothing to do with this column. A
    tree-wide rule would have to waive it, and a rule waived for correct code is one people learn
    to waive.
    """
    hits: list[str] = []
    for path in sorted(API_DIR.rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict):
                continue
            for key, value in zip(node.keys, node.values):
                if (
                    isinstance(key, ast.Constant)
                    and key.value == "source"
                    and isinstance(value, ast.Constant)
                ):
                    hits.append(f"{rel}:{key.lineno} writes source={value.value!r}")
    return hits


def test_no_handler_spells_the_column_by_hand() -> None:
    hits = _literal_source_writes()
    assert hits == [], (
        "these sites write the pending queue's `source` column as a bare string literal:\n  "
        + "\n  ".join(hits)
        + "\nA literal at the write site and a constant somewhere else are two spellings of one "
        "value that no test can see disagree. Write PENDING_SOURCE_INTERRUPT, or a `Kind` value."
    )


def _spec_property() -> dict[str, Any]:
    spec = json.loads(SPEC.read_text(encoding="utf-8"))
    row = spec["components"]["schemas"]["PendingClarificationRowResponse"]
    return row["properties"]["source"]


def test_the_spec_declares_the_axis_as_an_enum_and_not_as_prose() -> None:
    """A description is unchecked prose; an ``enum`` is validated against real payloads."""
    declared = _spec_property().get("enum")
    assert declared is not None, (
        "docs/openapi.json declares PendingClarificationRowResponse.source as a bare string. Its "
        "description names the three values, and test_the_spec_matches_the_server.py says "
        "descriptions are unchecked -- so the spec's only statement about this column is one "
        "nothing can falsify."
    )
    assert set(declared) == AXIS, (
        f"the spec's enum is {sorted(declared)} and the server's axis is {sorted(AXIS)}"
    )


def test_the_client_enum_is_the_same_three_values() -> None:
    """The one place a second language is allowed to restate the axis, checked rather than trusted.

    ``ui/`` cannot import from Python. So the literal enum is legitimate and the drift is what has
    to be caught: ``pendingClarificationSchema`` narrows ``source`` with ``z.enum``, and a value the
    server adds without touching that line arrives at the client as a Zod parse error on a queue
    row -- which renders as an empty queue, the failure ADR 0009 is about.
    """
    text = UI_SCHEMAS.read_text(encoding="utf-8")
    block = re.search(
        r"export const pendingClarificationSchema = z\.object\(\{(.*?)\n\}\);", text, re.S
    )
    assert block, "pendingClarificationSchema is not in ui/lib/schemas.ts in the shape parsed here"
    enum = re.search(r"^\s*source:\s*z\.enum\(\[(.*?)\]\)", block.group(1), re.M)
    assert enum, "pendingClarificationSchema.source is no longer a z.enum, so it narrows nothing"

    values = set(re.findall(r'"([^"]+)"', enum.group(1)))
    assert values == AXIS, (
        f"ui/lib/schemas.ts accepts {sorted(values)} for the pending row's source and the server "
        f"emits {sorted(AXIS)}. A row the client cannot parse is a row the steward never sees."
    )
