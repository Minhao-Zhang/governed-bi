"""A server that cannot serve says so, and a dropped audit row leaves a log line.

``/livez`` answers without touching the session, so under bare ``uvicorn`` a missing DSN was a
500 on the first user request while the liveness probe stayed green (``docs/open-work.md``
§6.8). The ``record`` node swallows its own failures because nothing after it can stamp them,
which made a lost audit row invisible (§6.1).
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from contracts import scratch_feedback_store  # noqa: E402


def _client(get_session: Any) -> TestClient:
    from governed_bi.api.routes import _build_app

    return TestClient(_build_app(get_session, object(), object(), scratch_feedback_store()))


def test_readyz_is_503_when_the_session_cannot_be_built() -> None:
    def missing_dsn() -> Any:
        raise RuntimeError("no database: set one of GOVERNED_BI_PG_DSN / PG_RENAME_DECOY_DSN")

    response = _client(missing_dsn).get("/readyz")
    assert response.status_code == 503
    assert response.json() == {
        "ready": False,
        "reason": "no database: set one of GOVERNED_BI_PG_DSN / PG_RENAME_DECOY_DSN",
    }


def test_readyz_names_only_the_type_of_an_unexpected_error() -> None:
    """A driver error can carry the DSN, so its text stays out of an unauthenticated response."""

    def leaky() -> Any:
        raise ValueError("password=hunter2 host=db")

    response = _client(leaky).get("/readyz")
    assert response.status_code == 503
    assert response.json()["reason"] == "ValueError"


def test_readyz_is_ready_without_a_model_because_that_mode_is_supported() -> None:
    session = SimpleNamespace(agent_model=None, fatal_problems=())
    assert _client(lambda: session).get("/readyz").status_code == 200


def test_readyz_is_503_on_a_fatal_corpus_problem() -> None:
    session = SimpleNamespace(agent_model=object(), fatal_problems=(object(),))
    assert _client(lambda: session).get("/readyz").status_code == 503


def test_a_model_without_its_credential_is_not_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    """The real environment adapter: DSN and corpus present, model named, no credential."""
    from governed_bi import credentials
    from governed_bi.api import graph_app
    from governed_bi.model import provider

    monkeypatch.setattr(graph_app, "_SESSION", None)
    monkeypatch.setattr(credentials, "load_into_environ", lambda *a, **k: None)
    monkeypatch.setattr(credentials, "secret", lambda *names: "host=127.0.0.1 dbname=x")
    monkeypatch.setattr(graph_app, "_resolve_corpus_dir", lambda *a: "corpus")
    monkeypatch.setenv(graph_app.MODEL_VAR, "some-model")
    monkeypatch.setattr(provider, "credentials_present", lambda name: False)

    response = _client(graph_app.session_from_environment).get("/readyz")
    assert response.status_code == 503
    assert "no credential" in response.json()["reason"]


def test_readyz_is_200_when_configured() -> None:
    session = SimpleNamespace(agent_model=object(), fatal_problems=())
    response = _client(lambda: session).get("/readyz")
    assert response.status_code == 200
    assert response.json() == {"ready": True}


def test_livez_stays_up_while_readyz_is_not() -> None:
    def missing_dsn() -> Any:
        raise RuntimeError("no database")

    client = _client(missing_dsn)
    assert client.get("/livez").status_code == 200
    assert client.get("/readyz").status_code == 503


def test_bare_uvicorn_with_no_dsn_is_not_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    """The real environment adapter, with no DSN reachable."""
    from governed_bi import credentials
    from governed_bi.api import graph_app

    monkeypatch.setattr(graph_app, "_SESSION", None)
    monkeypatch.setattr(credentials, "load_into_environ", lambda *a, **k: None)
    monkeypatch.setattr(credentials, "secret", lambda *names: None)

    response = _client(graph_app.session_from_environment).get("/readyz")
    assert response.status_code == 503
    assert response.json()["reason"].startswith("no database")


def test_a_failing_record_node_logs_the_dropped_row(caplog: pytest.LogCaptureFixture) -> None:
    from governed_bi.api.graph_app import record_node

    class _Exploding(dict):
        def get(self, key: str, default: Any = None) -> Any:
            if key == "turn_id":
                return "turn-7"
            if key == "outcome":
                raise KeyError("outcome")
            return super().get(key, default)

    state = {"thread_id": "thread-42", "answer": _Exploding(record={"turn_id": "turn-7"})}
    with caplog.at_level(logging.ERROR, logger="governed_bi.api.graph_app"):
        assert record_node()(state) == {}
    [entry] = [r for r in caplog.records if r.name == "governed_bi.api.graph_app"]
    assert entry.levelno == logging.ERROR
    assert "thread-42" in entry.getMessage() and "turn-7" in entry.getMessage()


def test_the_drop_is_logged_even_when_the_state_cannot_be_read(caplog: pytest.LogCaptureFixture) -> None:
    """The handler runs on a state that already broke once; reading its ids must not raise too."""
    from governed_bi.api.graph_app import record_node

    class _Hostile(dict):
        def get(self, key: str, default: Any = None) -> Any:
            raise RuntimeError("unreadable")

    with caplog.at_level(logging.ERROR, logger="governed_bi.api.graph_app"):
        assert record_node()(_Hostile()) == {}
    [entry] = [r for r in caplog.records if r.name == "governed_bi.api.graph_app"]
    assert "<unreadable>" in entry.getMessage()


def test_concurrent_first_requests_build_the_session_once(monkeypatch: pytest.MonkeyPatch) -> None:
    import threading
    import time

    from governed_bi.api import graph_app

    builds: list[int] = []

    def slow_build() -> Any:
        builds.append(1)
        time.sleep(0.05)
        graph_app._SESSION = SimpleNamespace(agent_model=None, fatal_problems=())
        return graph_app._SESSION

    monkeypatch.setattr(graph_app, "_SESSION", None)
    monkeypatch.setattr(graph_app, "_build_session_from_environment", slow_build)
    threads = [threading.Thread(target=graph_app.session_from_environment) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(builds) == 1
