"""Tests for observability wiring (governed_bi.obs).

Both tracers are opt-in by environment and must be no-ops when unset. These run
without the ``tracing`` extra installed, so they pin the safe default: no keys ->
no callbacks, no LangSmith.
"""

from __future__ import annotations

from governed_bi import obs


def test_tracing_callbacks_empty_without_langfuse_keys(monkeypatch):
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    assert obs.tracing_callbacks() == []


def test_tracing_callbacks_empty_when_only_one_key_set(monkeypatch):
    # Both keys are required; a half-configured env stays a no-op.
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    assert obs.tracing_callbacks() == []


def test_langsmith_enabled_reflects_env(monkeypatch):
    monkeypatch.delenv("LANGCHAIN_TRACING_V2", raising=False)
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
    assert obs.langsmith_enabled() is False

    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "ls-test")
    assert obs.langsmith_enabled() is True

    # Tracing flag off -> disabled even with a key present.
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")
    assert obs.langsmith_enabled() is False
