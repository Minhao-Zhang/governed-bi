"""``route`` honours a replayed shortlist.

The artifact and the drift statistic live in ``tests/eval``. A pin the node ignores would
label a run pinned while it still routes live.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from governed_bi.eval.replay import PINNED_SCHEMAS_KEY
from governed_bi.govern.policy import GovernancePolicy
from governed_bi.retrieve.index import UnifiedIndex
from governed_bi.serve.nodes.route_retrieve import route_node

# Same-directory import, the way ``test_turn_contract.py`` reaches
# ``turn_contract_fixtures``: ``tests/serve`` has no ``__init__.py``, so a relative import has
# no parent package and pytest's own path insertion is not in effect at collection time.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_pass_two_and_context import (  # noqa: E402
    SCHEMA_A,
    SCHEMA_B,
    _base_turn,
    _config,
    _live_channels,
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
    return {**state, "channels": _live_channels(state, config), "candidate_depth": 50}, config


def test_route_honours_a_pinned_shortlist_over_its_own_ranking(
    two_schema_index: UnifiedIndex, two_schema_assets: dict[str, Any]
) -> None:
    """Pinned to the schema the free run did not pick, so the pin has to change the result."""
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
    """An unknown schema falls back to live routing instead of licensing nothing."""
    state, config = _case(two_schema_index, two_schema_assets)

    routed = route_node({**state, PINNED_SCHEMAS_KEY: ["a_schema_from_another_corpus"]}, config)

    assert routed.get("schemas") == [SCHEMA_A]
    assert routed.get("path_kind") != "decline"


def test_a_partly_known_pin_keeps_only_the_schemas_this_corpus_has(
    two_schema_index: UnifiedIndex, two_schema_assets: dict[str, Any]
) -> None:
    """A pin that names one known schema and one unknown keeps only the known schema."""
    state, config = _case(two_schema_index, two_schema_assets)

    routed = route_node({**state, PINNED_SCHEMAS_KEY: [SCHEMA_B, "gone_from_this_corpus"]}, config)

    assert routed.get("schemas") == [SCHEMA_B]


def test_the_ranking_still_reaches_retrieval_under_a_pin(
    two_schema_index: UnifiedIndex, two_schema_assets: dict[str, Any]
) -> None:
    """The pin replaces the shortlist and leaves the pass-one ranking in place."""
    state, config = _case(two_schema_index, two_schema_assets)

    pinned = route_node({**state, PINNED_SCHEMAS_KEY: [SCHEMA_B]}, config)
    retrieved = pinned.get("retrieved") or {}

    assert retrieved.get("schema_ranking"), "pass one's ranking did not survive the pin"
    ranked = {str(name) for name, *_ in retrieved["schema_ranking"]}
    assert SCHEMA_A in ranked, (
        "the pin narrowed the ranking as well as the shortlist; only the shortlist is pinned"
    )
