"""The render budget shapes the prompt. It does not decide what the turn may query.

ADR 0006 §8 and ADR 0005 §3.2 both assert the separation — *"budgets shape what is rendered,
and licensing what is reachable"* — and for as long as ``route_node`` read

    licensed = retrieved["by_type"]["table"]

it was false in the one direction that costs a wrong verdict. ``by_type`` is assembled out of
the hits ``apply_budgets(...)`` **kept** and the table budget is 8
(``register/assets.py``), so a gold table ranked ninth was never licensed, and
``govern/check.py::_tables`` refused the statement ``r_table_not_licensed`` — a
retrieval-budget outcome recorded as a governance verdict. Neither widening node restored it:
``resolve`` adds the reference closure, ``connect`` adds Steiner points, and a budget-cut
table that is neither has no path back.

Four promises here, and the third is the one that keeps the fix from costing something else:

1. a table the cap cut is still licensed;
2. it then refuses on the **grant** rather than on the licence, which is the distinction
   ``_tables``'s own docstring is written for — "retrieval missed" and "you may not" stop
   being the same reason code;
3. the wider licence does **not** reach ``connect``'s Steiner search. Terminals are what the
   prompt renders. A table nobody is shown must not be able to decline the turn on
   ``missing_join_path``, or the coupling is back wearing a second reason code;
4. **both seeds still exist**, selected by ``licensed_seed_pre_budget`` — ``True`` as shipped,
   ``False`` reproducing the licence every arm in ``register/arms.toml`` was measured under.
   Pinned in both directions, because a regression either way is a silent one: an arm that
   claims the old seed and gets the new one measures nothing, and it is
   ``[arm.v4_live]``'s whole job to be that control.

The knob is read in ``_licensable_tables``, which ``route``, ``resolve`` and ``connect`` all
go through, so :func:`test_the_seed_survives_resolve_and_connect` drives the three in sequence:
a knob honoured at the seed and undone one node later is the failure that would still let the
control arm publish the treatment's licence.

Model-free: the two-schema fixture holds 15 tables in ``sales_a`` against a cap of 8, so the
budget bites on a lexical-only run with no embedder and no model call anywhere.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any

import pytest

from governed_bi.corpus.analyst import for_analyst
from governed_bi.govern.check import check
from governed_bi.govern.policy import GovernancePolicy
from governed_bi.ports import Grant, Reach
from governed_bi.register.assets import ASSET_REGISTER, AssetType
from governed_bi.register.facets import Channel, expected_channel_state
from governed_bi.register.knobs import knob_default
from governed_bi.register.stages import Stage
from governed_bi.retrieve.index import UnifiedIndex
from governed_bi.serve.nodes.facets import facet_entity_node, facet_schema_node
from governed_bi.serve.nodes.route_retrieve import (
    connect_node,
    resolve_node,
    route_node,
)

SCHEMA_A = "sales_a"

#: The knob's name, spelled once. ``arms.toml``'s ``[arm.licensed_pre_budget]`` declares it as
#: its treatment and ``register/arm_profiles.py`` refuses a treatment that is not a declared
#: comparability knob, so a rename cannot reach this file without going through that loader.
SEED_KNOB = "licensed_seed_pre_budget"


def _state(index: UnifiedIndex, policy: GovernancePolicy, *, pre_budget: bool) -> tuple[
    dict[str, Any], dict[str, Any]
]:
    """The routed turn's ``(state, config)``, with the seed knob resolved to ``pre_budget``.

    Written into ``knobs_resolved`` rather than onto the state root, because that is where the
    eval driver's ``--post-budget-licence`` puts it and a test that took the shorter path would
    pin a precedence step no run uses.
    """
    state: dict[str, Any] = {
        "question": "customer",
        "thread_id": "t-prebudget-licence",
        "turn_index": 1,
        "knobs_resolved": {
            "route_top_n": 1,
            "candidate_depth": 50,
            SEED_KNOB: pre_budget,
        },
        "messages": [],
        "usage": [],
    }
    config = {"configurable": {"thread_id": state["thread_id"], "policy": policy, "index": index}}
    facets = {
        "facet_entity": asyncio.run(facet_entity_node(state, config))["facets"]["facet_entity"],
        "facet_schema": asyncio.run(facet_schema_node(state, config))["facets"]["facet_schema"],
    }
    for stage in (Stage.facet_term, Stage.facet_metric, Stage.facet_example):
        facets[stage.value] = {
            "facet": stage.value,
            "queries": [state["question"]],
            "hits": [],
            "channels": {ch.value: expected_channel_state(stage, ch).value for ch in Channel},
        }
    return {**state, "facets": facets}, config


def _routed(
    index: UnifiedIndex, policy: GovernancePolicy, *, pre_budget: bool = True
) -> dict[str, Any]:
    """``route_node``'s output for a question that scores more ``sales_a`` tables than the cap.

    ``route_top_n=1`` so the shortlist is ``sales_a`` alone and the cap is the only thing
    deciding which of its tables are rendered. ``pre_budget`` defaults to the shipped value of
    the knob, which :func:`test_the_shipped_default_is_the_pre_budget_seed` pins.
    """
    state, config = _state(index, policy, pre_budget=pre_budget)
    return route_node(state, config)


def _rendered_and_licensed(out: dict[str, Any]) -> tuple[list[str], set[str]]:
    rendered = [str(t) for t in (out["retrieved"].get("by_type") or {}).get("table") or ()]
    return rendered, {str(t) for t in out.get("licensed") or ()}


def test_a_table_the_render_budget_cut_is_still_licensed(
    two_schema_index: UnifiedIndex, guard_off_policy: GovernancePolicy
) -> None:
    """The seed is the pre-budget table set, so the cap subtracts from the prompt only.

    Asserted as an equality between three numbers — rendered, cut, licensed — rather than as
    ``licensed >= rendered``, which the old code satisfied too.
    """
    out = _routed(two_schema_index, guard_off_policy)
    rendered, licensed = _rendered_and_licensed(out)
    cap = ASSET_REGISTER[AssetType.table].budget
    dropped = int((out["retrieved"].get("budget_dropped") or {}).get("table") or 0)

    assert len(rendered) == cap, (
        f"the fixture must exercise the cap or this test cannot fail: {len(rendered)} tables "
        f"rendered against a budget of {cap}"
    )
    assert dropped > 0, "the cap discarded nothing, so there is no budget-cut table to license"
    assert set(rendered) <= licensed, "a rendered table must be queryable"
    assert len(licensed) == len(rendered) + dropped, (
        "the licence is still the post-budget rendering. A table the cap cut is reachable — "
        "budgets shape what is rendered (ADR 0006 §8) — so every one of the "
        f"{dropped} discarded tables belongs here: licensed {sorted(licensed)}"
    )
    assert sorted(out["retrieved"]["table_candidates"]) == sorted(licensed), (
        "`licensed` is seeded from `table_candidates`, the set recorded before `apply_budgets` "
        "ran; anything else is a second answer to what retrieval reached"
    )


def test_the_cut_table_answers_the_grant_instead_of_reporting_a_retrieval_miss(
    two_schema_index: UnifiedIndex,
    two_schema_assets: dict[str, Any],
    guard_off_policy: GovernancePolicy,
) -> None:
    """What the widening buys, in ``check()``'s reason codes.

    The same statement over the same table, under a grant that authorizes every table except
    that one. Post-budget licence: ``r_table_not_licensed`` — the analyst is told retrieval
    never found it. Pre-budget licence: ``r_table_not_authorized`` — the analyst is told the
    truth, and the histogram stops attributing a permission decision to the retriever.

    The licence-before-grant **ordering** is untouched, and that is what keeps this from being
    an existence oracle: the grant is still asked only about a table this turn licensed, so no
    reason code names a table the question's own terms did not reach.
    """
    out = _routed(two_schema_index, guard_off_policy)
    rendered, licensed = _rendered_and_licensed(out)
    cut = sorted(licensed - set(rendered))
    assert cut, "no table was cut by the budget; the fixture stopped exercising the cap"
    target = cut[0]

    corpus = for_analyst(list(two_schema_assets.values()))
    denied = replace(
        guard_off_policy,
        access_grant=Grant(
            reach=Reach.listed,
            tables=frozenset(t for t in licensed if t != target),
        ),
    )
    sql = f"SELECT 1 FROM {target}"

    post_budget = check(sql, licensed=frozenset(rendered), corpus=corpus, policy=denied)
    pre_budget = check(sql, licensed=frozenset(licensed), corpus=corpus, policy=denied)

    assert post_budget["reason_code"] == "r_table_not_licensed", (
        "the fixture must reproduce the old refusal or the comparison below proves nothing"
    )
    assert pre_budget["reason_code"] == "r_table_not_authorized", (
        f"{target} was cut by the render budget and denied by the grant. Under the pre-budget "
        "licence the refusal must name the permission decision, not the retriever: got "
        f"{pre_budget['reason_code']!r}"
    )


def test_a_table_nobody_is_shown_cannot_decline_the_turn(
    two_schema_index: UnifiedIndex, guard_off_policy: GovernancePolicy
) -> None:
    """``connect``'s terminals are the rendered tables, not the licence.

    Feeding it the wider set would let a budget-cut table with no join edge fail the Steiner
    search and take the whole turn down on ``missing_join_path`` — the budget deciding a
    governance outcome again, one node further along. So the same ``connect_node`` call is made
    twice, differing only in ``table_candidates``: the retrieval delta and the path must be
    byte-identical, and only ``licensed`` may move.
    """
    out = _routed(two_schema_index, guard_off_policy)
    rendered, _licensed = _rendered_and_licensed(out)
    structure_config = {"configurable": {"assets_by_id": {}}}

    base_retrieved = {
        "by_type": {"table": list(rendered)},
        "table_candidates": list(rendered),
        "selected": {},
        "attributions": {},
        "pulled_in": {},
    }
    narrow = connect_node(
        {"retrieved": base_retrieved, "schemas": [SCHEMA_A]}, structure_config
    )
    wide = connect_node(
        {
            "retrieved": {
                **base_retrieved,
                "table_candidates": [*rendered, f"{SCHEMA_A}.a_table_the_cap_cut"],
            },
            "schemas": [SCHEMA_A],
        },
        structure_config,
    )

    assert narrow.get("path_kind") is None and wide.get("path_kind") is None, (
        "neither call may decline: these tables are one component per table and a component "
        "of one connects trivially"
    )
    assert narrow["retrieved"] == wide["retrieved"], (
        "a table outside the prompt changed the retrieval delta, so the licence has reached "
        "the Steiner search"
    )
    assert narrow["crossings"] == wide["crossings"], (
        "a table outside the prompt was charged a schema crossing"
    )
    assert set(wide["licensed"]) - set(narrow["licensed"]) == {
        f"{SCHEMA_A}.a_table_the_cap_cut"
    }, "the pre-budget table is what the licence — and only the licence — gains"


@pytest.mark.parametrize("key", ["by_type", "table_candidates"])
def test_a_hand_built_retrieval_result_still_licenses_what_it_renders(key: str) -> None:
    """The fallback in ``_licensable_tables``, stated as a promise rather than left implicit.

    ``retrieve_hooks`` callers and fixtures assemble ``retrieved`` themselves and do not know
    about ``table_candidates``. Absent the key the node reads ``by_type["table"]``, which is
    the old behaviour: it under-licenses in exactly the way this file is about, and it is here
    so such a caller licenses what it renders rather than nothing at all.
    """
    from governed_bi.serve.nodes.route_retrieve import _licensable_tables

    tables = [f"{SCHEMA_A}.orders", f"{SCHEMA_A}.customers"]
    retrieved: dict[str, Any] = {"by_type": {"table": []}, "selected": {}}
    retrieved[key] = {"table": tables} if key == "by_type" else tables
    # Empty state: the knob resolves through `knobs_resolved` to the register's shipped `True`,
    # which is the seed a caller who set nothing gets.
    assert sorted(_licensable_tables({}, retrieved)) == sorted(tables)


@pytest.mark.parametrize(
    ("pre_budget", "expected"),
    [(True, ["candidate_only", "rendered"]), (False, ["rendered"])],
)
def test_the_knob_selects_the_seed_and_both_seeds_still_exist(
    pre_budget: bool, expected: list[str]
) -> None:
    """``licensed_seed_pre_budget`` is what makes ``[arm.v4_live]`` a control and not a replicate.

    The other tests in this file pin the shipped ``True``. This one pins **both**, because the
    experiment needs the old seed to still be reachable: ``register/arms.toml``'s ``v4_live``
    runs at ``False`` and ``licensed_pre_budget`` names this knob as its whole treatment, so a
    change that quietly made ``False`` behave like ``True`` would leave the two arms resolving
    every comparability knob identically — a pair ``eval/report.py::knobs_comparable`` would
    certify while the delta it reports is code rather than treatment.

    Asserted on the *difference* rather than on the knob being read: a reader that consulted the
    knob and returned the same set either way passes a "is it read?" test and fails this one.
    """
    from governed_bi.serve.nodes.route_retrieve import _licensable_tables

    retrieved: dict[str, Any] = {
        # `rendered` survived `apply_budgets`; `candidate_only` was scored and cut.
        "by_type": {"table": ["rendered"]},
        "table_candidates": ["rendered", "candidate_only"],
        "selected": {},
    }
    state = {"knobs_resolved": {SEED_KNOB: pre_budget}}
    assert sorted(_licensable_tables(state, retrieved)) == expected
