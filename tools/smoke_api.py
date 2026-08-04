"""One-shot smoke test of the read API against the real corpus. Exits; starts no server.

    uv run --frozen python tools/smoke_api.py

Why a script and not a test: the routes are pure projections of a loaded `Session`, but
*loading* one needs a corpus on disk and the environment that names it, which a unit test must
not depend on. And why not `langgraph dev`: a long-running server is a second thing to remember
to stop, its reloader has served stale `src/` here before, and every question this answers is
answerable by calling the route functions directly.

This is the cheap half of verification. The other half is `npm run check:api` in
`governed-bi-ui`, which validates these same payloads against the client's real zod schemas —
that needs a live engine, because the schemas are TypeScript.

Prints shapes and counts only. Never prints an environment value: a DSN carries credentials.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _load_env() -> list[str]:
    """Put `.env` into the process environment. **A process entry point may do this; `src/`
    may not** — the engine reads its configuration from the environment, and a library that
    reaches for a dotfile makes its behaviour depend on the working directory.

    Returns the NAMES it set, never the values.
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


def main() -> int:
    set_names = _load_env()
    print(f"env: set {len(set_names)} name(s) from .env: {', '.join(sorted(set_names)) or '(none)'}")

    from governed_bi.api import browse_routes, routes

    session = routes._session()
    print(f"corpus: {len(session.assets_by_id)} assets, hash {session.corpus_content_hash[:12]}…")
    print(f"        {len(session.fatal_problems)} fatal, {len(session.degradations)} degradations")

    failures: list[str] = []

    def check(label: str, condition: bool, detail: str = "") -> None:
        print(f"  {'ok  ' if condition else 'FAIL'} {label}{f' — {detail}' if detail else ''}")
        if not condition:
            failures.append(label)

    print("\ncapabilities")
    caps = routes.capabilities()
    check("can_clarify is gated on can_stream", caps["can_clarify"] == (caps["can_stream"] and caps["has_live_model"]),
          f"can_stream={caps['can_stream']} has_live_model={caps['has_live_model']} can_clarify={caps['can_clarify']}")

    print("\nGET /schema/summary")
    summary = browse_routes.schema_summary()
    check("the whole catalog in one page", len(summary["items"]) == summary["total"],
          f"{len(summary['items'])} of {summary['total']} tables")
    check("the applied page is echoed", {"offset", "limit"} <= summary.keys(),
          f"offset={summary.get('offset')} limit={summary.get('limit')}")
    lean = next((c for t in summary["items"] for c in t["columns"]), None)
    check("lean columns carry id + nullable + is_unique",
          lean is not None and {"id", "nullable", "is_unique"} <= lean.keys(),
          ", ".join(sorted(lean.keys())) if lean else "no columns at all")

    print("\nGET /schema (deleted)")
    check("no route declares it", "/schema" not in {getattr(r, "path", None) for r in routes.app.routes})

    for name, payload in (("/graph", routes.er_graph()), ("/knowledge-graph", routes.knowledge_graph())):
        print(f"\nGET {name} (unscoped)")
        meta = payload["meta"]
        check("node_budget is inside meta.scope", "node_budget" in meta["scope"], str(meta["scope"]))
        check("truncation is bounded AND connected", payload["edges"] or not payload["nodes"],
              f"{len(payload['nodes'])} nodes, {len(payload['edges'])} edges, dropped {meta['dropped']}")

    schema_name = summary["items"][0]["schema"] if summary["items"] else None
    if schema_name:
        print(f"\nGET /graph?schema={schema_name}")
        scoped = routes.er_graph(schema=schema_name)
        check("boundary is emitted", "boundary" in scoped,
              f"{len(scoped.get('boundary') or [])} cross-namespace destination(s)")
        multi = [e for e in scoped["edges"] if (e.get("n_relationships") or 1) > 1]
        check("n_relationships counts distinct predicates", all(
            len({j.rsplit("_", 1)[-1] for j in e["join_ids"]}) == e["n_relationships"] for e in scoped["edges"]),
            f"{len(multi)} edge(s) with more than one relationship")

    print("\nGET /columns/{column_id}/related")
    column_id = lean["id"] if lean else None
    if column_id:
        related = browse_routes.column_related(column_id)
        check("a real id resolves", related["meta"]["column_resolvable"] is True, column_id)
        check("every declared key is present",
              {"column", "terms", "rules", "fk_out", "fk_in", "joins", "metrics", "meta"} <= related.keys())
    unknown = browse_routes.column_related("no.such.column")
    check("an unknown id is an answer, not a 404", unknown["meta"]["column_resolvable"] is False)

    print("\nGET /corpus/assets")
    assets = routes.corpus_assets()
    check("rows carry the client's required fields",
          bool(assets) and {"provenance_status", "excluded"} <= assets[0].keys(),
          ", ".join(sorted(assets[0].keys())) if assets else "empty")

    print(f"\n{'PASS' if not failures else 'FAIL: ' + ', '.join(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
