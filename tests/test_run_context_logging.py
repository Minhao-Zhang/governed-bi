"""N12a: RunContext / tracing_config / configure_logging."""

from __future__ import annotations

import logging

from governed_bi import obs
from governed_bi.logging_setup import (
    _reset_for_tests,
    bind_log_context,
    configure_logging,
    reset_log_context,
)
from governed_bi.obs import RunContext, tracing_config, tracing_invoke_config


def test_tracing_config_feeds_both_tracers():
    ctx = RunContext(
        run_id="run-abc",
        turn_id="thread:1",
        corpus_pin="datalake",
        arm="curated",
        schema="beer_factory",
        prompt_set_hash="deadbeef",
        identity="should-not-appear",
    )
    cfg = tracing_config(ctx)
    meta = cfg["metadata"]
    tags = cfg["tags"]

    assert meta["run_id"] == "run-abc"
    assert meta["turn_id"] == "thread:1"
    assert meta["corpus_pin"] == "datalake"
    assert meta["arm"] == "curated"
    assert meta["schema"] == "beer_factory"
    assert meta["prompt_set_hash"] == "deadbeef"
    assert "identity" not in meta

    assert meta["langfuse_session_id"] == "run-abc"
    assert meta["langfuse_user_id"] == "curated"
    assert meta["langfuse_tags"] == tags
    assert "curated" in tags
    assert "beer_factory" in tags
    assert "governed-bi" in tags


def test_tracing_callbacks_none_ctx_matches_empty_env(monkeypatch):
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    assert obs.tracing_callbacks() == []
    assert obs.tracing_callbacks(ctx=None) == []
    ctx = RunContext(run_id="r")
    # ctx does not invent a handler when keys are unset
    assert obs.tracing_callbacks(ctx=ctx) == []


def test_tracing_invoke_config_merges_metadata():
    ctx = RunContext(run_id="r1", arm="baseline")
    cfg = tracing_invoke_config(ctx=ctx, recursion_limit=8)
    assert "callbacks" in cfg
    assert cfg["metadata"]["run_id"] == "r1"
    assert cfg["metadata"]["langfuse_session_id"] == "r1"
    assert cfg["recursion_limit"] == 8


def test_configure_logging_stamps_run_id_on_records(caplog):
    _reset_for_tests()
    configure_logging(level=logging.INFO)
    tokens = bind_log_context(run_id="run-xyz", turn_id="t:1")
    try:
        with caplog.at_level(logging.INFO, logger="governed_bi.test_n12a"):
            logging.getLogger("governed_bi.test_n12a").info("hello")
    finally:
        reset_log_context(tokens)

    assert caplog.records, "expected at least one log record"
    rec = caplog.records[-1]
    assert getattr(rec, "run_id") == "run-xyz"
    assert getattr(rec, "turn_id") == "t:1"
