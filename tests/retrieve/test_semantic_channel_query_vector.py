"""The semantic channel must run for ``facet_schema``, which never rewrites its query.

``facet_schema`` is the only facet outside ``FACET_EXTRACTS``, so its query always equals the
raw question. ``vector_for_query`` skipped the embed in that case as a cache hit on the
pre-computed question vector — but the harness config carries none, so the facet that decides
which schema the analyst sees scored against ``None`` and reported ``semantic: failed`` on
every turn of a 1,351-question run.
"""

from __future__ import annotations

import asyncio

from governed_bi.corpus.schema import SchemaAsset, TableAsset
from governed_bi.govern.policy import GovernancePolicy
from governed_bi.model.deterministic_embedder import DeterministicEmbedder
from governed_bi.register.assets import AssetType
from governed_bi.register.facets import ChannelState
from governed_bi.serve.nodes.facets import facet_schema_node
from governed_bi.serve.runtime import vector_for_query
from governed_bi.serve.session import from_assets

QUESTION = "which warehouse logistics fleet schema holds the data"


def _session() -> object:
    assets = [
        SchemaAsset(id="sales_a", name="sales_a", summary="sales_a customer commerce orders"),
        SchemaAsset(id="ops_b", name="ops_b", summary="ops_b warehouse logistics fleet"),
        TableAsset(
            id="ops_b.shipments",
            schema="ops_b",
            physical_name="shipments",
            summary="shipments outbound logistics load",
        ),
    ]
    return from_assets(
        assets,
        connector=None,
        policy=GovernancePolicy(guard_rules_enabled={}),
        db_id="ops_b",
        corpus_content_hash_="test",
        agent_model=None,
        embedder=DeterministicEmbedder(dimensions=64),
    )


def test_schema_assets_carry_vectors_after_a_build() -> None:
    """Every ``INDEXED_TYPES`` member is embedded, ``AssetType.schema`` included.

    Asserted because the first diagnosis of the dead channel was that schema assets are
    never embedded; they are, and a missing *query* vector was the cause instead.
    """
    index = _session().index
    schema_ids = {
        eid for eid, e in index.entries.items() if e.asset_type is AssetType.schema
    }
    assert schema_ids, "fixture has no schema assets, so it cannot test this"
    assert index.vectors is not None
    assert schema_ids <= set(index.vectors.keys()), (
        "schema assets reached the index without vectors, so the schema facet's semantic "
        f"channel has nothing to score: missing {sorted(schema_ids - set(index.vectors.keys()))}"
    )


def test_a_query_that_was_not_rewritten_is_still_embedded_when_nothing_was_precomputed() -> None:
    """No rewrite is a reason to reuse ``fallback``, not a reason to score against ``None``."""
    embedder = DeterministicEmbedder(dimensions=64)
    same = vector_for_query(QUESTION, question=QUESTION, fallback=None, embedder=embedder)
    assert same is not None, (
        "an unrewritten query with no precomputed vector returned None, which silences the "
        "semantic channel for every facet that does not rewrite"
    )
    # The cache hit survives: given a fallback and no rewrite, no call is made.
    cached = vector_for_query(QUESTION, question=QUESTION, fallback=[1.0, 0.0], embedder=embedder)
    assert list(cached) == [1.0, 0.0]


def test_facet_schema_runs_the_semantic_channel_without_a_precomputed_query_vector() -> None:
    """``session.configurable()`` with no ``question=`` is the harness path (``eval/arms.py``)."""
    session = _session()
    config = session.configurable()
    assert "query_vector" not in config["configurable"], "fixture no longer models the defect"

    update = asyncio.run(facet_schema_node(dict(session.turn(QUESTION)), config))
    result = update["facets"]["facet_schema"]

    assert result["channels"]["semantic"] == ChannelState.ran.value, (
        f"the schema facet's semantic channel did not run: {result['channels']}"
    )
    scored = [h for h in result["hits"] if h.get("semantic") is not None]
    assert scored, (
        "the semantic channel reported `ran` but scored no schema asset, so the state is "
        f"true and the channel is still inert: {result['hits']}"
    )


def test_an_arm_with_no_embedder_reports_unconfigured_rather_than_failed() -> None:
    """``Anomaly.unconfigured`` was declared and unreachable: every declared channel that did
    not run read ``failed``, so a lexical-only arm and a dead embedder were the same word."""
    from governed_bi.measure.degradation import channel_anomalies, facets_degraded

    session = from_assets(
        [SchemaAsset(id="ops_b", name="ops_b", summary="ops_b warehouse logistics fleet")],
        connector=None,
        policy=GovernancePolicy(guard_rules_enabled={}),
        db_id="ops_b",
        corpus_content_hash_="test",
        agent_model=None,
        embedder=None,
    )
    update = asyncio.run(
        facet_schema_node(dict(session.turn(QUESTION)), session.configurable())
    )
    channels = update["facets"]["facet_schema"]["channels"]

    assert channels["semantic"] == ChannelState.not_configured.value, (
        f"no embedder is a configuration, not a failure: {channels}"
    )
    assert channels["lexical"] == ChannelState.ran.value
    facet_channels = {"facet_schema": channels}
    assert channel_anomalies(facet_channels) == {"facet_schema.semantic": "unconfigured"}
    # Still degradation: the facet declares two channels and ran one.
    assert facets_degraded(facet_channels) is True
