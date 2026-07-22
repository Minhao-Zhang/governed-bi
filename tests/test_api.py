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
from governed_bi.config import DataSourceConfig, load_settings  # noqa: E402

BIRD_DB = Path(__file__).resolve().parents[1] / "data" / "bird" / "beer_factory.sqlite"
CORPUS_ROOT = Path(__file__).resolve().parents[1] / "corpus"

# Agent-only serve (ADR 0002): answering a /chat turn needs a live model, which
# the hermetic suite forbids. These end-to-end answer cases are live-only; the
# read-only API surface (schema/graph/corpus/capabilities) stays fully offline.
requires_live_serve = pytest.mark.skip(
    reason="agent-only serve needs a live model; covered by scripts/live_smoke.py"
)


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
    # Additive scoping flags: the summary/detail routes are served; no server FTS.
    assert body["can_scope"] is True
    assert body["can_search"] is False


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


def test_build_stack_defaults_can_stream_false():
    # The shared factory builds the plain REST app too, which has no streaming
    # endpoint, so the committed [serve].can_stream default must be False.
    # Streaming is opted in by the LangGraph-server routes app.
    assert build_stack().can_stream is False


def test_build_stack_fails_fast_when_sqlite_missing(tmp_path):
    # Startup must refuse a missing SQLite file instead of waiting for first chat.
    settings = replace(
        load_settings(apply_local=False),
        datasource=DataSourceConfig(
            kind="sqlite",
            sqlite_path=str(tmp_path / "missing.sqlite"),
        ),
    )
    with pytest.raises(RuntimeError, match="datasource sqlite .* unavailable"):
        build_stack(settings)


def test_verify_datasource_fails_fast_on_unreachable_postgres(monkeypatch):
    # A down Postgres (docker not running) must fail in seconds, not hang ~2min.
    pytest.importorskip("psycopg")
    # Port 1 is almost never a Postgres listener; short connect_timeout is set by
    # build_connector / verify_datasource.
    monkeypatch.setenv(
        "TEST_PG_DSN",
        "host=127.0.0.1 port=1 dbname=bird user=bird password=bird",
    )
    settings = replace(
        load_settings(apply_local=False),
        datasource=DataSourceConfig(
            kind="postgres",
            dsn_env="TEST_PG_DSN",
        ),
    )
    with pytest.raises(RuntimeError, match="datasource postgres .* unavailable"):
        build_stack(settings)


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


def test_schema_param_less_is_unchanged(client):
    # Regression guard: param-less /schema is the byte-for-byte full dump (the
    # backward-compat contract). Adding optional query params must not change it.
    full = client.get("/schema").json()
    assert len(full) == 5
    # Every table carries the heavy detail fields (this is the full view, not the summary).
    assert all("description" in t for t in full)
    assert all("sample_values" in c for t in full for c in t["columns"])


def test_schema_filter_and_pagination(client):
    all_tables = client.get("/schema").json()
    assert len(all_tables) == 5
    assert len(client.get("/schema", params={"schema": "beer_factory"}).json()) == 5
    assert client.get("/schema", params={"schema": "nope"}).json() == []
    # Hard cut: ``?db=`` is not a filter (ignored); only ``?schema=`` scopes.
    assert len(client.get("/schema", params={"db": "nope"}).json()) == 5
    # limit/offset paginate against the same order.
    page = client.get("/schema", params={"limit": 2, "offset": 1}).json()
    assert [t["id"] for t in page] == [t["id"] for t in all_tables[1:3]]
    assert all("schema" in t and "db" not in t for t in all_tables)


def test_schema_summary_shape_and_heavy_fields_absent(client):
    body = client.get("/schema/summary").json()
    assert body["total"] == 5
    assert len(body["items"]) == 5
    tables = {t["physical_name"]: t for t in body["items"]}
    assert set(tables) == {"customers", "transaction", "rootbeer", "rootbeerbrand", "rootbeerreview"}
    row = tables["customers"]
    # Lean table-level fields present; heavy ones dropped.
    assert set(row) == {
        "id", "physical_name", "schema", "row_count", "n_columns",
        "excluded", "has_suspect", "provenance_status", "columns",
    }
    assert "description" not in row
    assert row["n_columns"] == len(row["columns"])
    assert row["has_suspect"] is True  # customers.ZipCode is suspect
    # Lean column rows carry only the search/preview fields (no sample_values/evidence).
    col = row["columns"][0]
    assert set(col) == {"physical_name", "physical_type", "role", "reliability", "excluded"}
    assert "sample_values" not in col
    assert "evidence" not in col
    # transaction has no suspect column but does have the excluded PII column.
    txn = tables["transaction"]
    assert txn["has_suspect"] is False
    assert any(c["excluded"] for c in txn["columns"])


def test_schema_summary_filter(client):
    assert client.get("/schema/summary", params={"schema": "beer_factory"}).json()["total"] == 5
    empty = client.get("/schema/summary", params={"schema": "nope"}).json()
    assert empty["total"] == 0
    assert empty["items"] == []
    # Hard cut: ``?db=`` does not filter.
    assert client.get("/schema/summary", params={"db": "nope"}).json()["total"] == 5


def test_schema_summary_pagination_total_is_before_paging(client):
    body = client.get("/schema/summary", params={"limit": 2, "offset": 1}).json()
    assert body["total"] == 5  # count BEFORE pagination, not the page size
    assert len(body["items"]) == 2
    full = client.get("/schema/summary").json()["items"]
    assert [t["id"] for t in body["items"]] == [t["id"] for t in full[1:3]]


def test_schema_lists_are_id_sorted(client):
    # Stable id order is what makes offset/limit pagination consistent across
    # workers/restarts (corpus load order is otherwise filesystem-dependent for a
    # multi-namespace corpus).
    summary_ids = [t["id"] for t in client.get("/schema/summary").json()["items"]]
    assert summary_ids == sorted(summary_ids)
    schema_ids = [t["id"] for t in client.get("/schema").json()]
    assert schema_ids == sorted(schema_ids)


def test_schema_summary_pagination_bounds(client):
    # offset beyond total -> empty page; total unchanged.
    beyond = client.get("/schema/summary", params={"offset": 999}).json()
    assert beyond["total"] == 5 and beyond["items"] == []
    # limit=0 -> empty page (0 means "none", not "all"); total unchanged.
    zero = client.get("/schema/summary", params={"limit": 0}).json()
    assert zero["total"] == 5 and zero["items"] == []
    # A limit larger than the corpus returns everything.
    assert len(client.get("/schema/summary", params={"limit": 10000}).json()["items"]) == 5
    # Negative limit/offset are rejected by the query validators (ge=0).
    assert client.get("/schema/summary", params={"limit": -1}).status_code == 422
    assert client.get("/schema/summary", params={"offset": -1}).status_code == 422


def test_schema_by_id_found_and_missing(client):
    tid = "tbl_beer_factory_customers"
    found = client.get(f"/schema/{tid}")
    assert found.status_code == 200
    body = found.json()
    assert body["id"] == tid
    assert body["physical_name"] == "customers"
    # This is the full detail view: heavy fields are present.
    assert "sample_values" in body["columns"][0]
    # Unknown id -> 404.
    missing = client.get("/schema/tbl_does_not_exist")
    assert missing.status_code == 404


def test_capabilities_scope_flags_reflect_the_stack():
    off = TestClient(create_app(replace(build_stack(), can_scope=False, can_search=True)))
    body = off.get("/capabilities").json()
    assert body["can_scope"] is False
    assert body["can_search"] is True


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
    assert {r["asset_type"] for r in everything} >= {
        "join", "metric", "term", "note", "negative_example"
    }
    metrics = client.get("/corpus/assets", params={"type": "metric"}).json()
    assert metrics and all(r["asset_type"] == "metric" for r in metrics)
    notes = client.get("/corpus/assets", params={"type": "note"}).json()
    assert notes and all(r["asset_type"] == "note" for r in notes)
    assert client.get("/corpus/assets", params={"type": "rule"}).status_code == 422


def test_skills_route_is_removed(client):
    assert client.get("/skills").status_code == 404


# --------------------------------------------------------------------------- #
# chat (governed serve flow) — needs the committed DB
# --------------------------------------------------------------------------- #


@requires_live_serve
@pytest.mark.skipif(not BIRD_DB.exists(), reason="vendored beer_factory.sqlite not present")
def test_chat_governed_answer_carries_result(client):
    r = client.post("/chat", json={"question": "What is the total revenue?", "session_id": "s"})
    assert r.status_code == 200
    body = r.json()
    assert body["tier"] == "governed"
    assert body["safety_clearance"] is True
    assert body["semantic_assurance"] == "grounded"
    assert "SUM(PurchasePrice)" in body["sql"]
    # Offline profile: no narrator -> compact render; rows are still carried.
    assert "total_revenue" in body["text"]
    assert body["result"]["columns"] == ["total_revenue"]
    assert body["result"]["rows"][0][0] == pytest.approx(18496.0)
    assert body["provenance"]["metric_id"] == "metric_revenue"


@requires_live_serve
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


@requires_live_serve
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
    # Inject a stack with a (dummy) model so the request clears the live-model
    # gate and reaches the DB open, then point at a nonexistent DB. The model is
    # never invoked (connector open fails first), so any non-None sentinel works.
    stack = replace(
        build_stack(), sqlite_path=Path("does/not/exist.sqlite"), chat_model=object()
    )
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
    stack = replace(build_stack(), corpus_root=tmp_path, **flags)
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


def test_cors_allows_configured_origin():
    settings = replace(
        load_settings(apply_local=False),
        cors_origins=("https://app.example.com",),
    )
    client = TestClient(create_app(build_stack(settings)))
    r = client.get("/capabilities", headers={"Origin": "https://app.example.com"})
    assert r.headers.get("access-control-allow-origin") == "https://app.example.com"


# --------------------------------------------------------------------------- #
# column -> related semantic items (handoff §14)
# --------------------------------------------------------------------------- #


def test_column_related_resolves_fk_and_joins(client):
    r = client.get("/columns/col_beer_factory_customers_CustomerID/related")
    assert r.status_code == 200
    body = r.json()
    assert body["column"]["id"] == "col_beer_factory_customers_CustomerID"
    assert body["column"]["schema"] == "beer_factory"  # namespace serializes as `schema`
    assert body["column"]["table_physical_name"] == "customers"
    assert body["fk_out"] is None
    assert {r_["column_id"] for r_ in body["fk_in"]} == {
        "col_beer_factory_transaction_CustomerID",
        "col_beer_factory_rootbeerreview_CustomerID",
    }
    assert "join_transaction_customers" in {j["id"] for j in body["joins"]}
    assert body["meta"]["column_resolvable"] is True
    # empty relations are [], never null
    assert body["metrics"] == []
    assert isinstance(body["terms"], list)


def test_column_related_unknown_is_404(client):
    assert client.get("/columns/col_does_not_exist/related").status_code == 404
