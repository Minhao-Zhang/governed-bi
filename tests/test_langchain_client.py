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
    assert chat.model.model_name == "gpt-5.6-luna"
    assert isinstance(chat, ChatClient)


def test_chat_client_from_config_builds_bedrock():
    pytest.importorskip("langchain_aws")  # the `bedrock` extra
    from langchain_aws import ChatBedrockConverse

    models = ModelConfig(
        provider="bedrock",
        llm_model="us.anthropic.claude-sonnet-4-5-20250929-v1:0",
        region="us-east-1",
    )
    chat = LangChainChatClient.from_config(models)
    assert isinstance(chat.model, ChatBedrockConverse)
    assert chat.model.model_id == models.llm_model
    assert isinstance(chat, ChatClient)


def test_embedder_from_config_builds_bedrock():
    pytest.importorskip("langchain_aws")  # the `bedrock` extra
    from langchain_aws import BedrockEmbeddings

    models = ModelConfig(
        provider="bedrock",
        embedding_model="amazon.titan-embed-text-v2:0",
        region="us-east-1",
    )
    emb = LangChainEmbedder.from_config(models)
    assert isinstance(emb.model, BedrockEmbeddings)
    assert emb.model.model_id == models.embedding_model
    assert isinstance(emb, Embedder)


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


def test_the_embedder_gets_the_same_retry_and_timeout_policy_as_the_chat_model(
    monkeypatch,
):
    """`max_retries` is documented as a stack-wide knob and reached only the chat
    client, so an embedding call had no retry budget and no wall-clock bound. Both
    matter under the eval's concurrency knobs: every serve worker embeds its
    question, and a 429 or a stalled connection there fails the turn as a crash.
    """
    pytest.importorskip("langchain_openai")

    from dataclasses import replace

    from governed_bi.config import load_settings
    from governed_bi.llm import LangChainChatClient, LangChainEmbedder

    models = replace(load_settings().models, max_retries=5, request_timeout_s=42.0)
    # Hermetic: constructing the client needs *a* key, never a valid one — no
    # request is made here.
    monkeypatch.setenv(models.api_key_env, "sk-test-not-a-real-key")

    embedder = LangChainEmbedder.from_config(models)
    inner = getattr(embedder, "_model", None) or getattr(embedder, "model", None)
    assert inner.max_retries == 5
    assert getattr(inner, "request_timeout", None) == 42.0

    chat = LangChainChatClient.from_config(models)
    chat_inner = getattr(chat, "model", None)
    assert chat_inner.max_retries == 5
