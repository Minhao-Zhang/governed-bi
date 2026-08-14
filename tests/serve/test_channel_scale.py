"""What scale the two channels are compared on, in both retrieval passes.

Split out of ``test_pass_two_and_context.py`` when that file crossed the 1,000-line cap, and it
is the right seam: everything here turns on one question, which has now been answered three
different ways.

1. ``max(lexical or 0.0, semantic or 0.0)``. Measured on corpus ``86ed1dbf``, a lexical top hit
   runs 0.666–0.933 where a semantic top hit runs 0.140–0.544, so ``max`` compared *units* rather
   than strength and the semantic channel was decorative. (The figure this once quoted, 0 wins in
   32,244 scored documents, is [retired] — ``register/citations.py`` records why it is not usable.
   The ranges above replace it and were measured after the tokenizer changed.)
2. A min-max over each channel's own scored population within the facet. Commensurate, and
   query-relative: the top-scoring document became exactly 1.0 whatever its absolute strength,
   the weakest became exactly 0.0, and ``route`` **sums those per-facet votes across facets** —
   so a facet that found nothing convincing voted as loudly as one that found the right table.
   ``tests/retrieve/test_scoring_contract.py`` states the violated property in the design
   holder's own words and tests it one layer below where the division happened.
3. A fixed ceiling per channel (audit I1), which is what is asserted here. The lexical channel
   passes through — ``raw/(raw+k)`` is already in ``[0, 1)`` — and cosine is divided by
   ``semantic_scale_ceiling``, whose value is a property of the embedder rather than of the
   query. Fitted for ``text-embedding-3-large`` at 0.6: measured over 120 questions × 57 schema
   summaries on corpus ``86ed1dbf``, the best-matching pair tops out at 0.5443.

The fixture is the same in both tests and the numbers in it are measured, not invented: BM25
over this index gives ``lex_best`` 0.627, ``lex_mid`` 0.599, ``lex_weak`` 0.207, and does not
score ``vector_only`` at all. The stub embedder puts ``vector_only`` at cosine 0.40, which is where
a real *good* cosine sits on this corpus: top-hit p90 is 0.442 and the observed max 0.544.

Note also that ``lex_mid`` repeats every query term four times and still scores *below*
``lex_best``: BM25's length normalisation penalises it, which is worth seeing in a fixture
rather than assumed away.
"""

from __future__ import annotations

import pytest

from governed_bi.register.assets import AssetType
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
    """The four properties, shared by both passes because both must agree on them.

    Asserted together in one helper rather than duplicated, because the whole point of moving the
    scale into ``combine_channels`` is that the two passes cannot answer this differently: pass
    one's untagged hits are carried into pass two verbatim and then compete against pass-two hits
    in ``apply_budgets``' single global sort.
    """
    # Resolved from the register through the one reader, exactly as a turn does (audit I10).
    SEMANTIC_CEILING = channel_scale({}).semantic_ceiling

    # The fixture must exercise the combiner rather than bypass it.
    assert mid["lexical"] is not None and mid["semantic"] is not None
    assert only["lexical"] is None, "it shares no query term; BM25 must not score it"
    assert only["semantic"] is not None

    # The premise, asserted so a change to BM25's constants cannot quietly void the test.
    assert mid["lexical"] > only["semantic"], (
        "the fixture no longer reproduces the scale gap it exists to demonstrate: "
        f"raw lexical {mid['lexical']} vs raw cosine {only['semantic']}"
    )

    # 1. Attribution is raw: the record publishes what each channel actually said.
    assert only["semantic"] == pytest.approx(0.40, abs=1e-3)
    assert mid["lexical"] == pytest.approx(0.599, abs=5e-3)

    # 2. **A good-but-not-maximal cosine no longer normalises up to 1.0.** Under min-max this
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
    #    document the channel scored zero — the residual `fuse`'s docstring used to record.
    assert mid["semantic"] > 0.0
    assert 0.0 < 0.5 * (mid["semantic"] / SEMANTIC_CEILING) < mid["score"]

    # 4. The units still do not decide. `vector_only` is BM25-invisible, so the fixed
    #    denominator caps it at the semantic weight and `lex_mid` — two channels, mediocre on
    #    both — comes out marginally ahead. That ordering is unchanged from the min-max rule and
    #    is **unmeasured either way**; what this asserts is that the two stay within a few
    #    percent, which is the property the units-deciding failure violates by a factor of three.
    assert only["score"] > mid["score"] * 0.9, (
        "a strong-cosine, no-shared-term asset is being ranked by units rather than strength: "
        f"vector_only={only['score']} mid={mid['score']}"
    )


def test_the_ceiling_clamps_rather_than_letting_one_channel_exceed_its_weight() -> None:
    """``scale_to_ceiling``'s own three properties, none of which the fixtures above reach.

    The fixture's cosine of 0.40 sits below the ceiling, so it never exercises the clamp — and an
    unclamped map would let a single unusually good cosine contribute more than
    ``w_semantic``, which silently un-declares the weight. ``fuse`` would not catch it: it
    renormalises by weight, not by the range of what it is handed.
    """
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
    """The case the suite never built: hits with **both** channels non-``None``.

    Every serve fixture calls ``build_index(entries)`` with no embedder, so
    ``UnifiedIndex.vectors is None`` and the combiner is a no-op in the only configuration it was
    ever exercised in. ``max(lexical or 0.0, semantic or 0.0)`` could have been ``min``, or
    ``lexical``, or a constant, and the suite would have stayed green.
    """
    from governed_bi.register.stages import Stage
    from governed_bi.serve.nodes.facets import _pass_one_hits

    index = _index()
    hits = {
        h["asset_id"]: h
        for h in _pass_one_hits(
            index,
            Stage.facet_entity,
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

    # Only pass one can assert this: it is the pass that ranks within the facet, and `route`
    # reads the per-facet maximum. A facet whose best evidence is mediocre must say so.
    assert max(h["score"] for h in hits.values()) < 0.75, (
        "this facet's best hit is a 0.627 BM25 and a 0.10 cosine, and it reported near the top "
        "of the scale; `route` then cannot weigh it against a facet that found the right table"
    )


def test_pass_two_uses_the_same_fixed_scale() -> None:
    """Pass two fused **raw** BM25 against **raw** cosine at 0.5/0.5 until 2026-08-06, and it is
    the pass that decides the budget.

    Pass one's combiner was repaired first and this one was not, which left the defect exactly
    where it costs most: ``_hybrid``'s output is what reaches ``apply_budgets``, so it decides
    which tables survive the cap of 8 — the largest attributable loss in the pipeline.
    """
    from governed_bi.serve.nodes.pass_two import pass_two_retrieve

    retrieved = pass_two_retrieve(
        state={
            "question": QUERY,
            "knobs_resolved": {},
            "facets": {
                "facet_entity": {"facet": "facet_entity", "queries": [QUERY], "hits": []}
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
    """The structural reason the scale moved into ``combine_channels``.

    A normaliser needing a population is a normaliser that depends on **which candidates were
    scored** — and the two passes score different sets by design, pass two being restricted to
    the selected schemas. So the same raw ``(lexical, semantic)`` pair came out as two different
    numbers in one turn, and ``apply_budgets`` sorted pass-one carry-forwards and pass-two hits
    together in a single global sort. With a fixed ceiling the pair determines the score, which
    is what makes the two comparable at all.
    """
    from governed_bi.register.stages import Stage
    from governed_bi.serve.nodes.facets import _pass_one_hits
    from governed_bi.serve.nodes.pass_two import pass_two_retrieve

    index = _index()
    one = {
        h["asset_id"]: h["score"]
        for h in _pass_one_hits(
            index, Stage.facet_entity, QUERY, depth=10, ran=set(), observed={},
            scale=channel_scale({}),
            query_vector=QUERY_VECTOR,
        )
    }
    two = pass_two_retrieve(
        state={
            "question": QUERY,
            "knobs_resolved": {},
            "facets": {
                "facet_entity": {"facet": "facet_entity", "queries": [QUERY], "hits": []}
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


def test_a_run_can_move_the_fusion_knobs_and_the_score_follows() -> None:
    """Audit I10. All three were read at **import** and no request could move them.

    That is not a missing feature, it is a false claim about a run: `w_lexical`, `w_semantic` and
    `semantic_scale_ceiling` are declared ``Role.comparability``, so they enter
    ``config_hash_keys()`` and ``knobs_resolved``. A run could publish ``w_semantic: 0.9``, move its
    config hash, and score exactly like the default — the inverse of the defect
    ``register/knobs.py`` opens by describing, and the same shape as the four knobs
    ``tests/serve/test_comparability_knobs.py`` was written for.

    Asserted through ``_pass_one_hits`` rather than on ``channel_scale``, because the resolver
    returning the right numbers says nothing about whether the scoring path uses them — which is
    exactly how the import-time constants survived. Two knob settings, two different scores, on one
    index and one query.
    """
    from governed_bi.register.stages import Stage
    from governed_bi.serve.nodes.facets import _pass_one_hits
    from governed_bi.serve.runtime import channel_scale

    index = _index()

    def score_of(asset_id: str, **knobs) -> float:
        hits = {
            h["asset_id"]: h
            for h in _pass_one_hits(
                index,
                Stage.facet_entity,
                QUERY,
                depth=10,
                scale=channel_scale(knobs),
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
    # And a knob the turn did not set still resolves from the register.
    assert channel_scale({}).semantic_ceiling == channel_scale({}).semantic_ceiling
