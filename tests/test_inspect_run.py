"""``scripts/inspect_run.py`` — the offline experiment inspector's server.

These tests pin the properties that make the inspector safe and honest, not a feature
checklist:

* the API is **injection-proof** — a query string full of SQL metacharacters is bound,
  never interpolated, and the ``turns`` table survives it;
* ``sort`` is an **allow-list** — an unknown column falls back rather than reaching the
  query as code;
* the database is opened **read-only** — the inspector cannot mutate the run it reads;
* the static file server **cannot be walked out of** ``scripts/inspector/``;
* **degraded runs still open** — a run with no ``stage_events.jsonl`` and no
  ``questions.jsonl`` reports its missing capabilities instead of 500ing, because the
  runs most worth inspecting are the ones that broke.

The fixtures build a real run directory and run it through the actual loader, so the
tests exercise the same schema the tool serves in production, not a hand-rolled one.
"""

from __future__ import annotations

import importlib.util
import json
import threading
import urllib.request
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def inspect_mod():
    return _load("inspect_run", "inspect_run.py")


@pytest.fixture(scope="module")
def loader_mod():
    return _load("load_run_db", "load_run_db.py")


# --------------------------------------------------------------------------- #
# Run-directory builders
# --------------------------------------------------------------------------- #


def _write_jsonl(path: Path, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8"
    )


def _gen_row(qid, arm, *, correct=True, outcome="answered", **overrides):
    row = {
        "question_id": qid,
        "db_id": "formula_1",
        "arm": arm,
        "split": "test",
        "generated_sql": f"SELECT {qid}",
        "correct": correct,
        "outcome": outcome,
        "failed_stage": None,
        "latency_sec": 1.5,
        "cost_est_usd": 0.01,
        "pick_hit": True,
        "routed_hit": True,
        "turn_id": f"eval-{arm}-w0:{qid}",
        "token_sum": {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12},
        "n_tool_calls": {"run_query": 2, "inspect_schema": 1},
        "governance_ledger": [
            {"action": "run_query", "verdict": "pass", "sql": f"SELECT {qid}", "row_count": 1},
        ],
    }
    row.update(overrides)
    return row


def _event(qid, arm, stage, *, turn_id, status="ok", ms=1.0, detail=None):
    return {
        "question_id": qid, "arm": arm, "db_id": "formula_1",
        "run_id": "r1", "turn_id": turn_id, "stage": stage, "status": status,
        "ms": ms, "detail": detail or {},
    }


def _build_run(tmp_path: Path, *, with_events=True, with_questions=True) -> Path:
    run = tmp_path / "run"
    run.mkdir()
    (run / "manifest.json").write_text(
        json.dumps({
            "mode": "datalake", "model": "test-model", "split": "test",
            "arms": ["baseline", "curated"], "completed_at_utc": "20260101T000000Z",
        }),
        encoding="utf-8",
    )
    _write_jsonl(run / "generations.baseline.jsonl", [
        _gen_row("1", "baseline", correct=False),
        _gen_row("2", "baseline", correct=True),
        _gen_row("3", "baseline", correct=False, outcome="crashed"),
    ])
    _write_jsonl(run / "generations.curated.jsonl", [
        _gen_row("1", "curated", correct=True),
        _gen_row("2", "curated", correct=True),
        _gen_row("3", "curated", correct=None, outcome="refused"),
    ])
    if with_questions:
        _write_jsonl(run / "questions.jsonl", [
            {"question_id": "1", "db_id": "formula_1", "question": "how many?",
             "evidence": "count", "gold_sql": "SELECT COUNT(*)"},
            {"question_id": "2", "db_id": "formula_1", "question": "who won?",
             "gold_sql": "SELECT winner"},
        ])
    if with_events:
        _write_jsonl(run / "stage_events.jsonl", [
            _event("1", "baseline", "route", turn_id="eval-baseline-w0:1"),
            _event("1", "baseline", "assemble", turn_id="eval-baseline-w0:1", ms=50.0),
            _event("1", "baseline", "agent_core", turn_id="eval-baseline-w0:1", ms=200.0),
        ])
    else:
        (run / "stage_events.jsonl").write_text("", encoding="utf-8")
    return run


@pytest.fixture
def db(tmp_path, inspect_mod, loader_mod):
    run = _build_run(tmp_path)
    db_path = tmp_path / "run.sqlite"
    conn = loader_mod.connect(db_path)
    try:
        loader_mod.load_run(conn, run, index={})
    finally:
        conn.close()
    return inspect_mod.Db(db_path)


# --------------------------------------------------------------------------- #
# API shape
# --------------------------------------------------------------------------- #


def test_runs_lists_the_run_with_db_path(db, inspect_mod):
    with db.session() as conn:
        out = inspect_mod.api_runs(conn, {})
    assert len(out["runs"]) == 1
    assert out["db_path"].endswith("run.sqlite")
    assert out["runs"][0]["arms_loaded"] == ["baseline", "curated"]


def test_overview_separates_crash_from_wrong(db, inspect_mod):
    run_dir = _run_dir(db, inspect_mod)
    with db.session() as conn:
        out = inspect_mod.api_overview(conn, {"run": [run_dir]})
    per = {a["arm"]: a for a in out["per_arm"]}
    # baseline: q1 wrong, q2 right, q3 crashed. EX is over graded non-crashed = 1/2.
    assert per["baseline"]["n_turns"] == 3
    assert per["baseline"]["n_crashed"] == 1
    assert per["baseline"]["n_graded"] == 2
    assert per["baseline"]["ex"] == 0.5
    assert per["baseline"]["crash_rate"] == round(1 / 3, 4)
    # facets and capabilities are present and truthful
    assert set(out["facets"]["arms"]) == {"baseline", "curated"}
    assert out["capabilities"]["stage_events"] is True
    assert out["capabilities"]["gold_sql"] is True


def test_turns_filter_sort_paginate(db, inspect_mod):
    run_dir = _run_dir(db, inspect_mod)
    with db.session() as conn:
        fails = inspect_mod.api_turns(conn, {"run": [run_dir], "verdict": ["fail"]})
        assert fails["total"] == 2  # baseline q1, baseline q3(crashed is correct=0)
        one_arm = inspect_mod.api_turns(conn, {"run": [run_dir], "arm": ["curated"]})
        assert one_arm["total"] == 3
        page = inspect_mod.api_turns(conn, {"run": [run_dir], "limit": ["2"], "offset": ["0"]})
        assert len(page["rows"]) == 2 and page["total"] == 6


def test_sort_is_allowlisted(db, inspect_mod):
    run_dir = _run_dir(db, inspect_mod)
    with db.session() as conn:
        out = inspect_mod.api_turns(conn, {"run": [run_dir], "sort": ["1; DROP TABLE turns"]})
    # bogus sort silently falls back to question_id; nothing raised, table intact.
    assert out["sort"] == "question_id"
    with db.session() as conn:
        assert conn.execute("SELECT COUNT(*) FROM turns").fetchone()[0] == 6


def test_search_is_injection_proof(db, inspect_mod):
    run_dir = _run_dir(db, inspect_mod)
    with db.session() as conn:
        out = inspect_mod.api_turns(conn, {"run": [run_dir], "q": ["x'; DROP TABLE turns;--"]})
        assert out["total"] == 0
        # the table is still there and still full
        assert conn.execute("SELECT COUNT(*) FROM turns").fetchone()[0] == 6


def test_turn_detail_has_trajectory_events_and_siblings(db, inspect_mod):
    run_dir = _run_dir(db, inspect_mod)
    with db.session() as conn:
        out = inspect_mod.api_turn(
            conn, {"run": [run_dir], "arm": ["baseline"], "question_id": ["1"]}
        )
    assert out["detail"]["question"] == "how many?"
    assert out["detail"]["gold_sql"] == "SELECT COUNT(*)"
    assert out["extras"]["governance_ledger"][0]["action"] == "run_query"
    assert [e["stage"] for e in out["events"]] == ["route", "assemble", "agent_core"]
    assert {s["arm"] for s in out["siblings"]} == {"baseline", "curated"}


def test_missing_required_params_are_400(db, inspect_mod):
    with db.session() as conn:
        assert inspect_mod.api_overview(conn, {})[1] == 400
        assert inspect_mod.api_turns(conn, {})[1] == 400
        assert inspect_mod.api_turn(conn, {"run": ["x"]})[1] == 400


# --------------------------------------------------------------------------- #
# Degradation
# --------------------------------------------------------------------------- #


def test_run_without_events_or_gold_still_opens(tmp_path, inspect_mod, loader_mod):
    run = _build_run(tmp_path, with_events=False, with_questions=False)
    db_path = tmp_path / "run.sqlite"
    conn = loader_mod.connect(db_path)
    try:
        loader_mod.load_run(conn, run, index={})
    finally:
        conn.close()
    db = inspect_mod.Db(db_path)
    run_dir = _run_dir(db, inspect_mod)
    with db.session() as conn:
        ov = inspect_mod.api_overview(conn, {"run": [run_dir]})
        assert ov["capabilities"]["stage_events"] is False
        assert ov["capabilities"]["gold_sql"] is False
        detail = inspect_mod.api_turn(
            conn, {"run": [run_dir], "arm": ["baseline"], "question_id": ["1"]}
        )
        assert detail["events"] == []
        assert detail["detail"]["gold_sql"] is None


def test_not_a_run_db_is_refused(tmp_path, inspect_mod):
    empty = tmp_path / "empty.sqlite"
    import sqlite3
    sqlite3.connect(empty).close()
    with pytest.raises(SystemExit):
        inspect_mod.Db(empty)


# --------------------------------------------------------------------------- #
# HTTP layer + read-only + traversal
# --------------------------------------------------------------------------- #


@pytest.fixture
def server(db, inspect_mod):
    from http.server import ThreadingHTTPServer

    handler = inspect_mod.make_handler(db)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    port = httpd.server_address[1]
    yield f"http://127.0.0.1:{port}"
    httpd.shutdown()
    httpd.server_close()


def _get(url):
    with urllib.request.urlopen(url) as resp:  # noqa: S310 (localhost test server)
        return resp.status, resp.read()


def test_http_serves_index_and_assets(server):
    status, body = _get(server + "/")
    assert status == 200 and b"Experiment inspector" in body
    status, body = _get(server + "/static/app.js")
    assert status == 200 and b"innerHTML is not allowed" in body  # the XSS guard is present


def test_http_api_runs(server):
    status, body = _get(server + "/api/runs")
    assert status == 200
    assert json.loads(body)["runs"]


def test_http_traversal_blocked(server):
    from urllib.error import HTTPError

    with pytest.raises(HTTPError) as exc:
        _get(server + "/static/..%2f..%2fload_run_db.py")
    assert exc.value.code in (403, 404)


def test_http_unknown_path_404(server):
    from urllib.error import HTTPError

    with pytest.raises(HTTPError) as exc:
        _get(server + "/does-not-exist")
    assert exc.value.code == 404


def test_favicon_is_204_not_404(server):
    # A browser always asks for /favicon.ico; a 404 is console/network noise. We answer
    # 204 so the network log stays clean. (Regression: it used to 404.)
    status, _ = _get(server + "/favicon.ico")
    assert status == 204


# --------------------------------------------------------------------------- #
# Frontend regression guards
#
# The layout collapse and the dead deep-link were browser-only defects (a missing CSS
# rule and a missing event listener). There is no headless browser in CI, so these pin
# the specific fixes at the asset level — enough to catch a re-introduction.
# --------------------------------------------------------------------------- #

STATIC = SCRIPTS / "inspector"


def test_css_has_filter_bar_and_bounded_workspace():
    css = (STATIC / "styles.css").read_text(encoding="utf-8")
    # The filters section must have an explicit layout, or its fields stack full-width
    # and consume the viewport (the reported collapse).
    assert ".filters {" in css and "display: flex" in css.split(".filters {", 1)[1][:200]
    # The workspace grid must define a row track, or the panes grow to content height
    # and the results become an unreachable, unscrollable overflow.
    assert "grid-template-rows: minmax(0, 1fr)" in css


def test_js_wires_hashchange_deep_link():
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    # A pasted/edited deep link (or back/forward) must select the turn without a reload.
    assert "applyDeepLink" in js
    assert 'addEventListener("hashchange"' in js


def test_database_is_opened_read_only(db):
    with db.session() as conn:
        with pytest.raises(Exception):
            conn.execute("DELETE FROM turns")


# --------------------------------------------------------------------------- #
# Build-from-run-dir
# --------------------------------------------------------------------------- #


def test_ensure_db_builds_from_run_dir(tmp_path, inspect_mod):
    run = _build_run(tmp_path)
    db_path = inspect_mod.ensure_db_for_run_dir(run, rebuild=False)
    assert db_path == run / "run.sqlite"
    assert db_path.is_file()
    # second call reuses it (no rebuild), still valid
    again = inspect_mod.ensure_db_for_run_dir(run, rebuild=False)
    assert again == db_path


def test_ensure_db_refuses_non_run_dir(tmp_path, inspect_mod):
    with pytest.raises(SystemExit):
        inspect_mod.ensure_db_for_run_dir(tmp_path, rebuild=False)


# --------------------------------------------------------------------------- #
# helper
# --------------------------------------------------------------------------- #


def _run_dir(db, inspect_mod) -> str:
    with db.session() as conn:
        return inspect_mod.api_runs(conn, {})["runs"][0]["run_dir"]
