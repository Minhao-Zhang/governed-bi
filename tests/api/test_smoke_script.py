"""``tools/smoke_api.py``, run against assets in memory.

The script is a live-stack tool that is not in CI, so its checks live in ``run_checks``, which
takes a client, and this file runs them over an in-memory session with no database, model or
credential. It proves the script still calls routes that exist and asserts what it says; it is not
a substitute for running it against the real corpus.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


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
    """One table, its column and a term: enough for the session to resolve with no fatal problem."""
    from governed_bi.corpus.schema import (
        AssetType,
        Binding,
        ColumnAsset,
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
            summary="Brands of root beer.", columns=("beer.brands.name",),
        ),
        ColumnAsset(
            id="beer.brands.name", schema="beer", parent_table="beer.brands",
            physical_name="name", summary="Brand name.",
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


def test_every_check_the_script_makes_still_passes(smoke_api) -> None:
    """The whole of ``run_checks``, over the four routes of an app built from :func:`_session`.

    Asserts the failure list is empty **and** that every check ran: a ``run_checks`` that returned
    early would report no failures.
    """
    from fastapi.testclient import TestClient

    from governed_bi.api.routes import make_app

    lines: list[str] = []
    app = make_app(_session(), _Pending())
    failures = smoke_api.run_checks(TestClient(app), out=lines.append)

    assert not failures, "\n".join(lines)
    checks = [line for line in lines if line.startswith(("  ok  ", "  FAIL"))]
    assert len(checks) >= 9, (
        f"the script made {len(checks)} checks, which is fewer than it declares. A `run_checks` "
        f"that returns early reports no failures and prints PASS:\n" + "\n".join(lines)
    )


class _Pending:
    """What ``make_app`` asks of the pending-question reader: nothing is waiting.

    The queue itself is pinned in ``tests/api/test_the_pending_queue_reads_interrupt_state.py``.
    """

    PENDING_FIELDS: tuple[str, ...] = ("asked_at", "question")

    class _Page:
        rows: tuple[Any, ...] = ()
        truncated = False
        threads_scanned = 0

    def pending(self, *, limit: int = 50, offset: int = 0) -> Any:
        return self._Page()


def test_the_help_is_the_module_docstring_and_exits_zero(smoke_api, capsys) -> None:
    """``--help`` must not need a database: it exits before the environment is read."""
    with pytest.raises(SystemExit) as raised:
        smoke_api.main(["--help"])
    assert raised.value.code == 0
    assert "/clarifications/pending" in capsys.readouterr().out
