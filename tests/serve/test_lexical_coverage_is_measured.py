"""``lexical_coverage`` is measured, not passed through from a test hook.

The field was null on every turn of every arm and the cause was a dead producer, not a missing
consumer. ``BM25.coverage`` is the declared derivation and had no production caller at all; its
wrapper had one call site, in ``route_retrieve``'s F1 no-index path, which passes ``index=None``
on purpose. Pass two -- the real indexed path -- returned ``state.get("lexical_coverage")``, a
hook nothing sets outside tests.

In its own file because ``test_pass_two_and_context.py`` is at the 1 000-line hard cap.
"""

from __future__ import annotations


def _one_table_index():
    """A real two-channel index over a single table, the smallest thing with a vocabulary."""
    from governed_bi.corpus.schema import TableAsset
    from governed_bi.model.deterministic_embedder import DeterministicEmbedder
    from governed_bi.retrieve.index import IndexEntry, build_index, schema_tag_for

    asset = TableAsset(
        id="sales.orders",
        schema="sales",
        physical_name="orders",
        summary="orders (sales order lines): customer, total",
        columns=(),
    )
    entries = [
        IndexEntry(
            id=asset.id,
            summary=asset.summary,
            asset_type=asset.asset_type,
            schema_tag=schema_tag_for(asset.asset_type, name=None, schema=asset.schema),
        )
    ]
    embedder = DeterministicEmbedder(dimensions=64)
    return build_index(entries, embedder=embedder), embedder


def test_pass_two_measures_lexical_coverage_from_the_live_index() -> None:
    """The declared measurement had a dead producer, and the row recorded null on every arm.

    ``BM25.coverage`` is the derivation and had **no production caller at all**. Its wrapper,
    ``lexical_coverage``, had one call site, in ``route_retrieve``'s F1 no-index path, which
    passes ``index=None`` on purpose — so nothing ever reached the real thing. Pass two, the
    indexed path, returned ``state.get("lexical_coverage")``: a test hook nothing sets outside
    tests.

    Two questions over the same index, one whose words the corpus has and one whose words it
    does not, so a constant cannot satisfy this.
    """
    from governed_bi.serve.nodes.pass_two import pass_two_retrieve

    index, embedder = _one_table_index()

    def _coverage(question: str) -> float | None:
        return pass_two_retrieve(
            state={
                "question": question,
                "knobs_resolved": {},
                "facets": {
                    "facet_entity": {
                        "facet": "facet_entity",
                        "queries": [question],
                        "hits": [],
                    }
                },
            },
            index=index,
            schemas=["sales"],
            ranking=[("sales", 1.0)],
            query_vector=embedder.embed([question])[0],
        )["lexical_coverage"]

    in_vocabulary = _coverage("sales order customer")
    out_of_vocabulary = _coverage("zzzqqq wwwvvv xxxyyy")

    assert isinstance(in_vocabulary, float), (
        f"pass two recorded no coverage at all: {in_vocabulary!r}"
    )
    assert isinstance(out_of_vocabulary, float)
    assert in_vocabulary > out_of_vocabulary, (
        "coverage does not move with the question, so it is not measuring the question: "
        f"{in_vocabulary} vs {out_of_vocabulary}"
    )
