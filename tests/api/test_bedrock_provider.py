"""api/graph_app.py::_agent_model -- the provider branch (UtkuAI, ported).

Constructs, never calls: a real Bedrock/OpenAI invocation belongs in
tests/model/test_bedrock_embedder.py's credential-gated smoke test, not here.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from contracts import needs  # noqa: E402

pytestmark = needs("D")


class _FakeCredentials:
    OPENAI_KEY_NAMES = ("OPENAI_API_KEY",)

    def __init__(self, has_openai: bool) -> None:
        self._has_openai = has_openai

    def have(self, *names: str) -> bool:
        return self._has_openai


def test_default_provider_is_openai_and_needs_a_key(monkeypatch) -> None:
    from governed_bi.api import graph_app

    monkeypatch.delenv(graph_app.MODEL_PROVIDER_VAR, raising=False)
    with pytest.raises(RuntimeError, match="no model credential"):
        graph_app._agent_model("gpt-4o-mini", _FakeCredentials(has_openai=False))


def test_bedrock_converse_provider_needs_no_openai_key(monkeypatch) -> None:
    """The whole point of the branch: a deployment with zero OpenAI credentials can still
    construct a chat model, because AWS resolves its own credential chain."""
    from governed_bi.api import graph_app

    monkeypatch.setenv(graph_app.MODEL_PROVIDER_VAR, "bedrock_converse")
    model = graph_app._agent_model("us.anthropic.claude-sonnet-5", _FakeCredentials(has_openai=False))
    assert type(model).__name__ == "ChatBedrockConverse"
    assert model.model_id == "us.anthropic.claude-sonnet-5"


def test_bedrock_reasoning_effort_is_forwarded(monkeypatch) -> None:
    from governed_bi.api import graph_app

    monkeypatch.setenv(graph_app.MODEL_PROVIDER_VAR, "bedrock_converse")
    monkeypatch.setenv(graph_app.MODEL_EFFORT_VAR, "low")
    model = graph_app._agent_model("us.anthropic.claude-sonnet-5", _FakeCredentials(has_openai=False))
    assert model.reasoning_effort == "low"


def test_an_unknown_provider_raises(monkeypatch) -> None:
    from governed_bi.api import graph_app

    monkeypatch.setenv(graph_app.MODEL_PROVIDER_VAR, "azure")
    with pytest.raises(RuntimeError, match="not a supported provider"):
        graph_app._agent_model("some-model", _FakeCredentials(has_openai=True))


def test_serve_main_model_matches_the_same_provider_contract(monkeypatch) -> None:
    """serve/__main__.py::_model is kept identical to graph_app._agent_model on purpose
    (see both docstrings) -- confirm the bedrock branch behaves the same way there too."""
    from governed_bi.serve.__main__ import _model

    model = _model("us.anthropic.claude-sonnet-5", _FakeCredentials(has_openai=False), provider="bedrock_converse")
    assert type(model).__name__ == "ChatBedrockConverse"

    with pytest.raises(SystemExit, match="not supported"):
        _model("m", _FakeCredentials(has_openai=True), provider="azure")
