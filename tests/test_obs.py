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


# --------------------------------------------------------------------------- #
# AUDIT T1/S7: `_trace_mask` was 0% covered — conftest strips LANGFUSE_* session
# wide, so the ON path was structurally unreachable. Test the function directly.
# --------------------------------------------------------------------------- #


def test_trace_mask_truncates_long_strings(monkeypatch):
    from governed_bi.obs import _trace_mask

    monkeypatch.setenv("GOVERNED_BI_TRACE_MAX_CHARS", "10")
    out = _trace_mask(data="x" * 50)
    assert out.startswith("x" * 10)
    assert "redacted" in out


def test_trace_mask_recurses_into_dicts_and_lists(monkeypatch):
    from governed_bi.obs import _trace_mask

    monkeypatch.setenv("GOVERNED_BI_TRACE_MAX_CHARS", "5")
    out = _trace_mask(data={"rows": [["abcdefghij"], "short"], "n": 3})
    assert "redacted" in out["rows"][0][0]
    assert out["rows"][1] == "short"
    assert out["n"] == 3  # non-strings pass through untouched


def test_trace_mask_is_a_length_truncator_not_a_secret_redactor(monkeypatch):
    """Documented limitation, pinned so nobody mistakes it for redaction.

    Anything under the limit ships verbatim — a DSN with an inline password, an API
    key, a small row preview. Raise this from a test to a real redactor and this
    assertion is the one to change deliberately.
    """
    from governed_bi.obs import _trace_mask

    monkeypatch.setenv("GOVERNED_BI_TRACE_MAX_CHARS", "300")
    secret = "postgresql://bird:bird@127.0.0.1:5435/bird"
    assert _trace_mask(data=secret) == secret


def test_trace_mask_disabled_by_zero(monkeypatch):
    from governed_bi.obs import _trace_mask

    monkeypatch.setenv("GOVERNED_BI_TRACE_MAX_CHARS", "0")
    assert _trace_mask(data="x" * 500) == "x" * 500


def test_trace_mask_falls_back_to_the_default_on_a_bad_limit(monkeypatch):
    from governed_bi.obs import _trace_mask

    monkeypatch.setenv("GOVERNED_BI_TRACE_MAX_CHARS", "not-a-number")
    assert "redacted" in _trace_mask(data="x" * 500)
