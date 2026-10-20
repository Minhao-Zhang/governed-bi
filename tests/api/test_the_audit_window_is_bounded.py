"""``/audit/turns?limit`` is bounded.

A bare int would let one request walk every thread. The route refuses a limit outside
``Query(ge=1, le=500)`` rather than clamping it.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from governed_bi.api.routes import make_app
from governed_bi.api.thread_turns import PendingPage


class _TurnLog:
    """Enough of the seam for the route to answer. The window is checked before it is used."""

    TURN_LOG_DIR = "/nowhere"
    SUMMARY_FIELDS: tuple[str, ...] = ("turn_id",)

    def list_turns(self, limit: int = 50, thread_id: str | None = None) -> list[Any]:
        return []

    def get_turn(self, turn_id: str) -> None:
        return None

    def clarifications_of(self, thread_id: str, turn_id: str) -> list[Any]:
        return []


class _Queue:
    PENDING_FIELDS: tuple[str, ...] = ("asked_at",)

    def pending(self, *, limit: int = 50, offset: int = 0) -> PendingPage:
        return PendingPage(rows=[], truncated=False, threads_scanned=0)


def _client() -> Any:
    return TestClient(make_app(object(), _TurnLog(), _Queue()))


def test_an_unbounded_audit_window_is_refused() -> None:
    """The request that walked every thread in the deployment."""
    client = _client()

    assert client.get("/audit/turns?limit=1000000").status_code == 422
    assert client.get("/audit/turns?limit=501").status_code == 422
    assert client.get("/audit/turns?limit=0").status_code == 422
    assert client.get("/audit/turns?limit=-1").status_code == 422


def test_the_windows_a_client_actually_asks_for_still_work() -> None:
    """500 is the ceiling because that is what the audit surface already requests.

    ``ui/components/audit/audit-surface.tsx`` calls ``useAuditTurns(500)`` with no
    ``thread_id``. A bound below that would have been a fix that broke the one caller.
    """
    client = _client()

    assert client.get("/audit/turns").status_code == 200
    assert client.get("/audit/turns?limit=500").status_code == 200
    assert client.get("/audit/turns?limit=1").status_code == 200
