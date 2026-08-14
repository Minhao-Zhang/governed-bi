"""A checkpointed ``Measured`` must come back as a ``Measured``, under strict msgpack.

Scheduled rather than historical: on langgraph 1.2.10 an unregistered type still
round-trips (with a warning), so nothing is broken today. The warning names the release
that breaks it — ``LANGGRAPH_STRICT_MSGPACK=true`` is the announced future default, and
under it the serde does not raise, it hands back a plain ``dict``. That failure is silent
by construction: a dict of an absence carries the same four fields, compares equal to
nothing that is checked, and is *truthy*, so the one distinction the type exists to hold
disappears on a framework upgrade with no test and no traceback to point at.

So the assertions here are **type identity and ``.is_measured``**, never equality. A dict
that compares equal to a ``Measured`` is precisely the defect.

The path exercised is the production one: LangGraph derives the strict allowlist from the
graph's state schema at ``compile()`` and applies it to the saver via the public
``BaseCheckpointSaver.with_allowlist``. Both halves are asserted, because either one
failing alone reproduces the bug — and the half that did fail was the derivation
(``Measured[int]`` is a ``_GenericAlias``, not a dataclass; see
``Measured.__class_getitem__``).
"""

from __future__ import annotations

from typing import Any

from langgraph._internal import _serde
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from governed_bi.register.quantity import Measured, State
from governed_bi.serve.state import ServeState, UsageRecord

#: What the serde must be told about. ``State`` and ``Relation`` are companions, not
#: extras: they are the field types, so a registered ``Measured`` whose enums are blocked
#: still comes back wrong.
QUANTITY_TYPES: frozenset[tuple[str, str]] = frozenset(
    ("governed_bi.register.quantity", name) for name in ("Measured", "State", "Relation")
)


def _strict_saver() -> Any:
    """The saver LangGraph builds when ``LANGGRAPH_STRICT_MSGPACK=true``.

    ``allowed_msgpack_modules=None`` is what ``JsonPlusSerializer.__init__`` selects under
    that env var, and the schema-derived allowlist is what ``StateGraph.compile`` applies
    on top. Constructed here rather than by setting the variable because the flag is read
    at *import* time, so a test that set it would depend on module import order.
    """
    saver = InMemorySaver(serde=JsonPlusSerializer(allowed_msgpack_modules=None))
    return saver.with_allowlist(_serde.build_serde_allowlist(schemas=[ServeState]))


def test_the_state_schema_registers_the_quantity_types() -> None:
    """``ServeState`` must be enough on its own — no construction site is asked to help."""
    derived = _serde.build_serde_allowlist(schemas=[ServeState])
    assert QUANTITY_TYPES <= derived, f"missing: {sorted(QUANTITY_TYPES - derived)}"


def test_an_unmeasured_token_count_survives_a_strict_round_trip() -> None:
    """The case that matters: absence, which a dict renders indistinguishable from zero."""
    serde = _strict_saver().serde
    row: UsageRecord = {
        "turn_index": 0,
        "input_tokens": Measured.unmeasured("no usage block"),
    }
    back = serde.loads_typed(serde.dumps_typed({"usage": [row]}))
    value = back["usage"][0]["input_tokens"]

    assert type(value) is Measured, f"came back as {type(value).__name__}: {value!r}"
    assert value.state is State.not_measured
    assert value.is_measured is False
    assert value.why == "no usage block"


def test_a_measured_token_count_survives_a_strict_round_trip() -> None:
    """The other half of the distinction, so a pass cannot mean "everything is absent"."""
    serde = _strict_saver().serde
    back = serde.loads_typed(serde.dumps_typed({"input_tokens": Measured.of(1234)}))
    value = back["input_tokens"]

    assert type(value) is Measured
    assert value.is_measured is True
    assert value.value == 1234
