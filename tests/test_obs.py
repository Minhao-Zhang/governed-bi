"""Tests for observability wiring (governed_bi.obs).

LangSmith is the only tracer (Langfuse was removed 2026-08-02) and it is opt-in
by environment. These pin the safe default — no env, no tracing — and the fact
that :func:`obs.usage_callbacks` is *not* a tracer and must keep working
regardless, because curator/SME token accounting reads it back.
"""

from __future__ import annotations

from governed_bi import obs


def test_langsmith_enabled_reflects_env(monkeypatch):
    monkeypatch.delenv("LANGCHAIN_TRACING_V2", raising=False)
    monkeypatch.delenv("LANGSMITH_TRACING", raising=False)
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
    assert obs.langsmith_enabled() is False

    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "ls-test")
    assert obs.langsmith_enabled() is True

    # Tracing flag off -> disabled even with a key present.
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")
    assert obs.langsmith_enabled() is False


def test_langsmith_enabled_accepts_langsmith_tracing(monkeypatch):
    # Current LangSmith docs use LANGSMITH_TRACING (not LANGCHAIN_TRACING_V2).
    monkeypatch.delenv("LANGCHAIN_TRACING_V2", raising=False)
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "ls-test")
    assert obs.langsmith_enabled() is True


def test_langsmith_enabled_has_no_acknowledgement_gate(monkeypatch, caplog):
    """Traces log in full and that is the decision — no warning, no opt-in.

    ``GOVERNED_BI_ALLOW_UNMASKED_LANGSMITH`` used to gate a once-per-process
    warning about LangSmith having no content mask. Both went with Langfuse: this
    repo is not production and the datasource filters sensitive columns before
    they can reach a tool message. Pinned so the env var is not quietly
    reintroduced as a condition on whether tracing is considered on.
    """
    monkeypatch.delenv("LANGCHAIN_TRACING_V2", raising=False)
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "ls-test")
    monkeypatch.delenv("GOVERNED_BI_ALLOW_UNMASKED_LANGSMITH", raising=False)
    with caplog.at_level("WARNING", logger="governed_bi.obs"):
        assert obs.langsmith_enabled() is True
    assert caplog.records == []
    assert not hasattr(obs, "_trace_mask")
    assert not hasattr(obs, "_langfuse_handler")


def test_usage_callbacks_off_by_default_and_on_when_asked():
    """TRAP 1 guard: the usage handler is not part of the Langfuse removal.

    ``curator/pipeline.py`` and ``curator/sme.py`` read deep-agent token totals
    back off this handler; if it stops being produced, curator token accounting
    silently reads zero — which is the largest unpriced line in a run.
    """
    assert obs.usage_callbacks(enabled=False) == []
    cbs = obs.usage_callbacks()
    assert len(cbs) == 1
    assert type(cbs[0]).__name__ == "UsageMetadataCallbackHandler"


def test_flush_tracing_is_a_real_drain_not_a_stub(monkeypatch):
    """TRAP 2 guard: ``flush_tracing`` survived the tracer swap with a body.

    It exists because exporters run on a background thread behind an ``atexit``
    hook that SIGTERM / ``os._exit`` / CI cancellation bypasses. That failure mode
    is not Langfuse-specific, so the function was re-pointed at LangChain's own
    drain rather than deleted. Calling it with no tracer configured must be a
    no-op, and it must actually call through.
    """
    import langchain_core.tracers.langchain as lc_tracer

    called: list[int] = []
    monkeypatch.setattr(lc_tracer, "wait_for_all_tracers", lambda: called.append(1))
    obs.flush_tracing()
    assert called == [1]
