"""Tests for the HTTP API (governed_bi.api), fully offline.

Skipped unless the ``api`` extra (FastAPI) is installed. The app is built per test
via the factory, AFTER the session-wide hermetic fixture (tests/conftest.py) has
stripped OPENAI_API_KEY — so the stack is the deterministic offline one (template
SQL generator, no narrator), and no test ever reaches a live model.
"""

from __future__ import annotations

import shutil
from dataclasses import replace
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from governed_bi.api import create_app  # noqa: E402
from governed_bi.api.stack import build_stack  # noqa: E402

BIRD_DB = Path(__file__).resolve().parents[1] / "data" / "bird" / "beer_factory.sqlite"
CORPUS_ROOT = Path(__file__).resolve().parents[1] / "corpus"


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
    # The plain REST app never advertises streaming (that is the LangGraph server's
    # job; see test_routes_app_advertises_streaming). can_edit is dev-derived.
    assert body["can_stream"] is False
    assert isinstance(body["can_edit"], bool)


def test_capabilities_flags_reflect_the_stack():
    on = TestClient(create_app(replace(build_stack(), can_stream=True, can_edit=True, edit_mode="file")))
    body = on.get("/capabilities").json()
    assert body["can_stream"] is True
    assert body["can_edit"] is True
    assert body["edit_mode"] == "file"

    off = TestClient(create_app(replace(build_stack(), can_stream=False, can_edit=False, edit_mode=None)))
    body = off.get("/capabilities").json()
    assert body["can_stream"] is False
    assert body["can_edit"] is False
    assert body["edit_mode"] is None


def test_build_stack_defaults_can_stream_false(monkeypatch):
    # The shared factory builds the plain REST app too, which has no streaming
    # endpoint, so the default must be False regardless of whether langgraph is
    # installed. Streaming is opted in by the LangGraph-server routes app / env.
    monkeypatch.delenv("GOVERNED_BI_CAN_STREAM", raising=False)
    assert build_stack().can_stream is False


def test_routes_app_advertises_streaming():
    # routes.py is only mounted on the LangGraph server (which fronts the chat
    # graph), so it flips can_stream on.
    from governed_bi.api.routes import app as routes_app

    assert TestClient(routes_app).get("/capabilities").json()["can_stream"] is True


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


def test_knowledge_graph_nodes_and_edges(client):
    kg = client.get("/knowledge-graph").json()
    node_ids = {n["id"] for n in kg["nodes"]}
    kinds = {n["kind"] for n in kg["nodes"]}
    assert {"table", "join", "metric", "term"} <= kinds
    assert "tbl_beer_factory_customers" in node_ids
    # Every edge connects two real nodes (the builder drops dangling edges).
    for e in kg["edges"]:
        assert e["source"] in node_ids
        assert e["target"] in node_ids
    relations = {e["relation"] for e in kg["edges"]}
    assert "join" in relations  # join -> its two tables
    assert "measures" in relations  # metric -> base_table


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


# --------------------------------------------------------------------------- #
# corpus edit (dev file-write; gated on can_edit) — writes to a temp corpus root
# --------------------------------------------------------------------------- #


_VALID_METRIC = {
    "asset_type": "metric",
    "id": "metric_test_kpi",
    "name": "Test KPI",
    "base_table": "tbl_beer_factory_transaction",
    "expression": "count of transactions",
}


def _edit_client(tmp_path, **flags):
    # Copy the corpus into a temp dir so edits validate + write against a real
    # (isolated) corpus and never touch the committed tree. The endpoint reloads
    # the corpus from corpus_root on each call, so writes and validation stay
    # consistent within the session.
    shutil.copytree(CORPUS_ROOT / "beer_factory", tmp_path / "beer_factory")
    stack = replace(build_stack(), corpus_root=tmp_path, db="beer_factory", **flags)
    return TestClient(create_app(stack))


def test_corpus_edit_disabled_returns_403(tmp_path):
    client = _edit_client(tmp_path, can_edit=False, edit_mode=None)
    r = client.post("/corpus/edit", json={"asset": _VALID_METRIC})
    assert r.status_code == 403


def test_corpus_edit_rejects_invalid_asset(tmp_path):
    client = _edit_client(tmp_path, can_edit=True, edit_mode="file")
    bad = {"asset_type": "metric", "id": "metric_x"}  # missing name/base_table/expression
    assert client.post("/corpus/edit", json={"asset": bad}).status_code == 422


def test_corpus_edit_rejects_path_traversal_id(tmp_path):
    client = _edit_client(tmp_path, can_edit=True, edit_mode="file")
    evil = {**_VALID_METRIC, "id": "../../etc/metric_evil"}
    assert client.post("/corpus/edit", json={"asset": evil}).status_code == 422


def test_corpus_edit_writes_valid_asset(tmp_path):
    client = _edit_client(tmp_path, can_edit=True, edit_mode="file")
    r = client.post("/corpus/edit", json={"asset": _VALID_METRIC})
    assert r.status_code == 200
    body = r.json()
    assert body["written"] is True
    assert body["findings"] == []
    assert body["asset_id"] == "metric_test_kpi"
    assert body["path"] == "beer_factory/metrics/metric_test_kpi.yaml"
    assert body["diff"]  # a new-file diff
    assert (tmp_path / "beer_factory" / "metrics" / "metric_test_kpi.yaml").exists()


def test_corpus_edit_validates_against_current_disk_not_a_stale_snapshot(tmp_path):
    # Two sequential edits in one process: the second references the asset the
    # first wrote. Because validation reloads the corpus from disk each call, the
    # reference resolves and the write succeeds. A startup-snapshot validation
    # would not see the first write and would wrongly report a dangling reference.
    client = _edit_client(tmp_path, can_edit=True, edit_mode="file")

    first = client.post("/corpus/edit", json={"asset": _VALID_METRIC})
    assert first.json()["written"] is True

    term_bound_to_new_metric = {
        "asset_type": "term",
        "id": "term_test_kpi",
        "name": "Test KPI term",
        "binding": {"asset_type": "metric", "asset_id": "metric_test_kpi"},
    }
    second = client.post("/corpus/edit", json={"asset": term_bound_to_new_metric})
    body = second.json()
    assert body["written"] is True  # the binding resolves against the just-written metric
    assert body["findings"] == []


def test_corpus_edit_rejects_id_violating_convention(tmp_path):
    # A well-formed-looking but convention-violating id (no 'metric_' prefix) is
    # rejected up front, before any filesystem access (closes the info-disclosure).
    client = _edit_client(tmp_path, can_edit=True, edit_mode="file")
    bad = {**_VALID_METRIC, "id": "index"}  # valid chars, wrong convention
    assert client.post("/corpus/edit", json={"asset": bad}).status_code == 422


def test_corpus_edit_findings_block_the_write(tmp_path):
    client = _edit_client(tmp_path, can_edit=True, edit_mode="file")
    broken = {**_VALID_METRIC, "id": "metric_broken", "base_table": "tbl_does_not_exist"}
    r = client.post("/corpus/edit", json={"asset": broken})
    assert r.status_code == 200
    body = r.json()
    assert body["written"] is False
    assert any("does not resolve" in f for f in body["findings"])
    assert not (tmp_path / "beer_factory" / "metrics" / "metric_broken.yaml").exists()


def test_cors_allows_configured_origin(monkeypatch):
    monkeypatch.setenv("GOVERNED_BI_CORS_ORIGINS", "https://app.example.com")
    client = TestClient(create_app())
    r = client.get("/capabilities", headers={"Origin": "https://app.example.com"})
    assert r.headers.get("access-control-allow-origin") == "https://app.example.com"
