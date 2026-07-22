"""Regression: reused rails graph mints unique turn_ids (eval UPSERT collision)."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage

from governed_bi.analyst.agent import build_serve_rails
from governed_bi.analyst.run_log import count_run_records, load_run_record
from governed_bi.config import Environment, Settings
from governed_bi.corpus import load_corpus
from governed_bi.gateway import Gateway, Identity, SqliteConnector
from governed_bi.llm.fake import FakeToolModel

CORPUS_ROOT = Path(__file__).resolve().parents[1] / "corpus"
BIRD_DB = Path(__file__).resolve().parents[1] / "data" / "bird" / "beer_factory.sqlite"


@pytest.fixture
def corpus():
    return load_corpus(CORPUS_ROOT, schema="beer_factory").for_analyst()


def test_reused_rails_graph_unique_turn_ids_and_run_log_rows(
    corpus, tmp_path
):
    if not BIRD_DB.exists():
        pytest.skip("vendored beer_factory.sqlite not present")
    settings = replace(
        Settings.for_env(Environment.dev),
        run_log_kind="sqlite",
        run_log_path=str(tmp_path / "runs.sqlite"),
    )
    conn = SqliteConnector(BIRD_DB)
    try:
        gateway = Gateway(conn)
        # Scripted refusals via empty agent turns that still finalize.
        model = FakeToolModel(responses=[AIMessage(content="no tools")])
        graph = build_serve_rails(
            corpus=corpus,
            gateway=gateway,
            settings=settings,
            identity=Identity(user="dev", all_access=True),
            model=model,
            session_id="eval-curated",
        )
        turn_ids = []
        for q in ("q1", "q2", "q3"):
            # Reset fake cursor so each invoke gets the same scripted message.
            object.__setattr__(model, "i", 0)
            final = graph.invoke({"question": q, "session_id": "eval-curated"})
            ans = final.get("answer")
            assert ans is not None
            tid = ans.provenance.get("turn_id")
            turn_ids.append(tid)
            assert load_run_record(tid, settings) is not None
        assert turn_ids == ["eval-curated:1", "eval-curated:2", "eval-curated:3"]
        assert count_run_records(settings) == 3
        run_ids = {
            load_run_record(tid, settings)["run_id"] for tid in turn_ids
        }
        assert len(run_ids) == 3
    finally:
        conn.close()
