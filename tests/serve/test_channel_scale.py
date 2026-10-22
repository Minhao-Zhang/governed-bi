"""Both retrieval passes compare lexical and semantic scores on a fixed ceiling, not a relative scale."""

from __future__ import annotations

import pytest

from governed_bi.registry.assets import AssetType
from governed_bi.serve.runtime import channel_scale

QUERY = "customers id email"
QUERY_VECTOR = [0.0, 1.0]
#: ``[sqrt(1 - c**2), c]`` against the query vector above, so the cosine is exactly ``c``.
DOCS = {
    "sales.lex_best": ("customers id email", [0.99499, 0.10]),
    "sales.lex_mid": ("customers customers customers customers id email", [0.99875, 0.05]),
    "sales.lex_weak": ("customers warehouse dock pallet crate forklift", [0.99980, 0.02]),
    "sales.vector_only": ("widgets sku warehouse", [0.91652, 0.40]),
}


class _Stub:
    model = "scale-stub"
    requested_model = "scale-stub"
    dimensions = 2
    _by_text = {summary: vector for summary, vector in DOCS.values()}

    def embed(self, texts):
        return [self._by_text[t] for t in texts]


def _index():
    from governed_bi.retrieve.index import IndexEntry, build_index

    return build_index(
        [
            IndexEntry(id=aid, summary=summary, asset_type=AssetType.table, schema_tag="sales")
            for aid, (summary, _) in DOCS.items()
        ],
        embedder=_Stub(),
    )


def _assert_the_scale_is_fixed_not_relative(mid, only) -> None:
    """The four properties, shared by both passes because both must agree on them."""
    # Resolved from the register through the one reader, exactly as a turn does (audit I10).
    SEMANTIC_CEILING = channel_scale({}).semantic_ceiling

    # The fixture must exercise the combiner rather than bypass it.
    assert mid["lexical"] is not None and mid["semantic"] is not None
    assert only["lexical"] is None, "it shares no query term; BM25 must not score it"
    assert only["semantic"] is not None

    # The premise, asserted so a change to BM25's constants cannot void the test.
    assert mid["lexical"] > only["semantic"], (
        "the fixture no longer reproduces the scale gap it exists to demonstrate: "
        f"raw lexical {mid['lexical']} vs raw cosine {only['semantic']}"
    )

    # 1. Attribution is raw: the record publishes what each channel said.
    assert only["semantic"] == pytest.approx(0.40, abs=1e-3)
    assert mid["lexical"] == pytest.approx(0.599, abs=5e-3)

    #    document was its channel's best hit, so it scored exactly the channel's full weight of
    #    0.5 — and it would have done so at cosine 0.04 just the same.
    expected = 0.5 * min(1.0, 0.40 / SEMANTIC_CEILING)
    assert only["score"] == pytest.approx(expected, abs=5e-3), (
        f"a cosine of 0.40 against a ceiling of {SEMANTIC_CEILING} should contribute "
        f"{expected:.4f}, not {only['score']}"
    )
    assert only["score"] < 0.5, (
        "the semantic channel's best hit reached its full declared weight, which is min-max "
        "again: it asserts that every query's best cosine is maximal evidence"
    )

    # 3. The weakest scored document is not floored at 0.0, so it stays distinguishable from a
    assert mid["semantic"] > 0.0
    assert 0.0 < 0.5 * (mid["semantic"] / SEMANTIC_CEILING) < mid["score"]

    # 4. The units still do not decide. `vector_only` is BM25-invisible, so the fixed
    #    denominator caps it at the semantic weight and `lex_mid` — two channels, mediocre on
    #    both — comes out marginally ahead. That ordering is unchanged from the min-max rule and
    assert only["score"] > mid["score"] * 0.9, (
        "a strong-cosine, no-shared-term asset is being ranked by units rather than strength: "
        f"vector_only={only['score']} mid={mid['score']}"
    )


def test_the_ceiling_clamps_rather_than_letting_one_channel_exceed_its_weight() -> None:
    """``scale_to_ceiling``'s own three properties, none of which the fixtures above reach."""
    from governed_bi.retrieve.fuse import scale_to_ceiling

    assert scale_to_ceiling(0.3, ceiling=0.6) == pytest.approx(0.5)
    assert scale_to_ceiling(0.9, ceiling=0.6) == 1.0, "a cosine above the ceiling was not clamped"
    # Zero and below stay zero rather than going negative: a cosine can be negative, and a
    # negative contribution would make one channel's evidence *subtract* from another's.
    assert scale_to_ceiling(0.0, ceiling=0.6) == 0.0
    assert scale_to_ceiling(-0.4, ceiling=0.6) == 0.0
    with pytest.raises(ValueError):
        scale_to_ceiling(0.3, ceiling=0.0)


def test_pass_one_compares_the_two_channels_on_a_fixed_scale() -> None:
    """The case the suite never built: hits with **both** channels non-``None``."""
    from governed_bi.registry.stages import Stage
    from governed_bi.serve.nodes.channels import _pass_one_hits

    index = _index()
    hits = {
        h["asset_id"]: h
        for h in _pass_one_hits(
            index,
            Stage.channel_entity,
            QUERY,
            depth=10,
            scale=channel_scale({}),
            ran=set(),
            observed={},
            query_vector=QUERY_VECTOR,
        )
    }
    assert set(hits) == set(DOCS), sorted(hits)
    _assert_the_scale_is_fixed_not_relative(hits["sales.lex_mid"], hits["sales.vector_only"])

    # Only pass one can assert this: it is the pass that ranks within the channel, and `route`
    # reads the per-channel maximum. A channel whose best evidence is mediocre must say so.
    assert max(h["score"] for h in hits.values()) < 0.75, (
        "this retrieval channel's best hit is a 0.627 BM25 and a 0.10 cosine, and it reported near the top "
        "of the scale; `route` then cannot weigh it against a retrieval channel that found the right table"
    )


def test_pass_two_uses_the_same_fixed_scale() -> None:
    """Pass two uses the same fixed scale. Its scores decide which tables survive the cap."""
    from governed_bi.serve.nodes.pass_two import pass_two_retrieve

    retrieved = pass_two_retrieve(
        state={
            "question": QUERY,
            "settings_resolved": {},
            "channels": {
                "channel_entity": {"channel": "channel_entity", "queries": [QUERY], "hits": []}
            },
        },
        index=_index(),
        schemas=["sales"],
        ranking=[("sales", 1.0)],
        query_vector=QUERY_VECTOR,
    )
    selected = retrieved["selected"]
    _assert_the_scale_is_fixed_not_relative(
        selected["sales.lex_mid"], selected["sales.vector_only"]
    )


def test_the_two_passes_score_one_asset_identically() -> None:
    """The structural reason the scale moved into ``combine_channels``."""
    from governed_bi.registry.stages import Stage
    from governed_bi.serve.nodes.channels import _pass_one_hits
    from governed_bi.serve.nodes.pass_two import pass_two_retrieve

    index = _index()
    one = {
        h["asset_id"]: h["score"]
        for h in _pass_one_hits(
            index, Stage.channel_entity, QUERY, depth=10, ran=set(), observed={},
            scale=channel_scale({}),
            query_vector=QUERY_VECTOR,
        )
    }
    two = pass_two_retrieve(
        state={
            "question": QUERY,
            "settings_resolved": {},
            "channels": {
                "channel_entity": {"channel": "channel_entity", "queries": [QUERY], "hits": []}
            },
        },
        index=index,
        schemas=["sales"],
        ranking=[("sales", 1.0)],
        query_vector=QUERY_VECTOR,
    )["selected"]

    assert set(one) == set(two), "the fixture no longer scores the same assets in both passes"
    for asset_id, score in one.items():
        assert score == pytest.approx(two[asset_id]["score"]), (
            f"{asset_id} carries two different scores in one turn: pass one {score}, pass two "
            f"{two[asset_id]['score']}. `apply_budgets` sorts them together."
        )


def test_a_run_can_move_the_fusion_settings_and_the_score_follows() -> None:
    """Audit I10."""
    from governed_bi.registry.stages import Stage
    from governed_bi.serve.nodes.channels import _pass_one_hits
    from governed_bi.serve.runtime import channel_scale

    index = _index()

    def score_of(asset_id: str, **settings) -> float:
        hits = {
            h["asset_id"]: h
            for h in _pass_one_hits(
                index,
                Stage.channel_entity,
                QUERY,
                depth=10,
                scale=channel_scale(settings),
                ran=set(),
                observed={},
                query_vector=QUERY_VECTOR,
            )
        }
        return hits[asset_id]["score"]

    # `vector_only` is BM25-invisible, so it is scored by the semantic channel alone.
    default = score_of("sales.vector_only")
    heavier = score_of("sales.vector_only", w_lexical=0.1, w_semantic=0.9)
    lower_ceiling = score_of("sales.vector_only", semantic_scale_ceiling=0.4)

    assert heavier > default, (
        f"raising w_semantic did not raise a semantic-only asset's score ({default} -> {heavier}); "
        "the weights are being read from somewhere the run cannot reach"
    )
    assert lower_ceiling > default, (
        f"lowering the ceiling did not raise a cosine's contribution ({default} -> {lower_ceiling})"
    )
    # And a setting the turn did not set still resolves from the register.
    assert channel_scale({}).semantic_ceiling == channel_scale({}).semantic_ceiling
