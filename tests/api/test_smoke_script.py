"""``tools/smoke_api.py``, run against four assets in memory.

**Why a script gets a test.** It is a live-stack tool, it is not in CI, and no test imported it —
so when the C5 refactor turned `routes.capabilities()`, `routes.er_graph()`,
`routes.knowledge_graph()`, `routes.corpus_assets()`, `routes._session()`,
`browse_routes.schema_summary()` and `browse_routes.column_related()` into closures inside
`make_app`, every one of those calls became an `AttributeError` and the suite stayed green. The
script was broken for as long as the refactor was in the tree and the only way to find out was to
run it against a Postgres server and a corpus.

That is the failure this file removes, and the removal is a design change rather than a test: the
checks moved into :func:`~tools.smoke_api.run_checks`, which takes a client. The environment lives
in ``main()`` alone. So the assertions the script makes about the payload shapes can run over an
in-memory session with no database, no model, no corpus on disk and no credential, and the only
thing left un-exercised is the environment adapter — which is the part a smoke script exists to
exercise by hand.

**This is not a substitute for running it.** It cannot tell you the real corpus produces a
connected graph or that a real column resolves. It tells you the script still calls things that
exist and still asserts what it says it asserts.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from contracts import needs  # noqa: E402

pytestmark = [needs("J")]

REPO = Path(__file__).resolve().parent.parent.parent


@pytest.fixture(scope="module")
def smoke_api():
    """The script as a module. Imported by path, because ``tools/`` is not a package."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "smoke_api_under_test", REPO / "tools" / "smoke_api.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _session() -> Any:
    """Two joined tables, their columns, and a term. The smallest corpus the script's checks fit.

    **Two tables and a join, deliberately.** The script asserts ``edges or not nodes`` on
    ``/graph`` — "truncation is bounded AND connected" — and a one-table corpus produces one node
    and no edges, which fails a check that is not wrong about the lake it was written for. The
    fixture is the shape the real corpus has rather than the assertion being loosened to fit a
    smaller one: this file exists to prove the script still runs, not to re-decide what it checks.
    (The check *is* imprecise — an isolated table is a fact about a corpus, not a defect — and
    that is reported rather than changed here.)
    """
    from governed_bi.corpus.schema import (
        AssetType,
        Binding,
        ColumnAsset,
        JoinAsset,
        SchemaAsset,
        TableAsset,
        TermAsset,
    )
    from governed_bi.govern.policy import GovernancePolicy
    from governed_bi.serve.session import from_assets

    assets = [
        SchemaAsset(id="beer", name="beer", summary="beer brands and sales"),
        TableAsset(
            id="beer.brands", schema="beer", physical_name="brands",
            summary="Brands of root beer.",
            columns=("beer.brands.name", "beer.brands.id"),
        ),
        ColumnAsset(
            id="beer.brands.name", schema="beer", parent_table="beer.brands",
            physical_name="name", summary="Brand name.",
        ),
        ColumnAsset(
            id="beer.brands.id", schema="beer", parent_table="beer.brands",
            physical_name="id", summary="Brand id.",
        ),
        TableAsset(
            id="beer.sales", schema="beer", physical_name="sales",
            summary="Sales per brand.",
            columns=("beer.sales.brand_id",),
        ),
        ColumnAsset(
            id="beer.sales.brand_id", schema="beer", parent_table="beer.sales",
            physical_name="brand_id", summary="The brand sold.",
        ),
        JoinAsset(
            id="beer.sales__beer.brands__brand",
            left_table="beer.sales", right_table="beer.brands",
            on="beer.sales.brand_id = beer.brands.id",
            summary="Each sale is of one brand.",
        ),
        TermAsset(
            id="term.root_beer", name="root beer", summary="root beer, sarsaparilla",
            binding=Binding(target_type=AssetType.column, target_id="beer.brands.name"),
        ),
    ]
    session = from_assets(
        assets,
        connector=None,
        policy=GovernancePolicy(guard_rules_enabled={}),
        db_id="beer",
        corpus_content_hash_="corpus-under-smoke",
        agent_model=None,
    )
    assert not session.fatal_problems, [str(p) for p in session.fatal_problems]
    return session


class _TurnLog:
    """The turn-log surface ``make_app`` reads, in memory. Readers only -- nothing appends."""

    TURN_LOG_DIR = Path("/nowhere")
    SUMMARY_FIELDS: tuple[str, ...] = ("turn_id", "outcome")

    def list_turns(self, limit: int = 50, thread_id: str | None = None) -> list[dict[str, Any]]:
        return []

    def get_turn(self, turn_id: str) -> dict[str, Any] | None:
        return None


def test_every_check_the_script_makes_still_passes(smoke_api) -> None:
    """The whole of ``run_checks``, over an app built from :func:`_session`.

    Asserts the returned failure list is empty **and** that the run printed something: a
    ``run_checks`` that returned early would report no failures, which is the shape the script's
    own `PASS` line would then print.

    The client is built here rather than through the script's own helper, because the helper's
    only remaining job was to attach a transport credential and the engine requires none
    (2026-08-13). ``run_checks`` takes a client, which is the seam that matters.
    """
    from fastapi.testclient import TestClient

    from governed_bi.api.routes import make_app

    lines: list[str] = []
    app = make_app(_session(), _TurnLog())
    failures = smoke_api.run_checks(TestClient(app), out=lines.append)

    assert not failures, "\n".join(lines)
    checks = [line for line in lines if line.startswith(("  ok  ", "  FAIL"))]
    assert len(checks) >= 15, (
        f"the script made {len(checks)} checks, which is fewer than it declares. A `run_checks` "
        f"that returns early reports no failures and prints PASS:\n" + "\n".join(lines)
    )
