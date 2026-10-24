"""One-shot smoke test of the custom REST routes against the real corpus. Exits; starts no server.

    uv run --frozen python tools/smoke_api.py

Loads `.env`, builds the session from the environment (a Postgres DSN and a corpus on disk), and
drives the app `langgraph.json` mounts through a `TestClient`. It checks the eight custom routes
and nothing else, because they are the only ones the app declares:

* `GET /livez` answers `{"ok": true}`.
* `GET /readyz` answers 200 with `ready: true`: the session resolves with no fatal corpus problem.
* `GET /capabilities` answers, and `can_clarify` is true only with `can_stream` and a live model.
* `GET /clarifications/pending` answers with a whole wrapper: `rows`, and `meta` carrying `n`,
  `truncated`, `threads_scanned`, `limit`, `offset` and `columns`.
* `GET /conversations` answers with a whole wrapper: `rows`, and `meta` carrying `n`, `total`,
  `truncated`, `threads_scanned`, `limit`, `offset` and `ending`.
* `GET /examples` answers with `examples`, each item a `question` and a `db_id` and nothing else.
* `GET /layer` lists schemas, and the first one's model is served by `GET /layer/{schema}`.
* The app declares exactly those eight custom routes.
* The production pending reader, used outside the LangGraph server, refuses by name
  (`InProcessServerRequired`) rather than leaking a transport.

The queue and the conversations are read over an empty thread store, because the real one is the Agent server's and this
script starts no server; a running server's routes are what `langgraph dev` and the chat page show.
The checks live in `run_checks`, which takes a client, so `tests/api/test_smoke_script.py` runs
them against assets in memory and they cannot rot unnoticed.

Prints shapes and counts only. Never prints an environment value: a DSN carries credentials.
Exits 0 when every check passes and 1 otherwise.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any, Callable

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

#: The custom routes the app declares. The platform's `/threads` routes are not the app's.
CUSTOM_ROUTES = frozenset({
    "/livez", "/readyz", "/capabilities", "/clarifications/pending", "/conversations", "/examples",
    "/layer", "/layer/{schema}",
})

PENDING_META = frozenset({"n", "truncated", "threads_scanned", "limit", "offset", "columns"})

CONVERSATIONS_META = frozenset(
    {"n", "total", "truncated", "threads_scanned", "limit", "offset", "ending"}
)


def _load_env() -> list[str]:
    """Put `.env` into the process environment. Returns the NAMES it set, never the values.

    A process entry point may do this; `src/` may not, or its behaviour would depend on the
    working directory.
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

    Takes a client so the environment-resolved app and a small in-memory one share one copy of
    the checks.
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

    out("\nGET /livez")
    check("liveness is ok", get("/livez") == {"ok": True})

    out("\nGET /readyz")
    check("the session resolves with no fatal problem", get("/readyz").get("ready") is True)

    out("\nGET /capabilities")
    caps = get("/capabilities")
    check(
        "can_clarify is gated on can_stream and a live model",
        caps.get("can_clarify") == (caps.get("can_stream") and caps.get("has_live_model")),
        f"can_stream={caps.get('can_stream')} has_live_model={caps.get('has_live_model')} "
        f"can_clarify={caps.get('can_clarify')}",
    )

    out("\nGET /clarifications/pending")
    queue = get("/clarifications/pending", limit=5)
    meta = queue.get("meta") or {}
    check(
        "the envelope is whole",
        isinstance(queue.get("rows"), list)
        and PENDING_META <= meta.keys()
        and meta.get("n") == len(queue.get("rows") or [])
        and "question" in (meta.get("columns") or []),
        f"n={meta.get('n')} truncated={meta.get('truncated')}",
    )

    out("\nGET /conversations")
    listed = get("/conversations", limit=5)
    listed_meta = listed.get("meta") or {}
    check(
        "the envelope is whole",
        isinstance(listed.get("rows"), list)
        and CONVERSATIONS_META <= listed_meta.keys()
        and listed_meta.get("n") == len(listed.get("rows") or []),
        f"n={listed_meta.get('n')} total={listed_meta.get('total')}",
    )
    check(
        "an unknown ending is refused",
        client.get("/conversations", params={"ending": "nonsense"}).status_code == 422,
    )

    out("\nGET /examples")
    examples = get("/examples")
    rows = examples.get("examples")
    check(
        "examples are questions and databases, with no SQL",
        isinstance(rows, list)
        and all(
            set(row) == {"question", "db_id"} and row["question"] and row["db_id"]
            for row in rows
        ),
        f"n={len(rows) if isinstance(rows, list) else None}",
    )

    out("\nGET /layer")
    schemas = get("/layer").get("schemas")
    listed_n = len(schemas) if isinstance(schemas, list) else None
    check("the layer lists schemas", isinstance(schemas, list), f"n={listed_n}")
    if schemas:
        served = client.get(f"/layer/{schemas[0]}")
        as_yaml = served.status_code == 200 and served.text.startswith("version:")
        check("the first schema's model is served as YAML", as_yaml)

    out("\nthe route table")
    declared = {getattr(route, "path", None) for route in client.app.routes}
    custom = {p for p in declared if p and not p.startswith(("/docs", "/openapi", "/redoc"))}
    check("exactly the eight custom routes", custom == CUSTOM_ROUTES, ", ".join(sorted(custom)))

    return failures


class _EmptyThreadStore:
    """The thread store this script can offer the pending reader: an empty one.

    Not seeded, so the checks stay about the route and its wrapper rather than about this class.
    """

    class _Threads:
        async def search(self, **_: Any) -> list[Any]:
            return []

    def __init__(self) -> None:
        self.threads = self._Threads()


def main(argv: list[str] | None = None) -> int:
    argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    ).parse_args(argv)

    set_names = _load_env()
    print(f"env: set {len(set_names)} name(s) from .env: {', '.join(sorted(set_names)) or '(none)'}")

    from fastapi.testclient import TestClient

    from governed_bi.api.graph_app import session_from_environment
    from governed_bi.api.routes import make_app
    from governed_bi.api.thread_turns import InProcessServerRequired, ThreadReader

    session = session_from_environment()
    print(f"corpus: {len(session.assets_by_id)} assets, hash {session.corpus_content_hash[:12]}…")
    print(f"        {len(session.fatal_problems)} fatal, {len(session.degradations)} degradations")

    print("\nthe pending reader outside the server")
    failures: list[str] = []
    try:
        ThreadReader().pending(limit=1)
        print("  FAIL the production reader answered with no server to read")
        failures.append("the production reader answered with no server to read")
    except InProcessServerRequired as refusal:
        print(f"  ok   refuses by name — {type(refusal).__name__}")

    failures += run_checks(TestClient(make_app(session, ThreadReader(_EmptyThreadStore))))

    print(f"\n{'PASS' if not failures else 'FAIL: ' + ', '.join(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
