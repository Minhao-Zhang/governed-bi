"""Tests for the LangChain-backed model clients.

No network: the chat model is LangChain's FakeListChatModel and the embedder is
DeterministicFakeEmbedding. Skipped entirely if the ``agents`` extra
(langchain-core) is not installed.
"""

from __future__ import annotations

import pytest

pytest.importorskip("langchain_core")

from langchain_core.embeddings import DeterministicFakeEmbedding  # noqa: E402
from langchain_core.language_models.fake_chat_models import FakeListChatModel  # noqa: E402

from governed_bi.config import ModelConfig  # noqa: E402
from governed_bi.llm import (  # noqa: E402
    ChatClient,
    Embedder,
    LangChainChatClient,
    LangChainEmbedder,
    cosine,
)


# --------------------------------------------------------------------------- #
# LangChainChatClient
# --------------------------------------------------------------------------- #


def test_chat_client_satisfies_protocol_and_returns_text():
    chat = LangChainChatClient(FakeListChatModel(responses=["SELECT 1"]))
    assert isinstance(chat, ChatClient)
    assert chat.complete("system prompt", "user prompt") == "SELECT 1"


def test_chat_client_maps_system_and_user_to_messages():
    # GenericFakeChatModel echoes; FakeListChatModel ignores input, so just verify
    # the call path works and strips whitespace.
    chat = LangChainChatClient(FakeListChatModel(responses=["  SELECT 2  "]))
    assert chat.complete("s", "u") == "SELECT 2"


def test_chat_client_from_config_builds_chat_openai(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    chat = LangChainChatClient.from_config(ModelConfig())
    # Lazy import worked and produced a ChatOpenAI bound to the configured model.
    assert chat.model.model_name == "gpt-5.6-sol"
    assert isinstance(chat, ChatClient)


# --------------------------------------------------------------------------- #
# LangChainEmbedder
# --------------------------------------------------------------------------- #


def test_embedder_satisfies_protocol_and_is_deterministic():
    emb = LangChainEmbedder(DeterministicFakeEmbedding(size=32))
    assert isinstance(emb, Embedder)
    v1 = emb.embed_one("total revenue")
    v2 = emb.embed_one("total revenue")
    assert v1 == v2
    assert len(v1) == 32


def test_embedder_batch_and_cosine():
    emb = LangChainEmbedder(DeterministicFakeEmbedding(size=16))
    vecs = emb.embed(["a", "b", "c"])
    assert len(vecs) == 3
    assert cosine(vecs[0], vecs[0]) == pytest.approx(1.0)


def test_embedder_empty_batch():
    emb = LangChainEmbedder(DeterministicFakeEmbedding(size=16))
    assert emb.embed([]) == []


def test_embedder_from_config_builds_openai_embeddings(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    emb = LangChainEmbedder.from_config(ModelConfig(embedding_dimensions=256))
    assert emb.model.model == "text-embedding-3-small"
    assert emb.model.dimensions == 256
