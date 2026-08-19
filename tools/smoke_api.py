"""One-shot smoke test of the read API against the real corpus. Exits; starts no server.

    uv run --frozen python tools/smoke_api.py

A script and not a test because loading a `Session` needs a corpus on disk and the environment
naming it, which a unit test must not depend on. Not `langgraph dev` because its reloader has
served stale `src/` here before.

**Driven over HTTP, through one app** (2026-08-12). It used to call `routes._session()`,
`routes.capabilities()`, `routes.er_graph()`, `routes.knowledge_graph()`, `routes.corpus_assets()`,
`browse_routes.schema_summary()` and `browse_routes.column_related()` as module-level functions.
The C5 refactor made every one of them a closure inside `make_app`, so this file has been a
`AttributeError` on its first line of real work ever since — and nothing noticed, because it is
not in CI and no test imports it. That is the defect it now guards against as well as the ones it
was written for: the checks live in :func:`run_checks`, which takes a client and is exercised by
`tests/api/test_smoke_script.py` against a handful of in-memory assets, so it cannot rot again
without a test going red. The environment is only in :func:`main`.

`TestClient` rather than a socket: this asks whether the payloads are the shape the client parses,
and a running uvicorn adds a port to that question without adding an answer.

The cheap half of verification. The other half is `npm --prefix ui run check:api`, which validates
these payloads against the client's zod schemas and needs a live engine.

Prints shapes and counts only. Never prints an environment value: a DSN carries credentials.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Callable

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))


def _load_env() -> list[str]:
    """Put `.env` into the process environment. Returns the NAMES it set, never the values.

    A process entry point may do this; `src/` may not — a library that reaches for a dotfile
    makes its behaviour depend on the working directory.
    """
    path = REPO / ".env"
    if not path.exists():
        return []
    names: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value
            names.append(key)
    return names


def run_checks(client: Any, *, out: Callable[[str], None] = print) -> list[str]:
    """Every assertion this script makes, over an app. Returns the labels that failed.

    Takes a client rather than building one, which is the whole point: the environment-resolved
    app is one caller and a small in-memory app is the other, and the checks cannot drift
    between them because there is one copy of them.
    """
    failures: list[str] = []

    def check(label: str, condition: bool, detail: str = "") -> None:
        out(f"  {'ok  ' if condition else 'FAIL'} {label}{f' — {detail}' if detail else ''}")
        if not condition:
            failures.append(label)

    def get(path: str, **params: Any) -> Any:
        response = client.get(path, params=params or None)
        check(f"GET {path} answers", response.status_code == 200, f"{response.status_code}")
        return response.json() if response.status_code == 200 else {}

    out("\ncapabilities")
    caps = get("/capabilities")
    check(
        "can_clarify is gated on can_stream and a live model",
        caps.get("can_clarify") == (caps.get("can_stream") and caps.get("has_live_model")),
        f"can_stream={caps.get('can_stream')} has_live_model={caps.get('has_live_model')} "
        f"can_clarify={caps.get('can_clarify')}",
    )

    out("\nGET /schema/summary")
    summary = get("/schema/summary")
    check(
        "the whole catalog in one page",
        len(summary.get("items") or []) == summary.get("total"),
        f"{len(summary.get('items') or [])} of {summary.get('total')} tables",
    )
    check(
        "the applied page is echoed",
        {"offset", "limit"} <= summary.keys(),
        f"offset={summary.get('offset')} limit={summary.get('limit')}",
    )
    lean = next((c for t in summary.get("items") or [] for c in t["columns"]), None)
    check(
        "lean columns carry id + nullable + is_unique",
        lean is not None and {"id", "nullable", "is_unique"} <= lean.keys(),
        ", ".join(sorted(lean.keys())) if lean else "no columns at all",
    )

    out("\nGET /schema and GET /health (both deleted)")
    declared = {getattr(route, "path", None) for route in client.app.routes}
    check("no route declares /schema", "/schema" not in declared)
    check("no route declares /health", "/health" not in declared)

    for name in ("/graph", "/knowledge-graph"):
        out(f"\nGET {name} (unscoped)")
        payload = get(name)
        meta = payload.get("meta") or {}
        check("node_budget is inside meta.scope", "node_budget" in (meta.get("scope") or {}),
              str(meta.get("scope")))
        check(
            "truncation is bounded AND connected",
            bool(payload.get("edges")) or not payload.get("nodes"),
            f"{len(payload.get('nodes') or [])} nodes, {len(payload.get('edges') or [])} edges, "
            f"dropped {meta.get('dropped')}",
        )

    schema_name = (summary.get("items") or [{}])[0].get("schema")
    if schema_name:
        out(f"\nGET /graph?schema={schema_name}")
        scoped = get("/graph", schema=schema_name)
        check("boundary is emitted", "boundary" in scoped,
              f"{len(scoped.get('boundary') or [])} cross-namespace destination(s)")
        edges = scoped.get("edges") or []
        multi = [e for e in edges if (e.get("n_relationships") or 1) > 1]
        check(
            "n_relationships counts distinct predicates",
            all(
                len({j.rsplit("_", 1)[-1] for j in e["join_ids"]}) == e["n_relationships"]
                for e in edges
            ),
            f"{len(multi)} edge(s) with more than one relationship",
        )

    out("\nGET /columns/{column_id}/related")
    if lean:
        related = get(f"/columns/{lean['id']}/related")
        check("a real id resolves", (related.get("meta") or {}).get("column_resolvable") is True,
              lean["id"])
        check(
            "every declared key is present",
            {"column", "terms", "rules", "fk_out", "fk_in", "joins", "metrics", "meta"}
            <= related.keys(),
        )
    unknown = get("/columns/no.such.column/related")
    check("an unknown id is an answer, not a 404",
          (unknown.get("meta") or {}).get("column_resolvable") is False)

    out("\nGET /corpus/assets")
    assets = get("/corpus/assets")
    check(
        "rows carry the client's required fields",
        bool(assets) and {"provenance_status", "excluded"} <= assets[0].keys(),
        ", ".join(sorted(assets[0].keys())) if assets else "empty",
    )

    out("\nGET /audit/corpus")
    audit = get("/audit/corpus")
    check(
        "fatal and degradations are separate lists (ADR 0008 D9)",
        isinstance((audit.get("problems") or {}).get("fatal"), list)
        and isinstance((audit.get("problems") or {}).get("degradations"), list),
        f"servable={audit.get('servable')}",
    )

    # These two used to be the hole in this file: `main()` was required to build a `turn_log` and
    # no check read one, which made the reader the only wired dependency nothing here could break.
    # What is checkable without a server is the whole path except the rows — route to reader to
    # projection to envelope — so that is what these check. Populated rows are
    # `npm --prefix ui run check:api` against a live engine; see `main()` for why they cannot be
    # here.
    out("\nGET /audit/turns")
    turns = get("/audit/turns", limit=5)
    meta = turns.get("meta") or {}
    check(
        "the envelope the audit footer reads is whole",
        isinstance(turns.get("turns"), list)
        and meta.get("n") == len(turns.get("turns") or [])
        and bool(meta.get("log_dir"))
        and "turn_id" in (meta.get("columns") or []),
        f"n={meta.get('n')} columns={len(meta.get('columns') or [])}",
    )

    out("\nGET /audit/turns/{turn_id}/trace")
    missing = get("/audit/turns/no-such-turn/trace")
    check(
        "an unknown turn is an answer, not a 404",
        missing.get("found") is False and missing.get("turn_id") == "no-such-turn",
        str(missing.get("found")),
    )

    return failures


class _EmptyThreadStore:
    """The thread store this script can honestly offer ``ThreadTurnLog``: an empty one.

    Not a stand-in for the real store and not seeded to look like one. Its only job is to let the
    reader answer so that the route, the projection and the wire envelope are exercised; a seeded
    fake would make the row assertions assertions about this class. ``main()`` separately checks
    that the *production* reader refuses outside the server, so nothing about this substitution is
    silent.
    """

    class _Threads:
        async def search(self, **_: Any) -> list[Any]:
            return []

    def __init__(self) -> None:
        self.threads = self._Threads()


def main() -> int:
    set_names = _load_env()
    print(f"env: set {len(set_names)} name(s) from .env: {', '.join(sorted(set_names)) or '(none)'}")

    from fastapi.testclient import TestClient

    from governed_bi.api.graph_app import session_from_environment
    from governed_bi.api.routes import make_app
    from governed_bi.api.thread_turns import (
        InProcessServerRequired,
        PendingClarifications,
        ThreadTurnLog,
    )

    session = session_from_environment()
    print(f"corpus: {len(session.assets_by_id)} assets, hash {session.corpus_content_hash[:12]}…")
    print(f"        {len(session.fatal_problems)} fatal, {len(session.degradations)} degradations")

    # **Why the audit checks below run over an empty thread store.** `ThreadTurnLog` reads the
    # Agent server's own thread rows over `langgraph_sdk`'s in-process ASGI transport, which
    # resolves only inside that server, and this script starts no server (see the module
    # docstring). So the production reader has no store here — and rather than leave that as a
    # sentence, it is the check immediately below: the reader must refuse *by name*. It used to
    # leak a transport and die on a bare `TypeError` instead.
    print("\nthe turn reader outside the server")
    failures: list[str] = []
    try:
        ThreadTurnLog().list_turns(limit=1)
        print("  FAIL the production turn reader answered with no server to read")
        failures.append("the production turn reader answered with no server to read")
    except InProcessServerRequired as refusal:
        print(f"  ok   refuses by name — {type(refusal).__name__}")

    # No headers: no route asks for a credential (2026-08-13). This used to carry `x-api-key`, and
    # `main()` used to exit 2 when the variable was unset rather than measure the auth gate.
    # `make_app` also lost its `graph` with `POST /chat`, and nothing here posts a turn.
    failures += run_checks(TestClient(make_app(
        session, ThreadTurnLog(_EmptyThreadStore), PendingClarifications(_EmptyThreadStore)
    )))

    print(f"\n{'PASS' if not failures else 'FAIL: ' + ', '.join(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
