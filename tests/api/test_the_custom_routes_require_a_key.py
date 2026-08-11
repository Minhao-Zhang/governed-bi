"""The custom routes refuse an unauthenticated caller, and say no when no key is configured.

Audit A1/A7, 2026-08-10. ``langgraph.json`` had no ``auth`` key, so ``LANGGRAPH_AUTH_TYPE``
defaulted to ``"noop"`` and every route answered anybody — including ``/audit/turns``, which
returns every thread's SQL, every full record, and an absolute filesystem path.

``api/auth.py`` covers the routes LangGraph Server owns. These are ours, and the platform will not
wrap them: ``http.enable_custom_route_auth`` walks ``app.routes`` expecting each to have ``.app``,
and fastapi 0.141's ``include_router`` leaves an ``_IncludedRouter`` that has none, so the flag
makes the server fail to bind. The check therefore lives in ``routes.py``'s own middleware, and
this file is its negative control — a gate with no test that watches it refuse is a preference.

The ``OPTIONS`` case is here because it broke: refusing a preflight for having no key refuses the
preflight for every cross-origin call the UI makes, and the browser then blocks the real request
with an opaque network error rather than a 401.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from governed_bi.api.auth import API_KEY_HEADER, API_KEY_VAR
from governed_bi.api.routes import app

_KEY = "test-key-for-the-middleware"

#: Any gated path. The middleware runs before the handler, so a refusal needs no session, no
#: model and no database — which is what lets this file assert the boundary without a Postgres
#: fixture. ``/audit/turns`` is named because it is the route whose exposure was the finding.
_GATED = "/audit/turns"


@pytest.fixture
def keyed(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv(API_KEY_VAR, _KEY)
    return TestClient(app, headers={API_KEY_HEADER: _KEY})


@pytest.fixture
def anonymous(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv(API_KEY_VAR, _KEY)
    return TestClient(app)


def test_no_key_is_refused(anonymous: TestClient) -> None:
    response = anonymous.get(_GATED)
    assert response.status_code == 401, (
        f"{_GATED} answered {response.status_code} to a caller with no key. It returns every "
        "thread's SQL and an absolute log path."
    )


def test_a_wrong_key_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(API_KEY_VAR, _KEY)
    client = TestClient(app, headers={API_KEY_HEADER: "not-the-key"})
    assert client.get(_GATED).status_code == 401


def test_a_correct_key_is_not_refused(keyed: TestClient) -> None:
    """Not ``== 200``: the route builds a session and this test owns no database.

    The property is the middleware's decision, so the assertion is that it did **not** refuse —
    which is what makes the three tests above a pair rather than a gate that refuses everything.
    """
    assert keyed.get(_GATED).status_code != 401


def test_an_unset_variable_refuses_rather_than_opens(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail closed. "No key configured" must never mean "everyone", which is exactly what the
    default ``noop`` backend answered before this existed."""
    monkeypatch.delenv(API_KEY_VAR, raising=False)
    response = TestClient(app).get(_GATED)
    assert response.status_code == 401
    assert API_KEY_VAR in response.json()["detail"], "the refusal must name the variable to set"


def test_liveness_needs_no_key(anonymous: TestClient) -> None:
    """A probe that has to hold a credential reports the credential's health, not the process's."""
    response = anonymous.get("/livez")
    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_a_cors_preflight_is_not_refused(anonymous: TestClient) -> None:
    """A browser sends no custom header on a preflight, so gating ``OPTIONS`` breaks every
    cross-origin call. Measured when this middleware was added: both origins 401'd, and the
    symptom in the UI is an opaque network error rather than an authentication failure.

    A preflight response carries no data — only which methods and headers are allowed — and the
    origin allow-list in ``langgraph.json``'s ``http.cors`` is what decides whether the browser
    proceeds.
    """
    response = anonymous.options(
        _GATED,
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": API_KEY_HEADER,
        },
    )
    assert response.status_code != 401, (
        "the preflight was refused for having no key, which blocks every cross-origin request "
        "the UI makes before it is ever sent"
    )

# `test_a_new_run_may_not_carry_a_state_writing_command` lived here and is **deleted**, not moved.
# It called the auth handler directly, so it passed while the handler read a key the runtime does not
# use and while the decorator registering it was absent. The replacement goes through the runtime's
# own dispatch: tests/api/test_a_run_cannot_write_state.py. This file's own docstring also scopes it
# to the routes the platform does not wrap, and `POST /threads/{id}/runs` is a platform route.
