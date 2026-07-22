"""M5 run-log: full-content OFF strip, prod ack, emit_run_record on error."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from governed_bi.analyst.answer import Answer, ReliabilityTier, SemanticAssurance
from governed_bi.analyst.run_log import (
    FinalizeCtx,
    assert_full_content_policy,
    count_run_records,
    emit_run_record,
    finalize_and_log,
    load_run_record,
    strip_ledger_for_log,
)
from governed_bi.config import Environment, Settings, load_settings
from governed_bi.provenance import Producer


def test_strip_ledger_drops_sql_and_result_when_metadata_only():
    ledger = [
        {
            "action": "execute",
            "verdict": "pass",
            "sql": "SELECT 1",
            "result": [{"a": 1}],
            "duration_ms": 3,
            "ts": "t",
        }
    ]
    stripped = strip_ledger_for_log(ledger, full_content=False)
    assert stripped == [
        {"action": "execute", "verdict": "pass", "duration_ms": 3, "ts": "t"}
    ]
    full = strip_ledger_for_log(ledger, full_content=True, row_previews=True)
    assert full[0]["sql"] == "SELECT 1"
    assert full[0]["result"] == [{"a": 1}]
    # Tier C off even when full_content on → no result
    no_c = strip_ledger_for_log(ledger, full_content=True, row_previews=False)
    assert "sql" in no_c[0]
    assert "result" not in no_c[0]


def test_finalize_metadata_only_has_no_verbatim(tmp_path: Path):
    settings = replace(
        load_settings(apply_local=False),
        run_log_kind="sqlite",
        run_log_path=str(tmp_path / "runs.sqlite"),
        log_full_content=False,
    )
    ans = Answer(
        tier=ReliabilityTier.governed,
        text="hello secret question echo",
        sql="SELECT secret FROM t",
        provenance={
            "governance_ledger": [
                {
                    "action": "execute",
                    "verdict": "pass",
                    "sql": "SELECT 1",
                    "result": [1],
                }
            ]
        },
        safety_clearance=True,
        semantic_assurance=SemanticAssurance.grounded,
    )
    stamped = finalize_and_log(
        ans,
        ctx=FinalizeCtx(
            settings=settings,
            run_id="r1",
            thread_id="th1",
            n_human=1,
            producer=Producer.serve,
            question="what is the secret?",
        ),
    )
    rec = load_run_record(stamped.provenance["turn_id"], settings)
    assert rec is not None
    assert "question" not in rec
    assert "sql" not in rec
    assert "answer" not in rec
    assert "answer_text" not in rec
    blob = str(rec)
    assert "SELECT secret" not in blob
    assert "what is the secret" not in blob
    assert "hello secret" not in blob
    for entry in rec.get("governance_ledger") or []:
        assert "sql" not in entry
        assert "result" not in entry


def test_prod_full_content_requires_ack():
    settings = replace(
        Settings.for_env(Environment.prod),
        log_full_content=True,
        log_full_content_ack=False,
        single_all_access_identity=True,
    )
    with pytest.raises(RuntimeError, match="log_full_content_ack"):
        assert_full_content_policy(settings)
    with pytest.raises(RuntimeError, match="log_full_content_ack"):
        from governed_bi.api.stack import build_stack

        build_stack(settings)


def test_emit_run_record_on_error(tmp_path: Path):
    settings = replace(
        load_settings(apply_local=False),
        run_log_kind="sqlite",
        run_log_path=str(tmp_path / "runs.sqlite"),
    )
    rec = emit_run_record(
        settings=settings,
        producer=Producer.curator,
        run_id="abc",
        thread_id="abc",
        outcome="error",
        error="boom",
    )
    assert rec["outcome"] == "error"
    assert load_run_record(rec["turn_id"], settings)["error"] == "boom"


def test_invoke_agent_emits_once_on_error(tmp_path: Path):
    """Failed deep-agent invoke still writes exactly one portable record (F6)."""
    from governed_bi.curator.pipeline import _invoke_agent

    settings = replace(
        load_settings(apply_local=False),
        run_log_kind="sqlite",
        run_log_path=str(tmp_path / "runs.sqlite"),
    )
    agent = MagicMock()
    agent.invoke.side_effect = RuntimeError("invoke exploded")
    result, _counts, err = _invoke_agent(
        agent,
        user="curate please",
        max_agent_steps=2,
        settings=settings,
        run_id="run-x",
        thread_id="thread-x",
    )
    assert result is None
    assert err is not None and "invoke exploded" in err
    assert count_run_records(settings) == 1
    rec = load_run_record("thread-x:1", settings)
    assert rec is not None
    assert rec["outcome"] == "error"
    assert rec["producer"] == "curator"
