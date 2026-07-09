"""Tests for the LLM/embedding seams: protocols, offline doubles, config wiring.

These never touch the network: the OpenAI clients are checked only for correct
construction/config wiring, and everything else uses the deterministic offline
implementations.
"""

from __future__ import annotations

import math

import pytest

from governed_bi.config import ModelConfig
from governed_bi.llm import (
    ChatClient,
    Embedder,
    HashingEmbedder,
    OpenAiChatClient,
    OpenAiEmbedder,
    StaticChatClient,
    cosine,
)


# --------------------------------------------------------------------------- #
# Protocols
# --------------------------------------------------------------------------- #


def test_offline_doubles_satisfy_the_protocols():
    assert isinstance(StaticChatClient("x"), ChatClient)
    assert isinstance(HashingEmbedder(), Embedder)


# --------------------------------------------------------------------------- #
# StaticChatClient
# --------------------------------------------------------------------------- #


def test_static_chat_client_returns_queued_then_repeats_last():
    chat = StaticChatClient(["a", "b"])
    assert chat.complete("s", "u1") == "a"
    assert chat.complete("s", "u2") == "b"
    assert chat.complete("s", "u3") == "b"  # exhausted -> repeats last
    assert chat.calls == [("s", "u1"), ("s", "u2"), ("s", "u3")]


def test_static_chat_client_single_string():
    chat = StaticChatClient("only")
    assert chat.complete("s", "u") == "only"
    assert chat.complete("s", "u") == "only"


def test_static_chat_client_needs_a_response():
    with pytest.raises(ValueError):
        StaticChatClient([])


# --------------------------------------------------------------------------- #
# HashingEmbedder
# --------------------------------------------------------------------------- #


def test_hashing_embedder_is_deterministic_and_normalised():
    emb = HashingEmbedder(dimensions=64)
    v1 = emb.embed_one("total revenue by brand")
    v2 = emb.embed_one("total revenue by brand")
    assert v1 == v2
    assert len(v1) == 64
    assert math.isclose(math.sqrt(sum(x * x for x in v1)), 1.0, rel_tol=1e-9)


def test_hashing_embedder_similar_text_scores_higher():
    emb = HashingEmbedder(dimensions=512)
    q = emb.embed_one("total revenue by brand")
    near = emb.embed_one("revenue by brand total")  # same tokens, reordered
    far = emb.embed_one("customer phone number address")
    assert cosine(q, near) > cosine(q, far)
    assert math.isclose(cosine(q, near), 1.0, rel_tol=1e-9)  # same bag of words


def test_hashing_embedder_empty_text_is_zero_vector():
    emb = HashingEmbedder(dimensions=16)
    assert emb.embed_one("") == [0.0] * 16
    assert emb.embed(["a", ""])[1] == [0.0] * 16


def test_hashing_embedder_batch_matches_single():
    emb = HashingEmbedder()
    texts = ["one", "two", "three"]
    assert emb.embed(texts) == [emb.embed_one(t) for t in texts]


def test_hashing_embedder_rejects_bad_dims():
    with pytest.raises(ValueError):
        HashingEmbedder(dimensions=0)


# --------------------------------------------------------------------------- #
# cosine
# --------------------------------------------------------------------------- #


def test_cosine_edges():
    assert cosine([1.0, 0.0], [1.0, 0.0]) == 1.0
    assert cosine([1.0, 0.0], [0.0, 1.0]) == 0.0
    assert cosine([1.0], [1.0, 2.0]) == 0.0  # length mismatch
    assert cosine([0.0, 0.0], [1.0, 1.0]) == 0.0  # zero vector


# --------------------------------------------------------------------------- #
# OpenAI clients: config wiring only (no network)
# --------------------------------------------------------------------------- #


def test_openai_chat_client_from_config():
    chat = OpenAiChatClient.from_config(ModelConfig())
    assert chat.model == "gpt-5.5"
    assert chat.reasoning_effort == "low"
    assert chat.api_key_env == "OPENAI_API_KEY"
    assert isinstance(chat, ChatClient)


def test_openai_embedder_from_config():
    emb = OpenAiEmbedder.from_config(ModelConfig(embedding_dimensions=256))
    assert emb.model == "text-embedding-3-small"
    assert emb.dimensions == 256
    assert isinstance(emb, Embedder)


def test_openai_chat_client_without_key_raises(monkeypatch):
    """A call with no key fails loudly (never silently, never with a bad request)."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    chat = OpenAiChatClient.from_config(ModelConfig())
    with pytest.raises((RuntimeError, ModuleNotFoundError)):
        chat.complete("system", "user")
