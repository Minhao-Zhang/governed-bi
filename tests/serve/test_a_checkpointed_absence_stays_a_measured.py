"""A checkpointed ``Measured`` must come back as a ``Measured`` under strict msgpack.

Assertions check type identity and ``.is_measured``. A dict of an absence is truthy and
compares equal, so equality would hide the loss. The allowlist comes from the state schema
at ``compile()`` via ``with_allowlist``; ``Measured[int]`` is a ``_GenericAlias``, so the
derivation is the half that drops the type.
"""

from __future__ import annotations

from typing import Any

from langgraph._internal import _serde
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from governed_bi.registry.quantity import Measured, State
from governed_bi.serve.state import ServeState, UsageRecord

#: What the serde must be told about. ``State`` and ``Relation`` are companions, not
#: extras: they are the field types, so a registered ``Measured`` whose enums are blocked
#: still comes back wrong.
QUANTITY_TYPES: frozenset[tuple[str, str]] = frozenset(
    ("governed_bi.registry.quantity", name) for name in ("Measured", "State", "Relation")
)


def _strict_saver() -> Any:
    """Built without the env flag: ``LANGGRAPH_STRICT_MSGPACK`` is read at import time."""
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
