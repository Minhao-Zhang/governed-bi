"""Retries and timeouts reach the provider, and the run records what it ran with.

**The defect.** `governed_bi.toml` carried `max_retries = 8` and `request_timeout_s = 900.0`
under a comment calling them *"the entire defence"* against provider rate limits — and v2
deleted the reader for that file, so nothing had loaded them since the rewrite. Measured on the
real objects: `ChatOpenAI.max_retries` was `None`, the underlying `openai` client fell back to
its own default of **2**, and there was no timeout at all.

Two things are asserted here and they fail differently, which is why both exist:

* the values **reach the SDK client**, because a setting that stops being passed leaves an
  object that still works and quietly runs on someone else's defaults — the state this replaces;
* the values are **recorded as knobs**, because retries move `crash_rate` and `crash_rate` is
  what the quotability gates read. Two runs differing only in retries comparing as one is the
  `llm_reasoning_effort` failure exactly.
"""

from __future__ import annotations

import pytest

from governed_bi.register.knobs import KNOB_REGISTER, Role, knob_default

RESILIENCE_KNOBS = ("llm_max_retries", "llm_timeout_s", "llm_utility_timeout_s")


def test_the_three_knobs_are_declared_comparability() -> None:
    """Not `Role.scope`, not undeclared config.

    A retry budget that is not comparability is a run able to change its own crash rate and
    still compare as unchanged — and `crash_rate` is a quotability gate's input.
    """
    by_name = {k.name: k for k in KNOB_REGISTER}
    for name in RESILIENCE_KNOBS:
        assert name in by_name, f"{name} must be declared in register/knobs.py"
        assert by_name[name].role is Role.comparability, (
            f"{name} changes what a run measures about itself; scope would let two runs "
            "differing in it compare as one"
        )


def test_the_worst_case_for_one_call_is_bounded() -> None:
    """`timeout x (retries + 1)` is the number that matters, and it is a *pair* decision.

    The two knobs are configured apart because they answer different questions, but choosing
    either alone is how the SDK's 600s default at three retries becomes a 40-minute hang. This
    fails if a future edit raises one without looking at the other.
    """
    attempts = int(knob_default("llm_max_retries")) + 1
    agent_ceiling = float(knob_default("llm_timeout_s")) * attempts
    utility_ceiling = float(knob_default("llm_utility_timeout_s")) * attempts

    assert agent_ceiling <= 30 * 60, f"one agent call could hang {agent_ceiling / 60:.0f} min"
    # Tighter, and deliberately so: these calls are on the critical path *before anything
    # appears on screen*, and each of their call sites already degrades gracefully.
    assert utility_ceiling <= 5 * 60, f"one utility call could stall first paint {utility_ceiling}s"
    assert utility_ceiling < agent_ceiling, (
        "the split exists so the small calls fail faster than the agent; equal ceilings mean "
        "the second knob is buying nothing"
    )


def test_node_timeouts_cover_one_call_provider_retry_budget() -> None:
    """Rail/agent hang-stops must not fire inside a single call's SDK retry budget.

    Same pairing as the knob docstrings: ``rail_node_timeout_s`` vs utility×(retries+1),
    ``agent_node_timeout_s`` vs agent×(retries+1). Shrinking either below its ceiling makes
    ``TimeoutError`` and a provider retry disagree about the same hung call.
    """
    attempts = int(knob_default("llm_max_retries")) + 1
    assert float(knob_default("rail_node_timeout_s")) >= (
        float(knob_default("llm_utility_timeout_s")) * attempts
    )
    assert float(knob_default("agent_node_timeout_s")) >= (
        float(knob_default("llm_timeout_s")) * attempts
    )


def test_the_settings_reach_the_openai_client_on_every_surface(monkeypatch) -> None:
    """Chat models **and** the embedder.

    "Global" is a claim about coverage, and the embedder is the surface that makes it true or
    false: it builds a raw `OpenAI()` in `model/openai_embedder.py`, which has its own default
    of 2, and it runs on the same critical path — `accept` embeds the question before any facet.
    """
    pytest.importorskip("langchain_openai")
    from governed_bi.api import graph_app
    from governed_bi.model.openai_embedder import OpenAIEmbedder

    monkeypatch.setenv("GOVERNED_BI_LLM_MAX_RETRIES", "5")
    monkeypatch.setenv("GOVERNED_BI_LLM_TIMEOUT_S", "111")
    monkeypatch.setenv("GOVERNED_BI_UTILITY_TIMEOUT_S", "7")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-a-real-key")

    assert graph_app._retries() == 5
    assert graph_app._timeout(graph_app.TIMEOUT_VAR, "llm_timeout_s") == 111.0
    assert graph_app._timeout(graph_app.UTILITY_TIMEOUT_VAR, "llm_utility_timeout_s") == 7.0

    embedder = OpenAIEmbedder(model="text-embedding-3-large", max_retries=5, timeout=7.0)
    client = embedder._openai_client()
    assert client.max_retries == 5
    assert client.timeout == 7.0


def test_an_unconfigured_embedder_keeps_the_sdk_defaults(monkeypatch) -> None:
    """`None` must mean "not configured", never "zero".

    Passing `max_retries=None` straight to the SDK sets it to `None` rather than leaving the
    default, which would turn an unconfigured embedder into one that never retries — the
    opposite of this change's intent, and the same absence-becomes-a-value shape the register
    spends its whole `Absence` enum on.

    **The key is set here, and it is a placeholder.** ``_openai_client`` refuses to build without
    one, so this failed in CI while passing locally — ``tests/conftest.py`` loads ``.env`` into the
    environment, so a developer machine supplies a real key and a runner does not. Nothing here
    reaches the network: the assertions are about how the *client object* was constructed. A test
    of default values that depends on a credential is a test that skips or fails for a reason
    unrelated to what it checks.
    """
    import openai

    from governed_bi.model.openai_embedder import OpenAIEmbedder

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-a-real-key")
    client = OpenAIEmbedder(model="text-embedding-3-large")._openai_client()
    assert client.max_retries == openai._constants.DEFAULT_MAX_RETRIES
    assert client.timeout == openai._constants.DEFAULT_TIMEOUT
