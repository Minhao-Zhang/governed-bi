"""FastAPI HTTP interface over the governed serve flow + corpus/audit views.

A thin, **stateless** JSON API: read endpoints serialize the ``viz.presenter``
view models (schema, relationship graph, corpus assets, skills, health); ``/chat``
runs one turn through ``answer_question`` with working memory rebuilt from the
turns the caller sends. It is the interface a separate frontend (Next.js) consumes
— see ``docs/ui-frontend-design.md``.

Run it (needs the ``api`` extra) — the app is built by a factory, so there are no
import-time side effects (the stack is assembled only when the factory is called):

    uv run --extra api uvicorn --factory governed_bi.api:create_app --reload

Configure via env (see ``api.stack.build_stack``): ``GOVERNED_BI_CORPUS`` /
``GOVERNED_BI_DB`` / ``GOVERNED_BI_SQLITE``, and ``GOVERNED_BI_CORS_ORIGINS``
(comma-separated, default ``*``). Import stays free of FastAPI unless this module
is used, keeping the core install lean.
"""

from __future__ import annotations

import os

from .. import __version__
from ..viz import presenter
from .schemas import (
    AnswerResponse,
    AssetRowResponse,
    CapabilitiesResponse,
    ChatRequest,
    HealthResponse,
    SchemaGraphResponse,
    SkillResponse,
    TableResponse,
)
from .stack import ServeStack, build_stack


def create_app(stack: ServeStack | None = None):
    """Build the FastAPI app over a serve stack (built from env if not given)."""
    from fastapi import FastAPI, HTTPException, Query
    from fastapi.middleware.cors import CORSMiddleware

    stack = stack or build_stack()
    app = FastAPI(
        title="governed-bi API",
        version=__version__,
        summary="Governed NL2SQL serve flow + corpus/schema/audit, as JSON.",
    )

    origins = [o.strip() for o in os.environ.get("GOVERNED_BI_CORS_ORIGINS", "*").split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins or ["*"],
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )

    @app.get("/capabilities", response_model=CapabilitiesResponse, tags=["meta"])
    def capabilities() -> CapabilitiesResponse:
        """What this backend can do — the UI adapts its affordances to this."""
        return CapabilitiesResponse(
            environment=stack.settings.environment.value,
            dialect=stack.dialect,
            can_edit=False,  # editing is a later phase (env-aware: dev=file, prod=PR)
            edit_mode=None,
            model=stack.model_name,
            has_live_model=stack.has_live_model,
        )

    @app.get("/health", response_model=HealthResponse, tags=["audit"])
    def health() -> HealthResponse:
        """Corpus health: asset counts, CI status, and the triage flags."""
        return HealthResponse.model_validate(presenter.corpus_health(stack.corpus_full))

    @app.get("/schema", response_model=list[TableResponse], tags=["schema"])
    def schema() -> list[TableResponse]:
        """Every table with its columns (types, roles, governance flags)."""
        return [TableResponse.model_validate(t) for t in presenter.table_views(stack.corpus_full)]

    @app.get("/graph", response_model=SchemaGraphResponse, tags=["schema"])
    def graph() -> SchemaGraphResponse:
        """Table-relationship graph for the ER view (nodes + join edges)."""
        return SchemaGraphResponse.model_validate(presenter.schema_graph(stack.corpus_full))

    @app.get("/corpus/assets", response_model=list[AssetRowResponse], tags=["corpus"])
    def corpus_assets(
        asset_type: str | None = Query(None, alias="type"),
    ) -> list[AssetRowResponse]:
        """Non-table assets (metrics/terms/joins/rules/few-shots/negatives)."""
        types = {asset_type} if asset_type else None
        rows = presenter.asset_rows(stack.corpus_full, asset_types=types)
        return [AssetRowResponse.model_validate(r) for r in rows]

    @app.get("/skills", response_model=list[SkillResponse], tags=["corpus"])
    def skills() -> list[SkillResponse]:
        """Curated skills (rendered markdown bodies)."""
        return [SkillResponse.model_validate(s) for s in presenter.skill_views(stack.corpus_full)]

    @app.post("/chat", response_model=AnswerResponse, tags=["chat"])
    def chat(req: ChatRequest) -> AnswerResponse:
        """Answer one turn. Working memory is rebuilt from ``history`` (the API is
        stateless); the caller persists the transcript."""
        from ..gateway import Gateway, SqliteConnector
        from ..memory import InMemoryWorkingMemory
        from ..server import answer_question

        if not stack.sqlite_path.exists():
            raise HTTPException(status_code=503, detail=f"database unavailable: {stack.sqlite_path}")

        memory = InMemoryWorkingMemory()
        for turn in req.history:
            memory.append(req.session_id, turn.role, turn.text)

        connector = SqliteConnector(stack.sqlite_path)
        try:
            answer = answer_question(
                req.question,
                stack.identity,
                corpus=stack.corpus_server,
                gateway=Gateway(connector),
                settings=stack.settings,
                session_id=req.session_id,
                sql_generator=stack.generator,
                embedder=stack.embedder,
                narrator=stack.narrator,
                working_memory=memory,
            )
        finally:
            connector.close()
        return AnswerResponse.model_validate(presenter.answer_view(answer))

    return app
