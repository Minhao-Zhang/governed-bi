"""``/audit/turns?limit`` is bounded, and was not. The four feedback routes always were.

``thread_turns`` reads the caller's ``limit`` as ``max(1, int(limit))`` — a floor and no
ceiling — and then pages threads until it has that many turns, materialising every turn
envelope it walks past, stopping only at ``_MAX_THREADS = 1000``. With a bare ``int = 50`` on
the route, ``?limit=1000000`` walked every thread in the deployment in one request. The UI
already asks for 500 with no ``thread_id``.

**Separate from the fact that the route is unauthenticated**, which is deliberate and
recorded in ``api/routes.py``'s header ("A7 is open again, knowingly", with the reachability
argument for LangGraph Studio). The unbounded walk behind it was not decided anywhere — it is
the one parameter on this surface that had a floor and no ceiling, while
``/clarifications/pending``, ``/observations`` and ``/patches`` all carry
``Query(ge=…, le=…)``.

Refused rather than clamped, for the reason the sibling routes give: a clamp answers a
different question than the one asked and says nothing about having done so.
"""

from __future__ import annotations

import tempfile
from typing import Any

from fastapi.testclient import TestClient

from governed_bi.api.routes import make_app
from governed_bi.api.thread_turns import PendingPage
from governed_bi.feedback.store import FeedbackStore


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
    store = FeedbackStore(tempfile.mkdtemp(prefix="auditwin-") + "/feedback.sqlite")
    return TestClient(make_app(object(), _TurnLog(), _Queue(), store))


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
