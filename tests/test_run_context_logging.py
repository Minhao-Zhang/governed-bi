"""N12a: RunContext / tracing_config / configure_logging.

See also ``tests/test_trace_metadata.py``, which pins the arm and corpus-hash
fields end to end through the eval driver's own seams.
"""

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


def test_tracing_config_carries_every_correlation_field():
    ctx = RunContext(
        run_id="run-abc",
        turn_id="thread:1",
        corpus_pin="datalake",
        arm="curated",
        schema="beer_factory",
        corpus_content_hash="sha256:abc",
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
    assert meta["corpus_content_hash"] == "sha256:abc"
    assert meta["prompt_set_hash"] == "deadbeef"
    assert "identity" not in meta

    assert "curated" in tags
    assert "beer_factory" in tags
    assert "governed-bi" in tags


def test_usage_callbacks_are_the_only_callbacks_left():
    """Langfuse was removed 2026-08-02. What used to be ``tracing_callbacks`` now
    produces one thing and only on request: the usage handler curator/SME token
    accounting reads back."""
    assert obs.usage_callbacks(enabled=False) == []
    assert len(obs.usage_callbacks()) == 1


def test_tracing_invoke_config_merges_metadata():
    ctx = RunContext(run_id="r1", arm="baseline")
    cfg = tracing_invoke_config(ctx=ctx, recursion_limit=8)
    assert cfg["callbacks"] == []  # no usage handler unless asked for
    assert cfg["metadata"]["run_id"] == "r1"
    assert cfg["tags"] == ["baseline", "governed-bi"]
    assert cfg["recursion_limit"] == 8


def test_tracing_invoke_config_with_usage_attaches_the_usage_handler():
    cfg = tracing_invoke_config(with_usage=True, ctx=RunContext(run_id="r1"))
    assert len(cfg["callbacks"]) == 1


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
