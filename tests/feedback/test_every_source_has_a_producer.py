"""A :class:`Source` member nothing writes is a population that cannot exist.

``Source.agent`` was one. Four sites in ``src/`` and ``tools/`` construct an observation or a
patch, and they write ``reader``, ``operator``, ``operator`` and ``eval``; nothing anywhere wrote
``agent``. The consequence was not a null column but a **dead policy branch**:
``validate.py::_may_file_operator_only`` returned ``obs.source is Source.agent and obs.category is
Category.column_suspect``, whose docstring called it "the one agent-writable exception ADR 0005
declares", and which could not evaluate true for any row this tree can produce. A reader of that
function came away believing a permission was in force. ADR 0005's own retro on
``restamp_model_authored`` is the sentence for it: *an uncalled control is not one either*.

**Why this is a test and not a rule in ``tools/check_declared_is_consumed.py``.** That gate's K1
counts "the name occurs somewhere outside ``register/``" as evidence, which credits a *consumer* as
proof of a *producer* -- and ``Source.agent`` had exactly one occurrence, the dead branch above. So
the gate would have reported it consumed. The rule here is the sharper one the gate cannot afford
tree-wide: a member is produced when it appears inside the value of a ``source=`` or ``author=``
keyword argument, or under a ``"source"``/``"author"`` key of a dict literal. Comparisons
(``obs.source is Source.eval``) and set membership are reads and are deliberately not evidence.

**``Source`` and no other enum in this vocabulary, and the reason is not scope-laziness.** ``Source``
is the one member of ``feedback/events.py``'s vocabulary that **no caller may choose**: the filing
route decides it from ``GOVERNED_BI_FEEDBACK_ADMIN`` (``operator`` or ``reader``) and the importer
hardcodes ``eval``. Nothing parses it off a request body. ``Kind``, ``Category`` and
``DeclineReason`` arrive from the client through ``Kind(value)`` and friends, so their members are
produced *by the wire* and this rule reports every one of them as unproduced -- measured: 13 of 13
``Category`` members, 2 of 2 ``Kind``. ``ObservationState`` and ``PatchState`` are chosen by code
but reached through ``store.transition(to=...)``, whose argument is likewise parsed at the edge --
measured: 4 of 6 and 2 of 3 have no literal producer. A rule that has to be waived for correct code
teaches people to waive it, which is the argument ``check_declared_is_consumed.py`` makes about its
own rejected sharpening. The closure those enums get instead is
``events.py::_assert_the_vocabularies_are_closed`` and
``tests/feedback/test_every_stored_state_names_its_actor.py``.

**Tests are not evidence.** ``Source.agent``'s only construction site in the whole repository was
``tests/feedback/test_the_store_refuses_what_the_vocabulary_forbids.py``, which filed one and
asserted the store accepted it -- a dead branch with a passing coverage number on it. So this scan
walks ``src/`` and ``tools/`` and never ``tests/``.
"""

from __future__ import annotations

import ast
from pathlib import Path

from governed_bi.feedback.events import Source

ROOT = Path(__file__).resolve().parents[2]

#: The declaration side, which cannot be its own producer.
EVENTS = "src/governed_bi/feedback/events.py"

#: Constructor keywords and dict keys that mean "this row's population". Both shapes are in use:
#: ``Observation(source=...)`` and ``Patch(author=...)``.
PRODUCING_FIELDS: frozenset[str] = frozenset({"source", "author"})


def _producers() -> dict[str, set[str]]:
    """``Source.x`` -> the ``path:line`` sites that *write* it, never the ones that read it."""
    out: dict[str, set[str]] = {member.name: set() for member in Source}
    for directory in ("src", "tools"):
        for path in sorted((ROOT / directory).rglob("*.py")):
            rel = path.relative_to(ROOT).as_posix()
            if rel == EVENTS:
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
            except SyntaxError:  # pragma: no cover - a broken file fails elsewhere, loudly
                continue
            for node in ast.walk(tree):
                values: list[ast.expr] = []
                if isinstance(node, ast.Call):
                    values = [kw.value for kw in node.keywords if kw.arg in PRODUCING_FIELDS]
                elif isinstance(node, ast.Dict):
                    values = [
                        value
                        for key, value in zip(node.keys, node.values)
                        if isinstance(key, ast.Constant) and key.value in PRODUCING_FIELDS
                    ]
                # The whole subtree of the value, not just the value: the live filing route writes
                # `source=Source.operator if for_steward else Source.reader`, so the member sits
                # under an `IfExp` and a rule that only looked at the top node would report both
                # `reader` and `operator` unproduced.
                for value in values:
                    for sub in ast.walk(value):
                        if (
                            isinstance(sub, ast.Attribute)
                            and isinstance(sub.value, ast.Name)
                            and sub.value.id == "Source"
                            and sub.attr in out
                        ):
                            out[sub.attr].add(f"{rel}:{sub.lineno}")
    return out


def test_every_source_member_is_written_by_something_that_ships() -> None:
    producers = _producers()
    assert any(producers.values()), (
        "the scan found no producer for any Source member, so this test proves nothing -- the "
        "constructor keywords in PRODUCING_FIELDS have probably been renamed"
    )

    orphans = sorted(name for name, sites in producers.items() if not sites)
    assert orphans == [], (
        f"Source members {orphans} are declared and nothing in src/ or tools/ ever writes one. A "
        "population with no producer is not a narrower queue, it is a policy branch that cannot "
        "evaluate true -- which is how `_may_file_operator_only` came to document an "
        "agent-writable exception no row could reach. Wire a producer or delete the member; "
        "declaring it against an unbuilt pipeline is the trade ADR 0015 already refused for "
        "`rendered_asset_ids` ('it lands with its consumer and not before')."
    )
