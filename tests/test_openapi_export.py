"""``docs/openapi.json`` still matches the app it claims to describe.

Not coverage for coverage's sake: that file is the contract a *separate*
repository (``../governed-bi-ui``) generates its client from, and it drifted
silently for months — the committed spec omitted the ``X-API-Key`` /
``Authorization`` headers on ``POST /chat`` and ``POST /corpus/edit``, so a
generated client had no way to authenticate against either mutating route.

CI runs ``scripts/export_openapi.py --check`` for the same reason. This test
keeps the guarantee inside the offline suite, where a route change surfaces at
``pytest`` time rather than after the push, and it also exercises the exporter's
own offline construction path (no key, no corpus, no database).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "export_openapi.py"


def _exporter():
    spec = importlib.util.spec_from_file_location("export_openapi", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_committed_openapi_matches_a_fresh_export():
    exporter = _exporter()
    fresh = exporter.render()
    committed = (REPO / "docs" / "openapi.json").read_text(encoding="utf-8")
    assert committed == fresh, (
        "docs/openapi.json is stale (the frontend contract has drifted):\n"
        + "\n".join(exporter.describe_drift(committed, fresh))
        + "\nre-run scripts/export_openapi.py"
    )
