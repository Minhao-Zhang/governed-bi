"""Regenerate ``docs/openapi.json`` from the implementation.

ADR 0007 §312 requires this file to be "regenerated from the implementation rather than kept by
hand". There was no script, so it drifted: on 2026-08-18 the tracked spec held 15 routes while
the app mounted 32, missing the entire curation surface -- clarifications, corpus drafts,
conflicts, assumptions, feedback, elicitation, settings/toggles and trust-loop/metrics. A
mandate with no command behind it is a mandate that quietly stops being true.

**Offline on purpose.** ``routes.py:app`` is the FastAPI app ``langgraph.json`` mounts, and
``app.openapi()`` needs no server, no database and no model credential -- so this runs in CI and
in a fresh clone. The platform routes (``/assistants``, ``/threads``, ...) are absent here
because LangGraph adds them by wrapping this app at runtime; the spec is our custom surface,
which is what ``info.description`` already claims it is.

    uv run python tools/dump_openapi.py --check    # exit 1 if it is stale, print the diff
    uv run python tools/dump_openapi.py            # rewrite the file -- SEE THE WARNING BELOW

**Do not rewrite the file yet.** Running this today deletes 46 of the tracked spec's 48
component schemas and adds none, because every route is annotated ``-> dict[str, Any]`` and
FastAPI cannot infer a body shape from that. Tried on 2026-08-18: 15 paths/48 schemas became
33 paths/2 schemas. More routes, no shapes -- and the change was reverted.

**Two ADRs currently disagree, and the code makes them incompatible:**

* ADR 0010 §479 -- "``docs/openapi.json`` remains the spec for route shapes."
* ADR 0007 §312 -- it "must be regenerated from the implementation rather than kept by hand;
  a spec no process checks is the defect this repository keeps rediscovering."

Both cannot hold while the handlers return bare dicts: regeneration is the only thing that
keeps paths honest, and it is also the thing that erases the shapes. ADR 0007 was right about
the failure mode -- the tracked spec drifted to 15 paths against 33 mounted, missing the whole
curation surface -- but the fix is response models on the handlers, not a dump that trades one
kind of wrongness for another. That is an upstream call.

So use ``--check`` as a **drift detector** in the meantime: it names exactly which routes the
spec has stopped describing, which is the part a reader can act on without a design decision.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

SPEC = Path(__file__).resolve().parent.parent / "docs" / "openapi.json"


def generate() -> dict:
    """The spec as the app declares it, key-sorted so a regeneration diffs cleanly."""
    from governed_bi.api.routes import app

    return json.loads(json.dumps(app.openapi(), sort_keys=True))


def _render(spec: dict) -> str:
    # 1-space indent and a trailing newline: matches the tracked file, so the first
    # regeneration diffs on content rather than on whitespace.
    return json.dumps(spec, indent=1, sort_keys=True, ensure_ascii=False) + "\n"


def main() -> int:
    fresh = generate()
    rendered = _render(fresh)

    if "--check" not in sys.argv[1:]:
        SPEC.write_text(rendered, encoding="utf-8")
        n = len(fresh.get("paths", {}))
        print(f"wrote {SPEC.relative_to(SPEC.parent.parent)} — {n} paths")
        return 0

    if not SPEC.exists():
        print(f"{SPEC} is missing; run without --check", file=sys.stderr)
        return 1
    current = json.loads(SPEC.read_text(encoding="utf-8"))
    if current == fresh:
        print(f"openapi.json is current — {len(fresh.get('paths', {}))} paths")
        return 0

    have, want = set(current.get("paths", {})), set(fresh.get("paths", {}))
    for p in sorted(want - have):
        print(f"  missing from the spec: {p}", file=sys.stderr)
    for p in sorted(have - want):
        print(f"  in the spec but not mounted: {p}", file=sys.stderr)
    if have == want:
        print("  same paths, different shapes — a request or response body changed", file=sys.stderr)
    print("openapi.json is stale; run without --check", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
