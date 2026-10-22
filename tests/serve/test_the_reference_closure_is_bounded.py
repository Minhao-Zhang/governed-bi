"""A reference closure past ``max_resolve_additions`` declines instead of widening the licence set.

The bound lives in ``resolve_node``. ``resolve`` stays total: cutting the closure would drop
a table and the TABLES layer would record that as a governance refusal.
"""

from __future__ import annotations

from typing import Any

import pytest

from governed_bi.registry.settings import setting_default
from governed_bi.retrieve.structure import CorpusStructure
from governed_bi.serve.nodes.route_retrieve import resolve_node


def _fan_out(n: int) -> CorpusStructure:
    """One hit table pointing at ``n`` other tables. The shape ``few_shot`` and ``join`` make."""
    hub, spokes = "s.hub", [f"s.t{i}" for i in range(n)]
    every = [hub, *spokes]
    return CorpusStructure(
        join_edges=frozenset(),
        references={hub: frozenset(spokes), **{s: frozenset() for s in spokes}},
        asset_types={a: "table" for a in every},
        table_schemas={a: "s" for a in every},
        schema_tags={a: "s" for a in every},
        joins_by_edge={},
    )


def _state(hub: str = "s.hub", **extra: Any) -> dict[str, Any]:
    return {
        "retrieved": {
            "selected": {hub: {"score": 1.0}},
            "by_type": {"table": [hub]},
            "pulled_in": {},
            "attributions": {},
        },
        "retrieved_tables": [hub],
        **extra,
    }


def _run(structure: CorpusStructure, state: dict[str, Any]) -> dict[str, Any]:
    return resolve_node(state, {"configurable": {"structure": structure}})


def test_a_closure_within_the_bound_licenses_everything_it_reaches() -> None:
    """A closure of 50 still licenses every table it reaches."""
    update = _run(_fan_out(50), _state())

    assert update.get("path_kind") != "decline", update
    assert len(update["retrieved_tables"]) == 51, "the closure did not reach the tables it points at"
    assert len(update["retrieved"]["pulled_in"]) == 50


def test_a_closure_past_the_bound_declines_rather_than_truncating() -> None:
    """Over the bound is a named refusal; truncating would record a governance miss for a budget stop."""
    cap = setting_default("max_resolve_additions")
    update = _run(_fan_out(cap + 1), _state())

    assert update["path_kind"] == "decline"
    assert update["terminal_reason"] == "over_resolve_bounds"
    assert "retrieved_tables" not in update, (
        "a declining turn still widened the licence set, which is the thing the bound exists "
        "to stop"
    )
    assert update["crossings"] == []


def test_the_bound_is_a_setting_and_not_a_constant() -> None:
    """Read through ``int_setting``: a local default is a setting nothing can set."""
    structure = _fan_out(10)

    assert _run(structure, _state()).get("path_kind") != "decline"
    tightened = _run(structure, _state(settings_resolved={"max_resolve_additions": 5}))
    assert tightened["path_kind"] == "decline"
    assert tightened["terminal_reason"] == "over_resolve_bounds"


def test_the_terminal_reason_is_in_the_declared_vocabulary() -> None:
    """Resolve has no stage of its own; the decline is attributed where the closure is used."""
    from governed_bi.registry.stages import REFUSED_BY_TO_STAGE, Stage

    assert REFUSED_BY_TO_STAGE["over_resolve_bounds"] is Stage.connect, (
        "resolve has no stage of its own; its decline belongs where the closure would have "
        "been used"
    )


@pytest.mark.parametrize("n", [0, 1, 399, 400])
def test_the_boundary_is_inclusive(n: int) -> None:
    """Exactly ``max_added`` still passes; the bound is the largest closure allowed."""
    assert _run(_fan_out(n), _state()).get("path_kind") != "decline", f"{n} additions declined"
