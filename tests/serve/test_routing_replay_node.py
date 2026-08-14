"""``route`` honouring a replayed shortlist — the half of ``--replay-routing`` that is a node.

Here rather than beside the rest of the replay contract in
``tests/eval/test_routing_replay.py`` because the two-schema fixture corpus is a
``tests/serve`` conftest fixture, and pytest shares a conftest down a package but not across
siblings. The artifact reading and the drift statistic are asserted there; what the pin *does*
once it reaches the graph is asserted here.

If this file broke, ``--replay-routing`` would be accepted, printed in the run header, and
silently ineffective — an arm labelled pinned that routed live. That is worse than not having
the flag, because the label is what a later reader trusts.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from governed_bi.eval.replay import PINNED_SCHEMAS_KEY
from governed_bi.govern.policy import GovernancePolicy
from governed_bi.retrieve.index import UnifiedIndex
from governed_bi.serve.nodes.route_retrieve import route_node

# Same-directory import, the way ``test_chat_transport.py`` reaches
# ``turn_contract_fixtures``: ``tests/serve`` has no ``__init__.py``, so a relative import has
# no parent package and pytest's own path insertion is not in effect at collection time.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_pass_two_and_context import (  # noqa: E402
    SCHEMA_A,
    SCHEMA_B,
    _base_turn,
    _config,
    _live_facets,
)


def _case(index: UnifiedIndex, assets: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """``(state, config)`` for one routable question over the fixture corpus."""
    config = _config(
        thread_id="t-replay",
        policy=GovernancePolicy(guard_rules_enabled={}),
        index=index,
        assets_by_id=assets,
    )
    state = _base_turn(question="customer", route_top_n=1)
    return {**state, "facets": _live_facets(state, config), "candidate_depth": 50}, config


def test_route_honours_a_pinned_shortlist_over_its_own_ranking(
    two_schema_index: UnifiedIndex, two_schema_assets: dict[str, Any]
) -> None:
    """Asserted as a *change*, not as an outcome.

    The same question is routed twice: once free, once pinned to the schema the free run did
    **not** pick. Asserting only that the pinned run returned the pinned schema would pass just
    as well if ``route`` had chosen it anyway — the version of this test that proves nothing.
    """
    state, config = _case(two_schema_index, two_schema_assets)

    free = route_node(state, config)
    assert free.get("schemas") == [SCHEMA_A], f"fixture drifted; free routing gave {free.get('schemas')}"

    pinned = route_node({**state, PINNED_SCHEMAS_KEY: [SCHEMA_B]}, config)
    assert pinned.get("schemas") == [SCHEMA_B], (
        "route ignored the pin, so a run would report --replay-routing and route live"
    )
    assert pinned.get("retrieved"), (
        "pinning emptied the retrieval it feeds; the pin replaces the shortlist, not pass two"
    )


def test_a_pin_naming_an_unknown_schema_is_ignored_rather_than_obeyed(
    two_schema_index: UnifiedIndex, two_schema_assets: dict[str, Any]
) -> None:
    """An artifact from another corpus must not collapse the arm it is replayed into.

    Obeying such a pin licenses nothing, and the run reads as a routing collapse caused by the
    replay rather than by anything under test — a confound introduced by the tool whose whole
    purpose is to remove one. Falling back to live routing is the honest failure: the row still
    carries ``routing_pinned`` so the fraction stays recoverable.
    """
    state, config = _case(two_schema_index, two_schema_assets)

    routed = route_node({**state, PINNED_SCHEMAS_KEY: ["a_schema_from_another_corpus"]}, config)

    assert routed.get("schemas") == [SCHEMA_A]
    assert routed.get("path_kind") != "decline"


def test_a_partly_known_pin_keeps_only_the_schemas_this_corpus_has(
    two_schema_index: UnifiedIndex, two_schema_assets: dict[str, Any]
) -> None:
    """The mixed case, which the all-or-nothing tests above cannot reach.

    A corpus that gained or lost one schema between runs produces exactly this. Dropping the
    whole pin would discard a decision that is still valid for the schemas that remain.
    """
    state, config = _case(two_schema_index, two_schema_assets)

    routed = route_node({**state, PINNED_SCHEMAS_KEY: [SCHEMA_B, "gone_from_this_corpus"]}, config)

    assert routed.get("schemas") == [SCHEMA_B]


def test_the_ranking_still_reaches_retrieval_under_a_pin(
    two_schema_index: UnifiedIndex, two_schema_assets: dict[str, Any]
) -> None:
    """Pass two scores against the pass-one ranking, so the pin must not replace it.

    Applying the pin before the ranking was computed would hand pass two a different input and
    make the pinned arm a different system — the opposite of what pinning is for.
    """
    state, config = _case(two_schema_index, two_schema_assets)

    pinned = route_node({**state, PINNED_SCHEMAS_KEY: [SCHEMA_B]}, config)
    retrieved = pinned.get("retrieved") or {}

    assert retrieved.get("schema_ranking"), "pass one's ranking did not survive the pin"
    ranked = {str(name) for name, *_ in retrieved["schema_ranking"]}
    assert SCHEMA_A in ranked, (
        "the pin narrowed the ranking as well as the shortlist; only the shortlist is pinned"
    )
