"""Tests for the HTTP API (governed_bi.api), fully offline.

Skipped unless the ``api`` extra (FastAPI) is installed. The app is built per test
via the factory, AFTER the session-wide hermetic fixture (tests/conftest.py) has
stripped OPENAI_API_KEY — so the stack is the deterministic offline one (template
SQL generator, no narrator), and no test ever reaches a live model.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from governed_bi.api import create_app  # noqa: E402
from governed_bi.api.stack import build_stack  # noqa: E402

BIRD_DB = Path(__file__).resolve().parents[1] / "data" / "bird" / "beer_factory.sqlite"


@pytest.fixture
def client() -> TestClient:
    # create_app() builds the stack now (after the hermetic fixture ran), so it is
    # the offline profile: no key -> template generator, no narrator.
    return TestClient(create_app())


# --------------------------------------------------------------------------- #
# meta / audit reads
# --------------------------------------------------------------------------- #


def test_capabilities_reports_offline_dev(client):
    body = client.get("/capabilities").json()
    assert body["environment"] == "dev"
    assert body["dialect"] == "sqlite"
    assert body["has_live_model"] is False  # hermetic env: no key -> offline
    assert body["model"] is None  # offline: no model name (locks the live<->model consistency)
    assert body["can_stream"] is False  # this REST API is request/response
    assert body["can_edit"] is False
    assert body["edit_mode"] is None


def test_health_is_green(client):
    body = client.get("/health").json()
    assert body["ci_green"] is True
    assert body["findings"] == []
    assert body["counts"]["table"] == 5
    assert body["n_suspect_columns"] == 1  # customers.ZipCode
    assert body["n_excluded"] == 1  # transaction.CreditCardNumber


def test_schema_exposes_columns_and_governance(client):
    tables = {t["physical_name"]: t for t in client.get("/schema").json()}
    assert set(tables) == {"customers", "transaction", "rootbeer", "rootbeerbrand", "rootbeerreview"}
    cols = {c["physical_name"]: c for c in tables["transaction"]["columns"]}
    # The full (audit) corpus is served here, so the excluded PII column is visible + flagged.
    assert cols["CreditCardNumber"]["excluded"] is True
    zip_col = {c["physical_name"]: c for c in tables["customers"]["columns"]}["ZipCode"]
    assert zip_col["reliability"] == "suspect"


def test_graph_nodes_and_edges(client):
    graph = client.get("/graph").json()
    nodes = {n["id"]: n for n in graph["nodes"]}
    assert len(nodes) == 5
    assert nodes["tbl_beer_factory_customers"]["row_count"] == 554
    assert len(graph["edges"]) == 4
    edge = graph["edges"][0]
    assert {"id", "source", "target", "on", "cardinality", "confidence", "low_confidence"} <= edge.keys()
    # beer_factory joins are all 0.95 -> above the low-confidence threshold.
    assert all(e["low_confidence"] is False for e in graph["edges"])


def test_corpus_assets_and_type_filter(client):
    everything = client.get("/corpus/assets").json()
    assert {r["asset_type"] for r in everything} >= {"join", "metric", "term", "negative_example"}
    metrics = client.get("/corpus/assets", params={"type": "metric"}).json()
    assert metrics and all(r["asset_type"] == "metric" for r in metrics)


def test_skills(client):
    skills = client.get("/skills").json()
    assert len(skills) == 1
    assert skills[0]["body"].strip()


# --------------------------------------------------------------------------- #
# chat (governed serve flow) — needs the committed DB
# --------------------------------------------------------------------------- #


@pytest.mark.skipif(not BIRD_DB.exists(), reason="vendored beer_factory.sqlite not present")
def test_chat_governed_answer_carries_result(client):
    r = client.post("/chat", json={"question": "What is the total revenue?", "session_id": "s"})
    assert r.status_code == 200
    body = r.json()
    assert body["tier"] == "governed"
    assert body["safety_clearance"] is True
    assert body["semantic_assurance"] == "certified"
    assert "SUM(PurchasePrice)" in body["sql"]
    # Offline profile: no narrator -> compact render; rows are still carried.
    assert "total_revenue" in body["text"]
    assert body["result"]["columns"] == ["total_revenue"]
    assert body["result"]["rows"][0][0] == pytest.approx(18496.0)
    assert body["provenance"]["metric_id"] == "metric_revenue"


@pytest.mark.skipif(not BIRD_DB.exists(), reason="vendored beer_factory.sqlite not present")
def test_chat_refuses_out_of_scope(client):
    r = client.post(
        "/chat", json={"question": "How many employees work at the factory?", "session_id": "s"}
    )
    body = r.json()
    assert body["tier"] == "refused"
    assert body["sql"] is None
    assert body["result"] is None
    assert body["escalation"]


@pytest.mark.skipif(not BIRD_DB.exists(), reason="vendored beer_factory.sqlite not present")
def test_chat_accepts_history_turns(client):
    # Exercises the working-memory rebuild loop (the stateless-chat mechanism).
    r = client.post(
        "/chat",
        json={
            "question": "What is the total revenue?",
            "session_id": "s",
            "history": [
                {"role": "user", "text": "hi"},
                {"role": "assistant", "text": "hello"},
            ],
        },
    )
    assert r.status_code == 200
    assert r.json()["tier"] == "governed"


# --------------------------------------------------------------------------- #
# error / validation / ops paths (offline; no DB needed)
# --------------------------------------------------------------------------- #


def test_livez(client):
    r = client.get("/livez")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_corpus_assets_rejects_unknown_type(client):
    assert client.get("/corpus/assets", params={"type": "bogus"}).status_code == 422
    # 'table' is a real asset_type but not selectable here -> also rejected.
    assert client.get("/corpus/assets", params={"type": "table"}).status_code == 422


def test_chat_rejects_blank_question(client):
    assert client.post("/chat", json={"question": ""}).status_code == 422
    assert client.post("/chat", json={"question": "   "}).status_code == 422  # stripped -> empty


def test_chat_rejects_invalid_history_role(client):
    r = client.post(
        "/chat",
        json={"question": "What is the total revenue?", "history": [{"role": "system", "text": "x"}]},
    )
    assert r.status_code == 422


def test_chat_returns_503_when_db_missing():
    # Inject a stack pointing at a nonexistent DB; no DB file needed for this test.
    stack = replace(build_stack(), sqlite_path=Path("does/not/exist.sqlite"))
    client = TestClient(create_app(stack))
    r = client.post("/chat", json={"question": "What is the total revenue?"})
    assert r.status_code == 503
    assert r.json()["detail"] == "database unavailable"  # generic; no path leaked


def test_cors_allows_configured_origin(monkeypatch):
    monkeypatch.setenv("GOVERNED_BI_CORS_ORIGINS", "https://app.example.com")
    client = TestClient(create_app())
    r = client.get("/capabilities", headers={"Origin": "https://app.example.com"})
    assert r.headers.get("access-control-allow-origin") == "https://app.example.com"
