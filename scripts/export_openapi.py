"""Export docs/openapi.json from the real FastAPI app (``api.create_app``).

That file is a **cross-repo contract**: the frontend (``../governed-bi-ui``, a
separate repository) is written against it, so a route the app grew — or a header
it started requiring — is invisible there until this artifact is re-exported.

It had no generator for most of its life, and it drifted exactly the way an
uncheckable artifact does: the committed spec omitted the ``X-API-Key`` /
``Authorization`` header parameters that ``require_mutating_auth`` adds to
``POST /chat`` and ``POST /corpus/edit``, so a client generated from it would
have called both mutating routes with no way to authenticate. CI now runs
``--check`` on every push, which fails the build when the committed bytes and a
fresh export disagree.

The app is built from a hand-made :class:`ServeStack` rather than
``build_stack()``: the route *shapes* are what the spec records, and none of them
depend on a corpus, a model, or a reachable database. Building it this way is
what lets the exporter run offline with no ``OPENAI_API_KEY`` — ``build_stack()``
loads the corpus tree and probes the datasource, neither of which CI has.

Re-run after touching ``src/governed_bi/api/`` (routes, docstrings, or the
``schemas.py`` response models) and commit both:

    uv run python scripts/export_openapi.py

Check without writing (what CI runs):

    uv run python scripts/export_openapi.py --check
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

OUT = pathlib.Path(__file__).resolve().parents[1] / "docs" / "openapi.json"

# Match the committed artifact byte for byte: 2-space indent, FastAPI's natural
# key order (NOT sorted), ASCII-escaped non-ASCII (json.dumps default), trailing
# newline. Changing any of these turns every future diff into whole-file noise.
INDENT = 2

MAX_DIFF_LINES = 40


def build_app():
    """The same app ``create_app`` serves, assembled without I/O or credentials."""
    from governed_bi.api.app import create_app
    from governed_bi.api.stack import ServeStack
    from governed_bi.config import Environment, Settings
    from governed_bi.corpus import Corpus
    from governed_bi.gateway import Identity

    corpus = Corpus()  # empty: the spec records route shapes, not corpus content
    return create_app(
        ServeStack(
            corpus_full=corpus,
            corpus_analyst=corpus.for_analyst(),
            settings=Settings.for_env(Environment.dev),
            dialect="sqlite",
            sqlite_path=pathlib.Path("data/bird/beer_factory.sqlite"),
            identity=Identity(user="demo", all_access=True),
            embedder=None,
            narrator=None,
            model_name=None,
            has_live_model=False,
            # Explicit None: the default factory imports the retrieval stack
            # (agents extra) for a cache no route touches during schema export.
            index_cache=None,
        )
    )


def render() -> str:
    """The exact bytes docs/openapi.json should contain."""
    return json.dumps(build_app().openapi(), indent=INDENT) + "\n"


def _flatten(node, prefix: str = "") -> dict[str, object]:
    """JSON tree -> {pointer: leaf}, so a diff can name the key that moved."""
    if isinstance(node, dict):
        out: dict[str, object] = {}
        for key, value in node.items():
            out.update(_flatten(value, f"{prefix}/{key}"))
        return out
    if isinstance(node, list):
        out = {}
        for i, value in enumerate(node):
            out.update(_flatten(value, f"{prefix}/{i}"))
        return out
    return {prefix: node}


def describe_drift(committed: str, fresh: str) -> list[str]:
    """Which paths/keys differ, as lines a CI log reader can act on."""
    try:
        old = _flatten(json.loads(committed))
    except json.JSONDecodeError as exc:
        return [f"docs/openapi.json is not valid JSON: {exc}"]
    new = _flatten(json.loads(fresh))

    lines = []
    for pointer in sorted(set(new) - set(old)):
        lines.append(f"  + missing from the committed file: {pointer}")
    for pointer in sorted(set(old) - set(new)):
        lines.append(f"  - stale in the committed file:    {pointer}")
    for pointer in sorted(set(old) & set(new)):
        if old[pointer] != new[pointer]:
            lines.append(f"  ~ changed: {pointer}")
    if not lines:
        # Same tree, different bytes: key order or formatting drifted.
        lines.append("  ~ same JSON tree, different bytes (key order / indentation)")
    if len(lines) > MAX_DIFF_LINES:
        extra = len(lines) - MAX_DIFF_LINES
        lines = lines[:MAX_DIFF_LINES] + [f"  ... and {extra} more difference(s)"]
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check",
        action="store_true",
        help="do not write; exit 1 when docs/openapi.json differs from a fresh export",
    )
    args = parser.parse_args()

    fresh = render()

    if args.check:
        committed = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
        if committed == fresh:
            print(f"docs/openapi.json is up to date ({len(fresh.splitlines())} lines)")
            return 0
        print("docs/openapi.json is STALE - the frontend contract has drifted:")
        if not committed:
            print("  docs/openapi.json is missing")
        else:
            for line in describe_drift(committed, fresh):
                print(line)
        print("\nRegenerate and commit it:\n    uv run python scripts/export_openapi.py")
        return 1

    OUT.write_text(fresh, encoding="utf-8")
    print(f"docs/openapi.json written: {len(fresh.splitlines())} lines")
    return 0


if __name__ == "__main__":
    sys.exit(main())
