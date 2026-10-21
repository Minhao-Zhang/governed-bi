"""Custom REST routes mounted by ``langgraph.json``'s ``http.app`` (ADR 0007).

Four reads: ``/livez``, ``/readyz``, ``/capabilities`` and ``/clarifications/pending``. A turn
is served by the graph the platform streams, not by this app. :func:`make_app` takes the session
and the pending-clarification reader; :func:`app_from_environment` is the only place that
resolves the environment, and it does so on the first request so importing this module needs no
database.

Nothing here asks for a credential. One operator on loopback is the deployment, so the
port is the boundary. ``api/auth.py`` still refuses a client-supplied state write.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from typing import Any

from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse

from governed_bi.model.provider import reasoning_effort_of
from governed_bi.paths import REPO_ROOT

__all__ = ["make_app", "app_from_environment", "app"]


# ── the seam ─────────────────────────────────────────────────────────────────


def make_app(session: Any, pending: Any) -> FastAPI:
    """An app over the session and the pending-clarification reader.

    Both are required. A default would put the environment back in the constructor.
    """
    return _build_app(lambda: session, pending)


def app_from_environment() -> FastAPI:
    """The process entry's adapter: the same app, over dependencies resolved on first request.

    Lazy on purpose. ``langgraph.json`` points ``http.app`` at the module attribute below, so
    this runs at import, and ``session_from_environment`` builds a Postgres connector and seeds
    a corpus. Resolving it here would make importing this module require a database.
    """
    from governed_bi.api.graph_app import configure_logging, session_from_environment
    from governed_bi.api.thread_turns import PendingClarifications

    configure_logging()
    return _build_app(session_from_environment, PendingClarifications())


def _build_app(get_session: Callable[[], Any], pending: Any) -> FastAPI:
    """Assemble the app from a session thunk and a pending-question reader.

    A thunk rather than a value, so :func:`make_app` can hand over a concrete object and
    :func:`app_from_environment` can defer.
    """
    app = FastAPI(title="governed-bi", version="2")

    @app.get("/livez")
    def livez() -> dict[str, Any]:
        """Liveness only — does not touch the session."""
        return {"ok": True}

    @app.get("/readyz", response_model=None)
    def readyz() -> dict[str, Any] | JSONResponse:
        """Ready when the session resolves with no fatal corpus problem; 503 if not.

        Resolving the session is the check. The reason carries our own ``RuntimeError`` text,
        which names variables, and only the type of anything else, which may hold a DSN. No model
        at all is a supported mode (``has_live_model: false``), so it is ready.
        """
        try:
            session = get_session()
        except RuntimeError as err:
            return _not_ready(str(err))
        except Exception as err:  # noqa: BLE001 — any failure to build the session is not-ready
            return _not_ready(type(err).__name__)
        fatal = getattr(session, "fatal_problems", ())
        if fatal:
            return _not_ready(f"{len(fatal)} fatal corpus problem(s)")
        return {"ready": True}

    @app.get("/capabilities")
    def capabilities() -> dict[str, Any]:
        """What this server can actually do. The UI blocks on this response."""
        return capabilities_for(get_session())

    @app.get("/clarifications/pending")
    def pending_clarifications(
        limit: int = Query(50, ge=1, le=500),
        offset: int = Query(0, ge=0),
    ) -> dict[str, Any]:
        """Questions the engine asked and nobody has answered, oldest first.

        Interrupt state only. A paused clarification writes nothing until it is
        resumed, so the queue is the platform's interrupted threads.
        """
        page = pending.pending(limit=limit, offset=offset)
        return {
            "rows": list(page.rows),
            "meta": {
                "n": len(page.rows),
                "truncated": bool(page.truncated),
                "threads_scanned": int(page.threads_scanned),
                "limit": limit,
                "offset": offset,
                "columns": list(pending.PENDING_FIELDS),
            },
        }

    return app


# ── projections: a function of the session, testable without an app ──────────


def models_for(session: Any) -> dict[str, Any]:
    """The three model surfaces this run resolved, read from ``knobs_resolved``.

    That mapping is what every measurement row publishes, so the screen and the artifact cannot
    disagree. ``embedding.id`` is reported verbatim: it is provider-qualified and may contain a
    colon. ``utility.effort`` is observed off the live client because no knob records it.
    """
    knobs = session.knobs_resolved
    utility = getattr(session, "utility_model", None)
    return {
        "agent": {
            "id": knobs.get("chat_model"),
            "provider": _gateway(knobs.get("chat_model")),
            "effort": knobs.get("llm_reasoning_effort"),
        },
        "utility": {
            "id": knobs.get("llm_utility_model"),
            "provider": _gateway(knobs.get("llm_utility_model")),
            "effort": reasoning_effort_of(utility) if utility is not None else None,
        },
        "embedding": {
            "id": knobs.get("embedding_model"),
            "provider": _gateway(knobs.get("embedding_model")),
            "dimensions": knobs.get("embedding_dimensions"),
        },
    }


def _gateway(model_id: Any) -> str | None:
    """The gateway a model id names. A qualified id uses its prefix; a bare id is OpenAI."""
    if not isinstance(model_id, str) or not model_id.strip():
        return None
    prefix, separator, rest = model_id.partition(":")
    if separator and prefix.strip() and rest.strip():
        return prefix.strip()
    return "openai"


def connection_for(session: Any) -> dict[str, Any]:
    """Which warehouse this engine is pointed at. **Credential-free by construction.**

    The redaction is the connector's (``datasource/postgres.py::endpoint``). Here the only job is
    to tolerate a session or test double with no ``endpoint``, or one that is not a mapping.
    """
    out: dict[str, Any] = {"dialect": getattr(session.connector, "dialect", "postgres")}
    endpoint = getattr(session.connector, "endpoint", None)
    if isinstance(endpoint, Mapping):
        out.update({str(k): v for k, v in endpoint.items()})
    return out


def durable_checkpointer_configured() -> bool:
    """Whether ``langgraph.json`` declares a checkpointer, and its module is on disk.

    The platform injects the saver into the graphs it runs, and this app never holds one, so the
    file that decides it is what can be read. It says nothing about whether the saver is open.
    """
    try:
        config = json.loads((REPO_ROOT / "langgraph.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    declared = str((config.get("checkpointer") or {}).get("path") or "")
    module, _, factory = declared.rpartition(":")
    return bool(module and factory) and (REPO_ROOT / module).is_file()


def served_graph_declared() -> bool:
    """Whether ``langgraph.json`` declares the streamed graph, and its module is on disk.

    Read off the file for the same reason as :func:`durable_checkpointer_configured`. It cannot
    see that the graph imports, but then nothing answers ``/capabilities`` either.
    """
    try:
        config = json.loads((REPO_ROOT / "langgraph.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    declared = str((config.get("graphs") or {}).get("serve") or "")
    module, _, factory = declared.rpartition(":")
    return bool(module and factory) and (REPO_ROOT / module).is_file()


def _not_ready(reason: str) -> JSONResponse:
    return JSONResponse(status_code=503, content={"ready": False, "reason": reason})


def capabilities_for(session: Any) -> dict[str, Any]:
    """``/capabilities``' body. Every field is an observation (ADR 0007 §7)."""
    can_stream = served_graph_declared()
    durable = durable_checkpointer_configured()
    return {
        "environment": "local",
        "dialect": getattr(session.connector, "dialect", "postgres"),
        "can_edit": False,
        "edit_mode": "none",
        "can_stream": can_stream,
        "has_live_model": session.agent_model is not None,
        "model": session.knobs_resolved.get("chat_model"),
        "models": models_for(session),
        "connection": connection_for(session),
        # The flag mounts the interrupt prompt, so it must not be true without a model.
        "can_clarify": can_stream and session.agent_model is not None,
        "checkpoint_durable": durable,
        # A paused `ask_user` interrupt is checkpoint state, so this is the same observation.
        "hitl_survives_process_restart": durable,
    }


#: What ``langgraph.json``'s ``http.app`` points at. An attribute, not a factory — the platform
#: imports the name — so the environment adapter is what builds it.
app = app_from_environment()
