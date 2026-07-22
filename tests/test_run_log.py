"""L5/L6: metadata-only portable run log + finalize_and_log seam."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from governed_bi.analyst.answer import (
    Answer,
    ReliabilityTier,
    SemanticAssurance,
    refusal,
)
from governed_bi.analyst.run_log import (
    METADATA_PROVENANCE_KEYS,
    FinalizeCtx,
    append_run_record,
    build_metadata_record,
    count_run_records,
    finalize_and_log,
    load_run_record,
    make_conversation_checkpointer,
    strip_ledger_for_log,
)
from governed_bi.config import Environment, Settings
from governed_bi.provenance import new_run_id


@pytest.fixture
def settings(tmp_path):
    return replace(
        Settings.for_env(Environment.dev),
        run_log_kind="sqlite",
        run_log_path=str(tmp_path / "runs.sqlite"),
        conversation_checkpointer_kind="sqlite",
        conversation_checkpointer_path=str(tmp_path / "ckpt.sqlite"),
    )


def _refusal(**prov):
    return refusal(escalation="nope", provenance=dict(prov))


def test_strip_ledger_drops_sql_and_result():
    ledger = [
        {
            "action": "run_query",
            "verdict": "pass",
            "sql": "SELECT 1",
            "result": {"columns": ["a"], "rows": [[1]], "row_count": 1},
            "duration_ms": 3,
            "ts": "2026-01-01T00:00:00+00:00",
        }
    ]
    stripped = strip_ledger_for_log(ledger)
    assert "sql" not in stripped[0]
    assert "result" not in stripped[0]
    assert stripped[0]["verdict"] == "pass"
    assert stripped[0]["duration_ms"] == 3


def test_strip_ledger_drops_reason_error_text():
    """Metadata-only: reason may echo SQL fragments / PII / question literals."""
    ledger = [
        {
            "action": "run_query",
            "verdict": "error",
            "sql": 'SELECT * FROM t WHERE d = \'March 3rd 2021\'',
            "reason": 'invalid input syntax for type date: "March 3rd 2021"',
            "layer": None,
            "duration_ms": 1,
            "ts": "2026-01-01T00:00:00+00:00",
            "allowed": ["beer_factory.transaction"],
            "licensed_ids": ["tbl_x"],
        },
        {
            "action": "run_query",
            "verdict": "block",
            "reason": "column not in the allowlist: public.patients.ssn",
            "layer": "policy_blacklist",
            "sql": "SELECT ssn FROM patients",
            "duration_ms": 2,
            "ts": "2026-01-01T00:00:01+00:00",
        },
    ]
    stripped = strip_ledger_for_log(ledger)
    for entry in stripped:
        assert "reason" not in entry
        assert "sql" not in entry
        assert "result" not in entry
    assert stripped[0]["verdict"] == "error"
    assert stripped[0]["allowed"] == ["beer_factory.transaction"]
    assert stripped[1]["layer"] == "policy_blacklist"


def test_finalize_and_log_stamps_identical_keys_and_upserts(settings):
    run_id = new_run_id()
    ctx = FinalizeCtx(
        settings=settings,
        run_id=run_id,
        thread_id="t1",
        n_human=1,
        token_usage=[
            {
                "source": "agent_core",
                "usage_metadata": {
                    "input_tokens": 10,
                    "output_tokens": 4,
                    "total_tokens": 14,
                },
            }
        ],
        t0=None,
        outcome="refuse",
    )
    ans = _refusal(refused_by="refuse_gate", governance_ledger=[
        {"action": "run_query", "verdict": "block", "sql": "SELECT secret", "result": {"rows": [1]}}
    ])
    stamped = finalize_and_log(ans, ctx=ctx)
    for key in METADATA_PROVENANCE_KEYS:
        assert key in stamped.provenance, key

    rec = load_run_record("t1:1", settings)
    assert rec is not None
    assert rec["turn_id"] == "t1:1"
    assert "question" not in rec
    assert "sql" not in rec
    ledger = rec.get("governance_ledger") or []
    assert ledger and "sql" not in ledger[0] and "result" not in ledger[0]

    # Idempotent UPSERT
    finalize_and_log(ans, ctx=ctx)
    assert count_run_records(settings) == 1


def test_success_and_refusal_share_metadata_keys(settings):
    ctx = FinalizeCtx(
        settings=settings,
        run_id=new_run_id(),
        thread_id="t2",
        n_human=2,
    )
    refused = finalize_and_log(_refusal(refused_by="x"), ctx=ctx)
    ok = finalize_and_log(
        Answer(
            tier=ReliabilityTier.governed,
            text="ok",
            sql=None,
            provenance={"tables_used": []},
            safety_clearance=True,
            semantic_assurance=SemanticAssurance.grounded,
        ),
        ctx=replace(ctx, n_human=3, outcome="finalize"),
    )
    assert set(METADATA_PROVENANCE_KEYS).issubset(refused.provenance)
    assert set(METADATA_PROVENANCE_KEYS).issubset(ok.provenance)


def test_make_conversation_checkpointer_sqlite(settings):
    saver = make_conversation_checkpointer(settings)
    assert saver is not None
    # Smoke: setup already called; put/get would need a full graph — type check is enough.
    assert hasattr(saver, "put")


def test_standalone_chat_graph_wires_checkpointer(tmp_path):
    """L3: build_standalone_chat_graph attaches a real saver (not dead code)."""
    from dataclasses import replace as dc_replace

    from governed_bi.api.graph_app import build_standalone_chat_graph
    from governed_bi.api.stack import ServeStack
    from governed_bi.config import DataSourceConfig, Environment, Settings
    from governed_bi.corpus import load_corpus
    from governed_bi.gateway import Identity

    settings = dc_replace(
        Settings.for_env(Environment.dev),
        conversation_checkpointer_kind="sqlite",
        conversation_checkpointer_path=str(tmp_path / "ckpt.sqlite"),
        run_log_kind="off",
    )
    corpus = load_corpus(Path(__file__).resolve().parents[1] / "corpus")
    stack = ServeStack(
        corpus_full=corpus,
        corpus_analyst=corpus.for_analyst(),
        settings=settings,
        dialect="sqlite",
        sqlite_path=Path(__file__).resolve().parents[1]
        / "data"
        / "bird"
        / "beer_factory.sqlite",
        identity=Identity(user="dev", all_access=True),
        embedder=None,
        narrator=None,
        model_name=None,
        has_live_model=False,
        chat_model=object(),  # non-None so graph can compile; node won't run here
        conversation_checkpointer=None,
    )
    graph = build_standalone_chat_graph(stack)
    assert graph.checkpointer is not None


def test_jsonl_upsert_serializes_concurrent_writes(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    settings = replace(
        Settings.for_env(Environment.dev),
        run_log_kind="jsonl",
        run_log_path=str(tmp_path / "runs.jsonl"),
    )

    def write(i: int) -> None:
        append_run_record(
            {
                "turn_id": f"t:{i}",
                "run_id": f"r{i}",
                "outcome": "finalize",
            },
            settings,
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(write, range(40)))
    assert count_run_records(settings) == 40


def test_build_metadata_record_has_no_verbatim(settings):
    ans = _refusal(
        refused_by="x",
        governance_ledger=[
            {
                "action": "run_query",
                "verdict": "error",
                "sql": "SELECT 1",
                "result": {},
                "reason": 'invalid input syntax for type date: "March 3rd 2021"',
            }
        ],
    )
    ctx = FinalizeCtx(settings=settings, run_id="r", thread_id="t", n_human=1)
    stamped = finalize_and_log(ans, ctx=ctx)
    rec = build_metadata_record(stamped, ctx=ctx, provenance=stamped.provenance)
    blob = str(rec)
    assert "SELECT 1" not in blob
    assert "March 3rd" not in blob
    assert "reason" not in blob
    assert "question" not in rec
