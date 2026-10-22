"""Reads only (ADR 0007). The environment resolves on first request, so import needs no database.
No credential; loopback is the boundary.
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


def make_app(session: Any, pending: Any) -> FastAPI:
    """Both arguments are required. A default would put the environment back in the constructor."""
    return _build_app(lambda: session, pending)


def app_from_environment() -> FastAPI:
    """Resolve on first request. ``http.app`` is imported by name, and a session build needs Postgres."""
    from governed_bi.api.graph_app import configure_logging, session_from_environment
    from governed_bi.api.thread_turns import PendingClarifications

    configure_logging()
    return _build_app(session_from_environment, PendingClarifications())


def _build_app(get_session: Callable[[], Any], pending: Any) -> FastAPI:
    """A thunk, so :func:`make_app` can pass a session and :func:`app_from_environment` can defer."""
    app = FastAPI(title="governed-bi", version="2")

    @app.get("/livez")
    def livez() -> dict[str, Any]:
        """Liveness only — does not touch the session."""
        return {"ok": True}

    @app.get("/readyz", response_model=None)
    def readyz() -> dict[str, Any] | JSONResponse:
        """Our ``RuntimeError`` text is returned; any other error contributes only its type, which can hide a DSN.
        No model is a supported ready state."""
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
        """The UI blocks on this response, so a missing field is a blank screen, not a later error."""
        return capabilities_for(get_session())

    @app.get("/clarifications/pending")
    def pending_clarifications(
        limit: int = Query(50, ge=1, le=500),
        offset: int = Query(0, ge=0),
    ) -> dict[str, Any]:
        """Oldest first. A paused clarification writes nothing, so the queue is interrupt state."""
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


def models_for(session: Any) -> dict[str, Any]:
    """From ``settings_resolved``, which measurement rows publish. ``embedding.id`` stays verbatim.
    ``utility.effort`` comes off the live client: no setting records it."""
    settings = session.settings_resolved
    utility = getattr(session, "utility_model", None)
    return {
        "agent": {
            "id": settings.get("chat_model"),
            "provider": _gateway(settings.get("chat_model")),
            "effort": settings.get("llm_reasoning_effort"),
        },
        "utility": {
            "id": settings.get("llm_utility_model"),
            "provider": _gateway(settings.get("llm_utility_model")),
            "effort": reasoning_effort_of(utility) if utility is not None else None,
        },
        "embedding": {
            "id": settings.get("embedding_model"),
            "provider": _gateway(settings.get("embedding_model")),
            "dimensions": settings.get("embedding_dimensions"),
        },
    }


def _gateway(model_id: Any) -> str | None:
    """A qualified id uses its prefix. A bare id is OpenAI."""
    if not isinstance(model_id, str) or not model_id.strip():
        return None
    prefix, separator, rest = model_id.partition(":")
    if separator and prefix.strip() and rest.strip():
        return prefix.strip()
    return "openai"


def connection_for(session: Any) -> dict[str, Any]:
    """The connector redacts the credential. A missing or non-mapping ``endpoint`` is tolerated."""
    out: dict[str, Any] = {"dialect": getattr(session.connector, "dialect", "postgres")}
    endpoint = getattr(session.connector, "endpoint", None)
    if isinstance(endpoint, Mapping):
        out.update({str(k): v for k, v in endpoint.items()})
    return out


def durable_checkpointer_configured() -> bool:
    """Read ``langgraph.json``: this app never holds the saver the platform injects."""
    try:
        config = json.loads((REPO_ROOT / "langgraph.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    declared = str((config.get("checkpointer") or {}).get("path") or "")
    module, _, factory = declared.rpartition(":")
    return bool(module and factory) and (REPO_ROOT / module).is_file()


def served_graph_declared() -> bool:
    """Read ``langgraph.json``, same as :func:`durable_checkpointer_configured`.
    Import failure means this route is down too."""
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
    """Every field is an observation (ADR 0007)."""
    can_stream = served_graph_declared()
    durable = durable_checkpointer_configured()
    return {
        "environment": "local",
        "dialect": getattr(session.connector, "dialect", "postgres"),
        "can_edit": False,
        "edit_mode": "none",
        "can_stream": can_stream,
        "has_live_model": session.agent_model is not None,
        "model": session.settings_resolved.get("chat_model"),
        "models": models_for(session),
        "connection": connection_for(session),
        # The flag mounts the interrupt prompt, so it must not be true without a model.
        "can_clarify": can_stream and session.agent_model is not None,
        "checkpoint_durable": durable,
        # A paused `ask_user` interrupt is checkpoint state, so this is the same observation.
        "hitl_survives_process_restart": durable,
    }


#: An attribute, not a factory: the platform imports the name.
app = app_from_environment()
