"""A corpus cannot widen the licence set without limit by adding reference edges.

``resolve_node`` takes the reference closure of the retrieval hits and puts **every table in
it into ``licensed``** — which is what the TABLES layer accepts — and every asset in it into
``pulled_in``, which is what the prompt renders. The closure was unbounded, so both sets grew
with the corpus's own shape: one ``references`` edge widens what SQL the guard will approve,
with no access-policy change and nothing to review it against.

**Measured before a bound was chosen**, on ``../BIRD-corpus`` (13,304 assets, 57 schemas), so
the number is not taste:

* from one asset: median 15 additions, p90 42, p99 118, max 181
* from a realistic seed (``route_top_n`` schemas, ``table`` budget 8): median 52-69,
  p99 142-218, **max 279**
* from every table of the three largest schemas (144 tables): **1,480 additions**, 11% of the
  corpus

So the pathological shape is reachable on the shipped corpus and the only thing in front of
it is the ``table`` budget of 8 — whose placement ``govern/bounds.py`` already records as
costing a wrong refusal. 400 sits ~45% above anything a real seed produced and well under
1,480: the bound exists so the growth has a ceiling, not to change what this corpus does.

**The bound is in the node, not in ``resolve``.** ``resolve`` is total by contract —
``connect.py``'s docstring draws that line and both acceptance contracts assert it — and the
first draft of this change broke both. The asymmetry is principled: ``connect`` searches, so
an early stop means something; this is a fixpoint walk where it buys nothing.
"""

from __future__ import annotations

from typing import Any

import pytest

from governed_bi.register.knobs import knob_default
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
        "licensed": [hub],
        **extra,
    }


def _run(structure: CorpusStructure, state: dict[str, Any]) -> dict[str, Any]:
    return resolve_node(state, {"configurable": {"structure": structure}})


def test_a_closure_within_the_bound_licenses_everything_it_reaches() -> None:
    """The behaviour the bound must not change, asserted first.

    A cap that refused ordinary retrieval would trade an unbounded licence set for an outage,
    and 27% of single-asset closures on the shipped corpus add more than 25.
    """
    update = _run(_fan_out(50), _state())

    assert update.get("path_kind") != "decline", update
    assert len(update["licensed"]) == 51, "the closure did not reach the tables it points at"
    assert len(update["retrieved"]["pulled_in"]) == 50


def test_a_closure_past_the_bound_declines_rather_than_truncating() -> None:
    """The headline. Over the bound is a refusal with a name, not a shorter licence set.

    Truncating is the tempting fix and is the worse one: a silently cut closure drops a table
    from ``licensed``, and the TABLES layer then refuses the statement
    ``r_table_not_licensed`` — a retrieval-budget outcome recorded as a governance verdict.
    """
    cap = knob_default("max_resolve_additions")
    update = _run(_fan_out(cap + 1), _state())

    assert update["path_kind"] == "decline"
    assert update["terminal_reason"] == "over_resolve_bounds"
    assert "licensed" not in update, (
        "a declining turn still widened the licence set, which is the thing the bound exists "
        "to stop"
    )
    assert update["crossings"] == []


def test_the_bound_is_a_knob_and_not_a_constant() -> None:
    """Read through ``int_knob``, so a deployment can move it and an arm records it.

    ``route_retrieve.py`` states the rule: "No local defaults … a ``state.get(name,
    <constant>)`` here is a knob nothing can set."
    """
    structure = _fan_out(10)

    assert _run(structure, _state()).get("path_kind") != "decline"
    tightened = _run(structure, _state(knobs_resolved={"max_resolve_additions": 5}))
    assert tightened["path_kind"] == "decline"
    assert tightened["terminal_reason"] == "over_resolve_bounds"


def test_the_terminal_reason_is_in_the_declared_vocabulary() -> None:
    """A reason the register cannot place is a refusal no report can attribute.

    ``REFUSED_BY_TO_STAGE`` is the inventory ``classify_outcome`` reads; a value missing from
    it is a turn that ends somewhere the funnel cannot name.
    """
    from governed_bi.register.stages import REFUSED_BY_TO_STAGE, Stage

    assert REFUSED_BY_TO_STAGE["over_resolve_bounds"] is Stage.connect, (
        "resolve has no stage of its own; its decline belongs where the closure would have "
        "been used"
    )


@pytest.mark.parametrize("n", [0, 1, 399, 400])
def test_the_boundary_is_inclusive(n: int) -> None:
    """``> max_added`` and not ``>=``: the bound is the largest closure still allowed.

    Spelled out because an off-by-one here is a turn that declines for being exactly the size
    somebody measured and chose.
    """
    assert _run(_fan_out(n), _state()).get("path_kind") != "decline", f"{n} additions declined"
