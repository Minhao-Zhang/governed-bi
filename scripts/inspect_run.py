"""Offline experiment inspector for eval runs — a read-only web view over ``run.sqlite``.

Why this exists, and why it is a *server* and not a generated HTML file.
A finished ladder is 1351 questions x 3-4 arms of trajectories. The question an
analyst actually asks is never "show me everything" — it is "open the curated arm's
failures on ``formula_1`` and read what the agent did." A pre-rendered single file
either inlines all of that (megabytes, unsearchable) or drops the trajectory. So this
serves the SQLite the loader already produces (:mod:`scripts.load_run_db`) behind a
tiny stdlib HTTP API, and a static frontend queries it: fast navigation, real search,
per-question detail on demand. No build step, no third-party dependency.

It is an **experiment inspector, not a chat UI**. Every screen is oriented around one
axis of the run — run / arm / db / question — and terminates in one turn's full record:
the question, gold SQL, generated SQL, the verdict, and the ordered trajectory (the
governance ledger's tool calls plus the rail stage timeline), with timing / token /
cost fields where the run recorded them and an honest "not recorded" where it did not.

Start it::

    uv run python scripts/inspect_run.py --run-dir runs/datalake/luna-max/20260801T-ladder
    uv run python scripts/inspect_run.py --sqlite runs/datalake/luna-max/20260801T-ladder/run.sqlite

``--run-dir`` builds ``<run-dir>/run.sqlite`` via the loader if it is missing (or
``--rebuild`` to force it) and then serves it. ``--sqlite`` serves an existing database
as-is — including a shared ``runs.db`` built with ``load_run_db --discover``, in which
case the run picker lists every run it holds.

Safety. The database is opened **read-only** (``mode=ro``); the server binds
**localhost only** by default; the frontend never sends SQL — every query in this file
is parameterised and every sort/column name is checked against an allow-list, so a
crafted query string cannot reach the database as code. Result content (SQL, questions,
JSON) is rendered by the frontend with ``textContent``, never ``innerHTML``, so a
``<script>`` that happens to live in a BIRD question is shown, not run.
"""

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import json
import sqlite3
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
STATIC_DIR = HERE / "inspector"

# --------------------------------------------------------------------------- #
# Query surface — the only columns the client may sort by or select into a list.
# Anything outside these tuples is refused, so a query string never names a column
# the server then interpolates. (Values are still always bound, never interpolated.)
# --------------------------------------------------------------------------- #

#: Columns returned for each row of the turn list, in display order. All live on
#: ``turns``; long text (SQL, question) is deliberately excluded and fetched only in
#: the detail view.
LIST_COLUMNS: tuple[str, ...] = (
    "arm", "question_id", "db_id", "correct", "outcome", "failed_stage",
    "pick_hit", "routed_hit", "n_tool_calls_total",
    "latency_sec", "total_tokens", "cost_est_usd",
)

#: Columns the client is allowed to sort by. Superset of LIST_COLUMNS is fine as long
#: as every entry is a real, indexed-or-cheap column on ``turns``.
SORTABLE: frozenset[str] = frozenset(
    LIST_COLUMNS + ("gold_nrows", "pred_nrows", "attempts", "input_tokens", "output_tokens")
)

#: Free-text search matches any of these with a bound LIKE. question / SQL are text
#: columns; question_id / db_id are short identifiers.
SEARCH_COLUMNS: tuple[str, ...] = (
    "question_id", "db_id", "question", "generated_sql", "gold_sql", "error",
)

#: Scalar columns surfaced individually in the detail view (everything else remains
#: reachable through the raw ``row_json`` panel).
DETAIL_COLUMNS: tuple[str, ...] = (
    "run_dir", "arm", "question_id", "db_id", "split", "run_id", "turn_id",
    "correct", "correct_strict", "outcome", "failed_stage", "refused_by",
    "error", "error_type", "failed_layer", "tier", "semantic_assurance",
    "safety_clearance", "graded_delivery",
    "question", "evidence", "gold_sql", "generated_sql",
    "pred_nrows", "pred_ncols", "gold_nrows", "nrows_match",
    "difficulty", "gold_twin_in_train", "gold_frozen", "gold_order_sensitive",
    "gold_schema_rank",
    "routed_hit", "pick_hit", "schema_pick", "schema_pick_fallback",
    "total_schemas", "routing_bypassed", "decoy_touch",
    "context_chars", "n_notes_injected", "n_few_shots_injected", "attempts",
    "n_tool_calls_total", "ledger_len",
    "latency_sec", "cost_est_usd", "input_tokens", "output_tokens", "total_tokens",
    "oracle_rung", "oracle_applied", "prompt_set_hash",
)

#: Fields lifted out of ``row_json`` for the detail view — the trajectory and the
#: small context lists that are not promoted to columns by the loader.
ROW_JSON_EXTRAS: tuple[str, ...] = (
    "governance_ledger", "n_tool_calls", "tables_used", "licensed_tables",
    "retrieved_tables", "routed_schemas", "shortlisted_schemas",
    "injected_note_ids", "by_guardrail_layer", "token_usage",
)

MAX_LIMIT = 500


# --------------------------------------------------------------------------- #
# SQLite access — a fresh read-only connection per request (thread-safe, cheap).
# --------------------------------------------------------------------------- #


class Db:
    """A read-only handle to one analysis database.

    Each request opens its own connection so the threading HTTP server never shares a
    cursor across threads. ``mode=ro`` means the process cannot write the file even if
    a bug tried to: this is an inspector, and the run it inspects is evidence.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._uri = f"file:{path.resolve().as_posix()}?mode=ro"
        # Fail fast with a clear message rather than 500ing on the first request.
        self._check()

    def _check(self) -> None:
        with self.session() as conn:
            missing = [
                name
                for name in ("runs", "turns", "events")
                if not conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
                ).fetchone()
            ]
        if missing:
            raise SystemExit(
                f"{self.path} is not a run database (missing table(s): {', '.join(missing)}). "
                "Build one with scripts/load_run_db.py."
            )

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._uri, uri=True)
        conn.row_factory = sqlite3.Row
        return conn

    @contextlib.contextmanager
    def session(self):
        """A connection that is *closed* on exit.

        ``with connection:`` only commits/rolls back — it never closes — so a request
        loop that relied on it would leak a handle per call. Read-only work never needs
        the commit; it needs the close.
        """
        conn = self.connect()
        try:
            yield conn
        finally:
            conn.close()


def _rows(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


# --------------------------------------------------------------------------- #
# API — every function takes a live connection and a parsed query dict, returns JSON.
# --------------------------------------------------------------------------- #


def api_runs(conn: sqlite3.Connection, _q: dict[str, list[str]]) -> dict[str, Any]:
    """Every run in the database, newest first, with just enough to pick one."""
    runs = _rows(
        conn,
        """
        SELECT run_dir, model, split, mode, n_questions, n_turns, n_events,
               arms_loaded, arms_missing, quotable, completed_at_utc, created_at_utc,
               notes, seq_source
        FROM runs
        ORDER BY COALESCE(created_at_utc, '') DESC, run_dir DESC
        """,
    )
    for r in runs:
        r["arms_loaded"] = _load_json(r.get("arms_loaded"), [])
        r["arms_missing"] = _load_json(r.get("arms_missing"), [])
        r["notes"] = _load_json(r.get("notes"), [])
    db_row = conn.execute("PRAGMA database_list").fetchone()
    db_path = db_row["file"] if db_row and db_row["file"] else None
    return {"runs": runs, "db_path": db_path}


def api_overview(conn: sqlite3.Connection, q: dict[str, list[str]]) -> dict[str, Any]:
    """Per-arm headline metrics for one run, plus the facets the filters need.

    Crashes are separated from the denominator deliberately — pooling a crash (our
    bug) with a wrong answer (the model's) is how a set of numbers had to be retired
    once already. ``ex`` is over graded, non-crashed turns; ``crash_rate`` is reported
    beside it rather than hidden inside it.
    """
    run = _one(q, "run")
    if run is None:
        return {"error": "run is required"}, 400  # type: ignore[return-value]

    run_row = conn.execute(
        "SELECT * FROM runs WHERE run_dir = ?", (run,)
    ).fetchone()
    if run_row is None:
        return {"error": f"no such run: {run}"}, 404  # type: ignore[return-value]
    run_dict = dict(run_row)
    for key in ("arms_loaded", "arms_missing", "notes", "not_quotable_because"):
        run_dict[key] = _load_json(run_dict.get(key), [])
    run_dict["headline"] = _load_json(run_dict.get("headline_json"), None)

    per_arm = _rows(
        conn,
        """
        SELECT arm,
               COUNT(*)                                                        AS n_turns,
               SUM(COALESCE(outcome,'') = 'crashed')                           AS n_crashed,
               SUM(COALESCE(outcome,'') <> 'crashed' AND correct IS NOT NULL)  AS n_graded,
               SUM(COALESCE(outcome,'') <> 'crashed' AND correct = 1)          AS n_correct,
               SUM(pick_hit = 1)                                               AS n_pick_hit,
               SUM(routed_hit = 1)                                             AS n_routed_hit,
               ROUND(AVG(latency_sec), 2)                                      AS avg_latency_sec,
               ROUND(SUM(COALESCE(cost_est_usd, 0)), 4)                        AS total_cost_usd,
               SUM(COALESCE(total_tokens, 0))                                  AS total_tokens
        FROM turns WHERE run_dir = ?
        GROUP BY arm ORDER BY arm
        """,
        (run,),
    )
    for a in per_arm:
        graded = a.get("n_graded") or 0
        a["ex"] = round(a["n_correct"] / graded, 4) if graded else None
        a["crash_rate"] = (
            round((a.get("n_crashed") or 0) / a["n_turns"], 4) if a["n_turns"] else None
        )

    arms = [r["arm"] for r in _rows(
        conn, "SELECT DISTINCT arm FROM turns WHERE run_dir=? ORDER BY arm", (run,)
    )]
    dbs = [r["db_id"] for r in _rows(
        conn,
        "SELECT DISTINCT db_id FROM turns WHERE run_dir=? AND db_id IS NOT NULL ORDER BY db_id",
        (run,),
    )]
    outcomes = [r["outcome"] for r in _rows(
        conn,
        "SELECT DISTINCT outcome FROM turns WHERE run_dir=? AND outcome IS NOT NULL ORDER BY outcome",
        (run,),
    )]

    # Honest degradation flags: what this run can and cannot show.
    has_events = bool(conn.execute(
        "SELECT 1 FROM events WHERE run_dir=? LIMIT 1", (run,)
    ).fetchone())
    has_gold = bool(conn.execute(
        "SELECT 1 FROM turns WHERE run_dir=? AND gold_sql IS NOT NULL LIMIT 1", (run,)
    ).fetchone())

    return {
        "run": run_dict,
        "per_arm": per_arm,
        "facets": {"arms": arms, "dbs": dbs, "outcomes": outcomes},
        "capabilities": {"stage_events": has_events, "gold_sql": has_gold},
    }


def api_turns(conn: sqlite3.Connection, q: dict[str, list[str]]) -> dict[str, Any]:
    """A filtered, sorted, paginated page of turns for the list view."""
    run = _one(q, "run")
    if run is None:
        return {"error": "run is required"}, 400  # type: ignore[return-value]

    where = ["run_dir = ?"]
    params: list[Any] = [run]

    for field, col in (("arm", "arm"), ("db", "db_id"), ("outcome", "outcome")):
        val = _one(q, field)
        if val:
            where.append(f"{col} = ?")
            params.append(val)

    verdict = _one(q, "verdict")
    if verdict == "pass":
        where.append("correct = 1")
    elif verdict == "fail":
        where.append("correct = 0")
    elif verdict == "ungraded":
        where.append("correct IS NULL")

    search = _one(q, "q")
    if search:
        needle = f"%{search}%"
        clause = " OR ".join(f"{c} LIKE ?" for c in SEARCH_COLUMNS)
        where.append(f"({clause})")
        params.extend([needle] * len(SEARCH_COLUMNS))

    where_sql = " AND ".join(where)

    sort = _one(q, "sort") or "question_id"
    if sort not in SORTABLE:
        sort = "question_id"
    direction = "DESC" if (_one(q, "dir") or "asc").lower() == "desc" else "ASC"

    limit = _clamp_int(_one(q, "limit"), default=100, lo=1, hi=MAX_LIMIT)
    offset = _clamp_int(_one(q, "offset"), default=0, lo=0, hi=10_000_000)

    total = conn.execute(
        f"SELECT COUNT(*) FROM turns WHERE {where_sql}", tuple(params)
    ).fetchone()[0]

    # sort/direction come from allow-lists above; every value is bound.
    cols = ", ".join(LIST_COLUMNS)
    rows = _rows(
        conn,
        f"SELECT {cols} FROM turns WHERE {where_sql} "
        f"ORDER BY {sort} {direction}, question_id ASC LIMIT ? OFFSET ?",
        tuple(params) + (limit, offset),
    )
    return {"total": total, "limit": limit, "offset": offset, "sort": sort,
            "dir": direction.lower(), "rows": rows}


def api_turn(conn: sqlite3.Connection, q: dict[str, list[str]]) -> dict[str, Any]:
    """One turn's full record: scalar fields, trajectory, and rail timeline."""
    run = _one(q, "run")
    question_id = _one(q, "question_id")
    arm = _one(q, "arm")
    if not (run and question_id and arm):
        return {"error": "run, arm and question_id are required"}, 400  # type: ignore[return-value]

    row = conn.execute(
        "SELECT * FROM turns WHERE run_dir=? AND arm=? AND question_id=?",
        (run, arm, question_id),
    ).fetchone()
    if row is None:
        return {"error": "no such turn"}, 404  # type: ignore[return-value]
    row = dict(row)

    detail = {col: row.get(col) for col in DETAIL_COLUMNS}

    raw = _load_json(row.get("row_json"), {})
    extras = {key: raw.get(key) for key in ROW_JSON_EXTRAS if key in raw}

    turn_id = row.get("turn_id")
    if turn_id is None:
        events = _rows(
            conn,
            "SELECT seq, seq_derived, stage, status, ms, detail_json FROM events "
            "WHERE run_dir=? AND arm=? AND question_id=? AND turn_id IS NULL "
            "ORDER BY file_row",
            (run, arm, question_id),
        )
    else:
        events = _rows(
            conn,
            "SELECT seq, seq_derived, stage, status, ms, detail_json FROM events "
            "WHERE run_dir=? AND arm=? AND question_id=? AND turn_id=? "
            "ORDER BY file_row",
            (run, arm, question_id, turn_id),
        )
    for e in events:
        e["detail"] = _load_json(e.pop("detail_json"), None)

    # Other arms' verdicts on the same question — the cross-arm jump the UI offers.
    siblings = _rows(
        conn,
        "SELECT arm, correct, outcome FROM turns WHERE run_dir=? AND question_id=? ORDER BY arm",
        (run, question_id),
    )

    return {
        "detail": detail,
        "extras": extras,
        "events": events,
        "siblings": siblings,
        "raw": raw,
        "has_events": bool(events),
    }


ROUTES = {
    "/api/runs": api_runs,
    "/api/overview": api_overview,
    "/api/turns": api_turns,
    "/api/turn": api_turn,
}


# --------------------------------------------------------------------------- #
# Small parse helpers
# --------------------------------------------------------------------------- #


def _one(q: dict[str, list[str]], key: str) -> str | None:
    vals = q.get(key)
    if not vals:
        return None
    val = vals[0].strip()
    return val or None


def _clamp_int(value: str | None, *, default: int, lo: int, hi: int) -> int:
    try:
        n = int(value) if value is not None else default
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


def _load_json(text: Any, fallback: Any) -> Any:
    if text is None:
        return fallback
    if not isinstance(text, (str, bytes)):
        return text
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return fallback


# --------------------------------------------------------------------------- #
# HTTP server
# --------------------------------------------------------------------------- #

_STATIC_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
}


def make_handler(db: Db) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "governed-bi-inspector"

        def log_message(self, *args: Any) -> None:  # quiet by default
            pass

        def do_GET(self) -> None:  # noqa: N802 (stdlib naming)
            parsed = urlparse(self.path)
            path = parsed.path
            if path in ROUTES:
                self._serve_api(ROUTES[path], parse_qs(parsed.query))
            elif path == "/" or path == "":
                self._serve_static("index.html")
            elif path == "/favicon.ico":
                self.send_response(204)  # no icon; keep it out of the network log
                self.end_headers()
            elif path.startswith("/static/"):
                self._serve_static(path[len("/static/"):])
            else:
                self._send_json({"error": "not found"}, 404)

        def _serve_api(self, fn: Any, query: dict[str, list[str]]) -> None:
            try:
                with db.session() as conn:
                    result = fn(conn, query)
            except Exception as exc:  # a broken run must not take the server down
                self._send_json({"error": f"{type(exc).__name__}: {exc}"}, 500)
                return
            status = 200
            if isinstance(result, tuple):
                result, status = result
            self._send_json(result, status)

        def _serve_static(self, rel: str) -> None:
            # Contain to STATIC_DIR: no traversal out of the asset directory.
            target = (STATIC_DIR / rel).resolve()
            try:
                target.relative_to(STATIC_DIR)
            except ValueError:
                self._send_json({"error": "forbidden"}, 403)
                return
            if not target.is_file():
                self._send_json({"error": "not found"}, 404)
                return
            body = target.read_bytes()
            ctype = _STATIC_TYPES.get(target.suffix, "application/octet-stream")
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(body)

        def _send_json(self, payload: Any, status: int = 200) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler


# --------------------------------------------------------------------------- #
# Building a run.sqlite from a run directory (delegates to the loader)
# --------------------------------------------------------------------------- #


def _import_loader() -> Any:
    spec = importlib.util.spec_from_file_location("load_run_db", HERE / "load_run_db.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def ensure_db_for_run_dir(run_dir: Path, *, rebuild: bool) -> Path:
    """Return the ``run.sqlite`` for *run_dir*, building it via the loader if needed."""
    run_dir = run_dir.resolve()
    if not (run_dir / "manifest.json").is_file():
        raise SystemExit(f"{run_dir} has no manifest.json — not a run directory.")
    db_path = run_dir / "run.sqlite"
    if db_path.exists() and not rebuild:
        return db_path
    loader = _import_loader()
    conn = loader.connect(db_path)
    try:
        index = loader.load_index(loader.find_index_file(run_dir))
        report = loader.load_run(conn, run_dir, index=index)
        print(report.summary_line(), file=sys.stderr)
        for note in report.notes:
            print(f"        note: {note}", file=sys.stderr)
    finally:
        conn.close()
    return db_path


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Offline experiment inspector over an eval run's run.sqlite.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--sqlite", type=Path, help="an existing run.sqlite / runs.db to serve")
    src.add_argument("--run-dir", type=Path, help="a run directory; builds run.sqlite if absent")
    parser.add_argument("--rebuild", action="store_true", help="rebuild run.sqlite even if present (with --run-dir)")
    parser.add_argument("--host", default="127.0.0.1", help="bind address (default localhost)")
    parser.add_argument("--port", type=int, default=8765, help="port (default 8765; 0 picks a free one)")
    parser.add_argument("--no-browser", action="store_true", help="do not open a browser")
    args = parser.parse_args(argv)

    if args.sqlite is not None:
        db_path = args.sqlite.resolve()
        if not db_path.is_file():
            parser.error(f"{db_path} does not exist")
    else:
        db_path = ensure_db_for_run_dir(args.run_dir, rebuild=args.rebuild)

    db = Db(db_path)
    handler = make_handler(db)
    httpd = ThreadingHTTPServer((args.host, args.port), handler)
    host, port = httpd.server_address[0], httpd.server_address[1]
    url = f"http://{host}:{port}/"

    print(f"inspecting {db_path}", file=sys.stderr)
    print(f"serving    {url}  (Ctrl-C to stop)", file=sys.stderr)

    if not args.no_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped", file=sys.stderr)
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
