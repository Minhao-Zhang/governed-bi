"""Retries and timeouts must reach the SDK client and be recorded as settings.

A setting that is not passed leaves the SDK default. Retries move ``crash_rate``, which the quotability checks read.
"""

from __future__ import annotations

import pytest

from governed_bi.registry.settings import SETTING_REGISTER, setting_default

RESILIENCE_SETTINGS = ("llm_max_retries", "llm_timeout_s", "llm_utility_timeout_s")


def test_the_three_settings_are_declared_affects_results() -> None:
    """A retry budget that is not a recorded setting lets crash rate change while runs still compare."""
    by_name = {k.name: k for k in SETTING_REGISTER}
    for name in RESILIENCE_SETTINGS:
        assert name in by_name, f"{name} must be declared in registry/settings.py"
        assert by_name[name].env_var, f"{name} must name the variable that overrides it"


def test_the_worst_case_for_one_call_is_bounded() -> None:
    """``timeout x (retries + 1)`` is the pair. Raising either alone is how a call hangs for tens of minutes."""
    attempts = int(setting_default("llm_max_retries")) + 1
    agent_ceiling = float(setting_default("llm_timeout_s")) * attempts
    utility_ceiling = float(setting_default("llm_utility_timeout_s")) * attempts

    assert agent_ceiling <= 30 * 60, f"one agent call could hang {agent_ceiling / 60:.0f} min"
    # Tighter, and deliberately so: these calls are on the critical path *before anything
    # appears on screen*, and each of their call sites already degrades gracefully.
    assert utility_ceiling <= 5 * 60, f"one utility call could stall first paint {utility_ceiling}s"
    assert utility_ceiling < agent_ceiling, (
        "the split exists so the small calls fail faster than the agent; equal ceilings mean "
        "the second setting is buying nothing"
    )


def test_node_timeouts_cover_one_call_provider_retry_budget() -> None:
    """A node timeout inside the SDK retry budget makes ``TimeoutError`` and a retry disagree."""
    attempts = int(setting_default("llm_max_retries")) + 1
    assert float(setting_default("rail_node_timeout_s")) >= (
        float(setting_default("llm_utility_timeout_s")) * attempts
    )
    assert float(setting_default("agent_node_timeout_s")) >= (
        float(setting_default("llm_timeout_s")) * attempts
    )


def test_the_settings_reach_the_openai_client_on_every_surface(monkeypatch) -> None:
    """The embedder builds a raw ``OpenAI()`` with its own default of 2."""
    pytest.importorskip("langchain_openai")
    from governed_bi.model.openai_embedder import OpenAIEmbedder
    from governed_bi.registry.settings import reset_settings, resolve_settings

    monkeypatch.setenv("GOVERNED_BI_LLM_MAX_RETRIES", "5")
    monkeypatch.setenv("GOVERNED_BI_LLM_TIMEOUT_S", "111")
    monkeypatch.setenv("GOVERNED_BI_UTILITY_TIMEOUT_S", "7")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-a-real-key")
    reset_settings()
    settings = resolve_settings()

    assert settings["llm_max_retries"] == 5
    assert settings["llm_timeout_s"] == 111.0
    assert settings["llm_utility_timeout_s"] == 7.0

    embedder = OpenAIEmbedder(model="text-embedding-3-large", max_retries=5, timeout=7.0)
    client = embedder._openai_client()
    assert client.max_retries == 5
    assert client.timeout == 7.0


def test_an_unconfigured_embedder_keeps_the_sdk_defaults(monkeypatch) -> None:
    """``None`` passed through sets the SDK field to ``None`` instead of leaving its default.

    The key is a placeholder: ``_openai_client`` refuses to build without one, and CI has no ``.env``.
    """
    import openai

    from governed_bi.model.openai_embedder import OpenAIEmbedder

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-a-real-key")
    client = OpenAIEmbedder(model="text-embedding-3-large")._openai_client()
    assert client.max_retries == openai._constants.DEFAULT_MAX_RETRIES
    assert client.timeout == openai._constants.DEFAULT_TIMEOUT
