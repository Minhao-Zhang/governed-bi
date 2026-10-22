"""Both embedder adapters on one contract.

Model identity and width are part of the interface. A width check only separates
models whose widths differ, so the cache key still has to carry the model.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

#: Per test, never module-level. A module-level empty spec was the incident in
#: ``tests/serve/test_turn_contract.py``.
UNWRITTEN = pytest.mark.xfail(strict=True, reason="contract specified, body not yet written")


#: The OpenAI adapter skips without a key, names the variable, and never prints the value.
ADAPTERS = ("deterministic", "openai")


@pytest.mark.parametrize("adapter", ADAPTERS)
def test_embed_returns_one_vector_per_input_in_input_order(adapter: str) -> None:
    """A reordered batch is silent: zipping with ``strict=True`` catches only a short result.
    """
    from embedders import NARROW_DIMENSIONS, SMALL_BATCH, make_embedder
    from governed_bi.retrieve.semantic import cosine

    embedder = make_embedder(adapter, dimensions=NARROW_DIMENSIONS, batch_size=SMALL_BATCH)

    # Seven inputs, batch of four, so the boundary is crossed. Unrelated topics: the
    # check is an argmax, and two near-synonyms would make it a toss.
    texts = [
        "customers registered in the sales schema",
        "brewing capacity per factory site",
        "geographic coordinates of each location",
        "invoice totals by billing month",
        "employee shift rota for the packaging line",
        "supplier contracts and their renewal dates",
        "returned bottles counted at the depot",
    ]
    assert len(texts) > embedder.batch_size, "fixture no longer crosses a batch boundary"

    batched = embedder.embed(texts)
    assert len(batched) == len(texts)

    individual = [embedder.embed([text])[0] for text in texts]

    for position, own in enumerate(individual):
        similarities = [cosine(own, candidate) for candidate in batched]
        nearest = max(range(len(similarities)), key=similarities.__getitem__)
        assert nearest == position, (
            f"input {position} ({texts[position]!r}) embedded alone is nearest to "
            f"batched vector {nearest} ({texts[nearest]!r}): the batch came back "
            "reordered, so every asset would silently take another asset's vector"
        )
        # Margin over the runner-up, not proximity to 1.0. The provider is not
        # bit-identical across calls, and a tolerance that passes a reorder is useless.
        runner_up = max(s for i, s in enumerate(similarities) if i != position)
        assert similarities[position] - runner_up > 0.05, (
            f"input {position} is nearest to its own batched vector by only "
            f"{similarities[position] - runner_up:.4f}: too close to call, so this body "
            "cannot distinguish a correct batch from a reordered one"
        )


@pytest.mark.parametrize("adapter", ADAPTERS)
def test_every_vector_has_exactly_the_declared_width(adapter: str) -> None:
    """A shortened ``dimensions`` the provider ignored must fail, not only the native width.
    """
    from embedders import NARROW_DIMENSIONS, make_embedder

    probe = ["a customers table", "a brewing capacity metric"]

    native = make_embedder(adapter)
    vectors = native.embed(probe)
    assert [len(v) for v in vectors] == [native.dimensions, native.dimensions]

    shortened = make_embedder(adapter, dimensions=NARROW_DIMENSIONS)
    assert shortened.dimensions == NARROW_DIMENSIONS
    assert shortened.dimensions != native.dimensions, (
        "the shortened width equals the native one, so this body would pass for an "
        "adapter that ignored `dimensions` entirely"
    )
    short_vectors = shortened.embed(probe)
    assert [len(v) for v in short_vectors] == [NARROW_DIMENSIONS, NARROW_DIMENSIONS]


@pytest.mark.parametrize("adapter", ADAPTERS)
def test_an_empty_or_whitespace_input_refuses(adapter: str) -> None:
    """The adapter refuses a blank inside a batch. A zero vector would look orthogonal.
    """
    from embedders import NARROW_DIMENSIONS, make_embedder

    embedder = make_embedder(adapter, dimensions=NARROW_DIMENSIONS)

    for blank in ("", "   ", "\t\n "):
        with pytest.raises(ValueError):
            embedder.embed([blank])
        # A batch is where a blank rides in unnoticed.
        with pytest.raises(ValueError):
            embedder.embed(["a customers table", blank])
        with pytest.raises(ValueError):
            embedder.embed([blank, "a customers table"])

    # Real text still embeds, so an adapter that raises on every input fails here.
    good = embedder.embed(["a customers table"])
    assert len(good) == 1
    assert len(good[0]) == NARROW_DIMENSIONS
    assert any(component != 0.0 for component in good[0])


def test_the_shared_half_inherits_the_port_instead_of_restating_it() -> None:
    from governed_bi.model.embedder import BaseEmbedder
    from governed_bi.ports import Embedder

    # MRO, not issubclass: `model` and `dimensions` are properties, so the port is
    # a data protocol and `issubclass` raises.
    assert Embedder in BaseEmbedder.__mro__

    restated = {"model", "dimensions"} & set(vars(BaseEmbedder))
    assert not restated, (
        f"{sorted(restated)} are declared on both BaseEmbedder and ports.Embedder. Two "
        "declarations of one seam is two places a rule has to land, and the port is the one "
        "retrieve/ and serve/ type against."
    )
    # `embed` is overridden with the shared checks, not a second signature.
    assert "embed" in vars(BaseEmbedder)


def test_an_adapter_that_forgets_the_model_identity_cannot_be_constructed() -> None:
    """Without ``@abstractmethod``, a ``Protocol`` ``...`` body is a concrete method returning ``None``.
    """
    from typing import Sequence

    from governed_bi.model.embedder import BaseEmbedder
    from governed_bi.ports import Embedder, Vector

    class ForgotItsIdentity(BaseEmbedder):
        @property
        def dimensions(self) -> int:
            return 4

        def _embed_batch(self, texts: Sequence[str]) -> list[Vector]:
            return [[0.5, 0.5, 0.5, 0.5] for _ in texts]

    with pytest.raises(TypeError, match="model"):
        ForgotItsIdentity()

    class NotOurBaseClass:
        model = "deterministic:duck"
        dimensions = 4

        def embed(self, texts: Sequence[str]) -> list[Vector]:
            return [[0.5, 0.5, 0.5, 0.5] for _ in texts]

    assert isinstance(NotOurBaseClass(), Embedder)


def test_two_models_of_the_same_width_do_not_share_a_cache_entry() -> None:
    """Same width is not the same model: cosine only rejects a width mismatch.
    """
    from typing import Any, Sequence

    from governed_bi.model import DeterministicEmbedder
    from governed_bi.ports import Vector
    from governed_bi.registry.assets import AssetType
    from governed_bi.retrieve.index import IndexEntry, UnifiedIndex, build_index
    from governed_bi.retrieve.semantic import cache_key, cosine
    from governed_bi.retrieve.vector_cache import VectorCache

    def stored(index: UnifiedIndex, asset_id: str) -> list[float]:
        """Vector stored for one asset. The table is keyed by asset id, not summary text."""
        assert index.vectors is not None
        arrow = index.vectors.to_arrow()
        return arrow.column("vector")[arrow.column("key").to_pylist().index(asset_id)].as_py()

    class Counting(DeterministicEmbedder):
        """Counts texts. A hit and a miss build the same index, so reuse is invisible on it."""

        def __init__(self, **kwargs: Any) -> None:
            super().__init__(**kwargs)
            self.embedded: list[str] = []

        def _embed_batch(self, texts: Sequence[str]) -> list[Vector]:
            self.embedded.extend(texts)
            return super()._embed_batch(texts)

    width = 64
    summary = "customers registered in the sales schema"

    def entries() -> list[IndexEntry]:
        return [
            IndexEntry(
                id="t1",
                summary=summary,
                asset_type=AssetType.table,
                schema_tag="sales",
            )
        ]

    large = Counting(dimensions=width, salt="stands-in-for-3-large")
    small = Counting(dimensions=width, salt="stands-in-for-3-small")
    assert large.dimensions == small.dimensions == width
    assert large.model != small.model

    # One cache for every embedder. Separate dicts per model would hide a shared key.
    cache = VectorCache()

    first = build_index(entries(), embedder=large, vector_cache=cache)
    assert large.embedded == [summary]
    assert cache_key(summary, model=large.model, dimensions=width) in cache.keys()

    second = build_index(entries(), embedder=small, vector_cache=cache)
    assert small.embedded == [summary], (
        "the second model was handed the first model's vector: the cache key is not "
        "carrying the model, which is v1's defect exactly"
    )
    assert len(cache) == 2
    assert cache_key(summary, model=small.model, dimensions=width) in cache.keys()

    assert cosine(stored(first, "t1"), stored(second, "t1")) < 0.99
    assert len(stored(first, "t1")) == len(stored(second, "t1"))

    large.embedded.clear()
    third = build_index(entries(), embedder=large, vector_cache=cache)
    assert large.embedded == [], "the same embedder missed its own cache entry"
    assert stored(third, "t1") == stored(first, "t1")
    assert len(cache) == 2

    # Two widths of one model name are two vector spaces.
    narrow = Counting(dimensions=32, salt="stands-in-for-3-large")
    assert narrow.model == large.model
    build_index(entries(), embedder=narrow, vector_cache=cache)
    assert narrow.embedded == [summary]
    assert len(cache) == 3

    # No embedder means no key, so the call is refused instead of guessed.
    with pytest.raises(ValueError):
        build_index(entries(), vector_cache=cache)


@pytest.mark.parametrize("adapter", ADAPTERS)
def test_the_adapter_reports_the_model_the_provider_used(adapter: str) -> None:
    """``model`` is what the provider served. Recording the request hides an alias or a snapshot.
    """
    from embedders import NARROW_DIMENSIONS, make_embedder

    embedder = make_embedder(adapter, dimensions=NARROW_DIMENSIONS)

    if adapter == "deterministic":
        from governed_bi.model import DeterministicEmbedder

        # No model argument, so the identity cannot be an echo of a request.
        with pytest.raises(TypeError):
            DeterministicEmbedder(model="text-embedding-3-large")  # type: ignore[call-arg]

        assert embedder.model.startswith("deterministic:hashed-bow-sha256:")

        assert embedder.model == DeterministicEmbedder(dimensions=NARROW_DIMENSIONS).model
        assert embedder.model == DeterministicEmbedder(dimensions=NARROW_DIMENSIONS * 4).model

        assert (
            embedder.model
            != DeterministicEmbedder(dimensions=NARROW_DIMENSIONS, salt="v2").model
        )

        # A digest of the adapter's own output. A hand-kept version string would not move,
        # and cached vectors from an older algorithm would look current.
        assert embedder.model == "deterministic:hashed-bow-sha256:daef773fc3d2"
        return

    from openai import OpenAI

    # Requested name is immediate; the served name is not. That gap is the only
    # difference visible while the provider still serves the asked name.
    assert embedder.requested_model == "text-embedding-3-large"
    assert embedder.served_model is None

    observed = OpenAI().embeddings.create(
        model="text-embedding-3-large",
        input=["a customers table"],
        dimensions=NARROW_DIMENSIONS,
    )

    # Observed from a response this adapter never saw.
    assert embedder.model == f"openai:{observed.model}"
    assert embedder.served_model == observed.model
    assert embedder.served_model is not None


def test_the_embedding_settings_reach_settings_resolved() -> None:
    """Two runs that differ only in embedder must not share one setting set.
    """
    from governed_bi.model import DeterministicEmbedder, embedding_settings
    from governed_bi.registry.settings import setting_names

    embedder = DeterministicEmbedder(dimensions=64)
    settings = embedding_settings(embedder)

    assert set(settings) == {"embedding_model", "embedding_dimensions"}
    assert settings["embedding_model"] == embedder.model
    assert settings["embedding_dimensions"] == 64
    assert settings["embedding_model"].startswith("deterministic:")

    # A name the settings list does not declare is outside the config hash.
    assert set(settings) <= setting_names()
    assert set(settings) <= setting_names()
    assert set(settings) <= setting_names()

    base = {"route_top_n": 3, "candidate_depth": 50}
    resolved = {**base, **settings}
    assert resolved["embedding_model"] == embedder.model
    assert resolved["embedding_dimensions"] == 64

    other_model = {
        **base,
        **embedding_settings(DeterministicEmbedder(dimensions=64, salt="other")),
    }
    other_width = {**base, **embedding_settings(DeterministicEmbedder(dimensions=128))}
    assert resolved != other_model
    assert resolved != other_width

    # The same configuration still resolves the same way.
    assert resolved == {**base, **embedding_settings(DeterministicEmbedder(dimensions=64))}


def test_the_deterministic_adapter_is_stable_across_processes() -> None:
    """``hash(str)`` is salted per process, so the check is a subprocess with another ``PYTHONHASHSEED``.
    """
    import json
    import os
    import subprocess
    import sys
    import textwrap

    from governed_bi.model import DeterministicEmbedder

    texts = ["customers registered in 2019", "brewing capacity per site"]
    local = DeterministicEmbedder(dimensions=64)
    expected = [list(v) for v in local.embed(texts)]

    program = textwrap.dedent(
        """
        import json
        from governed_bi.model import DeterministicEmbedder

        texts = ["customers registered in 2019", "brewing capacity per site"]
        embedder = DeterministicEmbedder(dimensions=64)
        print(json.dumps({
            "model": embedder.model,
            "vectors": [list(v) for v in embedder.embed(texts)],
            "builtin_hash": hash(texts[0]),
        }))
        """
    )

    root = Path(__file__).resolve().parent.parent.parent
    runs = []
    for seed in ("0", "12345"):
        completed = subprocess.run(
            [sys.executable, "-c", program],
            env={**os.environ, "PYTHONHASHSEED": seed},
            cwd=str(root),
            capture_output=True,
            text=True,
            check=True,
        )
        runs.append(json.loads(completed.stdout))

    # The two processes really did get different string-hash salts. Without that,
    # agreement below would not separate a digest seed from a ``hash()`` seed.
    assert runs[0]["builtin_hash"] != runs[1]["builtin_hash"], (
        "PYTHONHASHSEED did not change the interpreter's string hashing, so this "
        "test cannot distinguish a digest-seeded adapter from a hash()-seeded one"
    )

    for run in runs:
        assert run["vectors"] == expected
        assert run["model"] == local.model

    assert expected[0] != expected[1]


def test_the_semantic_channel_scores_a_channel_that_declares_it() -> None:
    """The few-shot summary is not the question verbatim, so a dict lookup cannot pass.
    """
    from governed_bi.model import DeterministicEmbedder
    from governed_bi.registry.assets import AssetType
    from governed_bi.registry.channels import (
        CHANNEL_TARGETS,
        DECLARED_CHANNELS,
        Channel,
        ChannelState,
        expected_channel_state,
    )
    from governed_bi.registry.stages import Stage
    from governed_bi.retrieve.index import IndexEntry, build_index
    from governed_bi.retrieve.semantic import semantic_search
    from governed_bi.retrieve.vector_cache import VectorCache

    assert DECLARED_CHANNELS[Stage.channel_example] == frozenset({Channel.semantic})
    assert Channel.lexical not in DECLARED_CHANNELS[Stage.channel_example]
    assert expected_channel_state(Stage.channel_example, Channel.semantic) is ChannelState.ran
    assert CHANNEL_TARGETS[Stage.channel_example] == frozenset({AssetType.few_shot})

    question = "how many customers registered in 2019"

    wanted = IndexEntry(
        id="fs_customers_2019",
        summary="count of customers who registered during 2019",
        asset_type=AssetType.few_shot,
        schema_tag="sales",
    )
    distractor = IndexEntry(
        id="fs_capacity",
        summary="average brewing capacity across factory sites",
        asset_type=AssetType.few_shot,
        schema_tag="sales",
    )
    table = IndexEntry(
        id="sales.kunden",
        summary="kunden (customer records)",
        asset_type=AssetType.table,
        schema_tag="sales",
    )

    embedder = DeterministicEmbedder(dimensions=256)
    index = build_index(
        [wanted, distractor, table], embedder=embedder, vector_cache=VectorCache()
    )
    query_vector = embedder.embed([question])[0]

    few_shots = {wanted.id, distractor.id}
    hits, state = semantic_search(index, query_vector, candidates=few_shots)

    assert state is ChannelState.ran
    assert [asset_id for asset_id, _ in hits] == [wanted.id, distractor.id]
    assert 0.0 < hits[0][1] < 1.0, (
        "the top score is 0 or an exact match: either the channel found nothing, or the "
        "fixture is matching verbatim and would pass without an embedder at all"
    )
    assert hits[0][1] > hits[1][1]

    # Without an embedder the state is `not_configured`, distinct from an empty hit list.
    bare = build_index([wanted, distractor, table])
    bare_hits, bare_state = semantic_search(bare, query_vector, candidates=few_shots)
    assert bare_state is ChannelState.not_configured
    assert bare_hits == []
