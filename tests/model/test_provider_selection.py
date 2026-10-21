"""The OpenAI gateway spells effort, timeout and retries in one place.

Call sites pass a surface and an intent. This module is what turns that into the
client constructor, so a second spelling at a call site cannot drift.
"""

from __future__ import annotations

import pytest

from governed_bi.model import provider as P


def test_chat_model_uses_the_openai_spelling(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    def fake(model_id: str, **kwargs: object) -> object:
        captured["model_id"] = model_id
        captured["kwargs"] = kwargs
        return object()

    monkeypatch.setattr("langchain.chat_models.init_chat_model", fake)
    P.chat_model(
        "gpt-6-luna",
        surface="agent",
        effort="xhigh",
        timeout=300.0,
        max_retries=3,
        max_output_tokens=32_000,
        tools=True,
    )
    assert captured["model_id"] == "gpt-6-luna"
    assert captured["kwargs"] == {
        "model_provider": "openai",
        "use_responses_api": True,
        "reasoning_effort": "xhigh",
        "timeout": 300.0,
        "max_retries": 3,
        "max_tokens": 32_000,
    }


def test_a_non_openai_provider_is_refused() -> None:
    with pytest.raises(ValueError, match="openai"):
        P.chat_model("gpt-6-luna", provider="other")
    with pytest.raises(ValueError, match="openai"):
        P.embedder("text-embedding-3-large", provider="other")


def test_tools_are_what_selects_the_responses_api(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    def fake(model_id: str, **kwargs: object) -> object:
        captured.update(kwargs)
        return object()

    monkeypatch.setattr("langchain.chat_models.init_chat_model", fake)
    P.chat_model("gpt-6-luna", tools=False, effort="medium")
    assert "use_responses_api" not in captured
    assert captured["reasoning_effort"] == "medium"


def test_reasoning_effort_of_reads_the_attribute() -> None:
    class Client:
        reasoning_effort = "xhigh"

    class Silent:
        reasoning_effort = "  "

    assert P.reasoning_effort_of(Client()) == "xhigh"
    assert P.reasoning_effort_of(Silent()) is None
    assert P.reasoning_effort_of(object()) is None


def test_credentials_follow_the_openai_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert P.credential_names() == ("OPENAI_API_KEY",)
    assert P.credentials_present() is False
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    assert P.credentials_present() is True


def test_the_embedding_default_is_the_openai_model() -> None:
    from governed_bi.model.openai_embedder import OPENAI_EMBEDDING_MODEL
    from governed_bi.registry.settings import setting_default

    assert P.default_embedding_model() == OPENAI_EMBEDDING_MODEL == setting_default("embedding_model")


def test_embedder_is_the_openai_adapter() -> None:
    from governed_bi.model.openai_embedder import OpenAIEmbedder

    built = P.embedder("text-embedding-3-large", dimensions=64, max_retries=2, timeout=5.0)
    assert isinstance(built, OpenAIEmbedder)
    assert built.requested_model == "text-embedding-3-large"
    assert built.dimensions == 64
