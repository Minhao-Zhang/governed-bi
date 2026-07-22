"""SPIKE-1 / L4: usage_metadata survives coercion; after_model lands on token_usage."""

from __future__ import annotations

from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from governed_bi.analyst.middleware import GovernanceMiddleware
from governed_bi.config import Environment, Settings
from governed_bi.corpus import load_corpus
from governed_bi.gateway import Gateway, Identity, SqliteConnector
from governed_bi.llm.fake import FakeToolModel, ai_tool_turn
from governed_bi.analyst.agent import build_agent_core

CORPUS_ROOT = Path(__file__).resolve().parents[1] / "corpus"
BIRD_DB = Path(__file__).resolve().parents[1] / "data" / "bird" / "beer_factory.sqlite"
TXN = "tbl_beer_factory_transaction"


@pytest.fixture
def corpus():
    return load_corpus(CORPUS_ROOT, schema="beer_factory").for_analyst()


@pytest.fixture
def settings():
    return Settings.for_env(Environment.dev)


@pytest.fixture
def identity():
    return Identity(user="dev", all_access=True)


@pytest.fixture
def bird_gateway():
    if not BIRD_DB.exists():
        pytest.skip("vendored beer_factory.sqlite not present")
    conn = SqliteConnector(BIRD_DB)
    yield Gateway(conn)
    conn.close()


def test_coerce_preserves_usage_metadata_on_parallel_tool_calls():
    """SPIKE-1: rebuilt AIMessage keeps usage_metadata + response_metadata."""
    usage = {"input_tokens": 11, "output_tokens": 3, "total_tokens": 14}
    msg = AIMessage(
        content="",
        tool_calls=[
            {"name": "a", "args": {}, "id": "1", "type": "tool_call"},
            {"name": "b", "args": {}, "id": "2", "type": "tool_call"},
        ],
        usage_metadata=usage,
        response_metadata={"model": "fake"},
    )
    out = GovernanceMiddleware._coerce_single_tool_call(msg)
    assert isinstance(out, AIMessage)
    assert len(out.tool_calls) == 1
    assert out.usage_metadata == usage
    assert out.response_metadata.get("model") == "fake"


def test_after_model_token_usage_lands_on_reducer(
    corpus, bird_gateway, settings, identity
):
    """after_model writes token_usage that survives agent.invoke (SPIKE-1 PASS)."""
    usage = {"input_tokens": 5, "output_tokens": 2, "total_tokens": 7}
    tool_msg = ai_tool_turn("inspect_schema", {"table_id": TXN}, "c1")
    # Attach usage onto the tool-call AIMessage so after_model can read it.
    tool_msg = AIMessage(
        content=tool_msg.content,
        tool_calls=tool_msg.tool_calls,
        id=tool_msg.id,
        usage_metadata=usage,
    )
    turns = [
        tool_msg,
        AIMessage(content="done", usage_metadata={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}),
    ]
    agent = build_agent_core(
        corpus,
        bird_gateway,
        identity,
        FakeToolModel(responses=turns),
        settings=settings,
        dialect="sqlite",
        default_schema="beer_factory",
    )
    final = agent.invoke(
        {
            "messages": [HumanMessage("hi")],
            "licensed": [],
            "ledger": [],
            "token_usage": [],
        }
    )
    usage_list = final.get("token_usage") or []
    assert usage_list, "expected after_model to land token_usage entries"
    assert any(
        (e.get("usage_metadata") or {}).get("total_tokens") in (7, 2) for e in usage_list
    )


def test_failed_model_call_records_stub_on_middleware():
    """L4: wrap_model_call appends a metadata-only failed stub before re-raising."""
    from governed_bi.analyst.middleware import GovernanceMiddleware
    from governed_bi.config import Environment, Settings
    from governed_bi.corpus import load_corpus
    from governed_bi.gateway import Gateway, Identity, SqliteConnector

    corpus = load_corpus(CORPUS_ROOT, schema="beer_factory").for_analyst()
    if not BIRD_DB.exists():
        pytest.skip("vendored beer_factory.sqlite not present")
    conn = SqliteConnector(BIRD_DB)
    try:
        mw = GovernanceMiddleware(
            corpus,
            Gateway(conn),
            Identity(user="dev", all_access=True),
            dialect="sqlite",
            default_schema="beer_factory",
            settings=Settings.for_env(Environment.dev),
        )

        class _Req:
            model_settings = {}
            model = object()

            def override(self, **kwargs):
                return self

        def boom(req):
            raise RuntimeError("secret SQL fragment SELECT ssn FROM patients")

        with pytest.raises(RuntimeError, match="secret"):
            mw.wrap_model_call(_Req(), boom)
        assert len(mw.failed_model_calls) == 1
        stub = mw.failed_model_calls[0]
        assert stub["failed"] is True
        assert stub["error_type"] == "RuntimeError"
        assert "secret" not in str(stub)
        assert "ssn" not in str(stub)
    finally:
        conn.close()
