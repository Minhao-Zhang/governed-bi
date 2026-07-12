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

import logging
import os

from .. import __version__
from ..viz import presenter
from .schemas import (
    AnswerResponse,
    AssetRowResponse,
    AssetTypeFilter,
    CapabilitiesResponse,
    ChatRequest,
    EditRequest,
    EditResponse,
    HealthResponse,
    KnowledgeGraphResponse,
    SchemaGraphResponse,
    SchemaSummaryResponse,
    SkillResponse,
    TableResponse,
    TableSummaryResponse,
)
from .stack import ServeStack, build_stack

logger = logging.getLogger("governed_bi.api")


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

    # CORS: default to the local Next.js dev origin so the separate frontend works
    # out of the box, without a blanket wildcard. Override with
    # GOVERNED_BI_CORS_ORIGINS (comma-separated; "*" to allow any origin); set it
    # empty to disable CORS entirely (same-origin only).
    origins = [
        o.strip()
        for o in os.environ.get("GOVERNED_BI_CORS_ORIGINS", "http://localhost:3000").split(",")
        if o.strip()
    ]
    if origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=["*"],
        )

    @app.get("/capabilities", response_model=CapabilitiesResponse, tags=["meta"])
    def capabilities() -> CapabilitiesResponse:
        """What this backend can do — the UI adapts its affordances to this."""
        return CapabilitiesResponse(
            environment=stack.settings.environment.value,
            dialect=stack.dialect,
            can_edit=stack.can_edit,  # dev file-write; prod PR is deferred
            edit_mode=stack.edit_mode,  # "file" | "pr" | null
            model=stack.model_name,
            has_live_model=stack.has_live_model,
            # Streaming is served by the LangGraph chat graph, not this REST app; the
            # flag lets the UI pick the streaming path when that server is in front.
            can_stream=stack.can_stream,
            # Additive scoping affordances: the summary/detail routes are served
            # (can_scope), but there is no server-side FTS (can_search) — the UI
            # builds its own client-side (Fuse) search index from /schema/summary.
            can_scope=stack.can_scope,
            can_search=stack.can_search,
        )

    @app.get("/", include_in_schema=False)
    def root() -> dict:
        return {"name": "governed-bi API", "version": __version__, "docs": "/docs"}

    @app.get("/livez", tags=["meta"])
    def livez() -> dict:
        """Process liveness (no corpus work). Use /health for corpus status."""
        return {"status": "ok"}

    @app.get("/health", response_model=HealthResponse, tags=["audit"])
    def health() -> HealthResponse:
        """Corpus health: asset counts, CI status, and the triage flags."""
        return HealthResponse.model_validate(presenter.corpus_health(stack.corpus_full))

    @app.get("/schema", response_model=list[TableResponse], tags=["schema"])
    def schema(
        db: str | None = Query(None),
        limit: int | None = Query(None, ge=0),
        offset: int = Query(0, ge=0),
    ) -> list[TableResponse]:
        """Every table with its columns (types, roles, governance flags).

        Param-less this is the full dump (backward-compatible). ``db`` filters to
        one namespace; ``limit``/``offset`` paginate (default: all rows, offset 0).
        """
        views = presenter.table_views(stack.corpus_full)
        if db is not None:
            views = [v for v in views if v.db == db]
        page = views[offset:] if limit is None else views[offset : offset + limit]
        return [TableResponse.model_validate(t) for t in page]

    @app.get("/schema/summary", response_model=SchemaSummaryResponse, tags=["schema"])
    def schema_summary(
        db: str | None = Query(None),
        limit: int | None = Query(None, ge=0),
        offset: int = Query(0, ge=0),
    ) -> SchemaSummaryResponse:
        """Lean catalog for the virtualized table list + the client search index.

        Heavy fields (sample_values, evidence, description) are dropped; fetch full
        detail lazily via ``/schema/{table_id}``. ``db`` filters to one namespace;
        ``limit``/``offset`` paginate (default: all rows, offset 0); ``total`` is
        the count BEFORE pagination.
        """
        summaries = presenter.table_summaries(stack.corpus_full, db=db)
        total = len(summaries)
        page = summaries[offset:] if limit is None else summaries[offset : offset + limit]
        return SchemaSummaryResponse(
            total=total,
            items=[TableSummaryResponse.model_validate(s) for s in page],
        )

    @app.get("/schema/{table_id}", response_model=TableResponse, tags=["schema"])
    def schema_table(table_id: str) -> TableResponse:
        """Full detail for one table by asset id (404 when the id is unknown)."""
        view = presenter.table_view_by_id(stack.corpus_full, table_id)
        if view is None:
            raise HTTPException(status_code=404, detail="unknown table id")
        return TableResponse.model_validate(view)

    @app.get("/graph", response_model=SchemaGraphResponse, tags=["schema"])
    def graph() -> SchemaGraphResponse:
        """Table-relationship graph for the ER view (nodes + join edges)."""
        return SchemaGraphResponse.model_validate(presenter.schema_graph(stack.corpus_full))

    @app.get("/knowledge-graph", response_model=KnowledgeGraphResponse, tags=["schema"])
    def knowledge_graph() -> KnowledgeGraphResponse:
        """Full corpus knowledge graph: every asset a node, typed relationships as
        edges. Filter/layer by ``node.kind`` (e.g. tables + joins for the ER view)."""
        return KnowledgeGraphResponse.model_validate(presenter.knowledge_graph(stack.corpus_full))

    @app.get("/corpus/assets", response_model=list[AssetRowResponse], tags=["corpus"])
    def corpus_assets(
        asset_type: AssetTypeFilter | None = Query(None, alias="type"),
    ) -> list[AssetRowResponse]:
        """Non-table assets (metrics/terms/joins/rules/few-shots/negatives)."""
        types = {asset_type} if asset_type else None
        rows = presenter.asset_rows(stack.corpus_full, asset_types=types)
        return [AssetRowResponse.model_validate(r) for r in rows]

    @app.get("/skills", response_model=list[SkillResponse], tags=["corpus"])
    def skills() -> list[SkillResponse]:
        """Curated skills (rendered markdown bodies)."""
        return [SkillResponse.model_validate(s) for s in presenter.skill_views(stack.corpus_full)]

    @app.post("/corpus/edit", response_model=EditResponse, tags=["corpus"])
    def corpus_edit(req: EditRequest) -> EditResponse:
        """Validate a corpus asset and, in dev, write it to the YAML tree.

        Gated on ``capabilities.can_edit`` (403 otherwise). The asset is schema-
        validated (422 on a bad shape) then reference-checked against the rest of
        the corpus; findings block the write and are returned with the diff so the
        editor can fix them. Prod PR mode is deferred; the request shape is stable.
        """
        import difflib

        from pydantic import ValidationError

        from ..corpus import (
            dump_asset,
            is_valid_id,
            load_corpus,
            parse_asset,
            subdir_for_type,
            validate_corpus,
            write_corpus,
        )

        if not stack.can_edit:
            raise HTTPException(status_code=403, detail="corpus editing is not enabled")

        try:
            asset = parse_asset(req.asset)
        except ValidationError as exc:
            raise HTTPException(
                status_code=422, detail=f"invalid asset: {exc.error_count()} validation error(s)"
            ) from exc

        # Enforce the id convention BEFORE any filesystem access: the id becomes a
        # filename, and a loose id would let the canonical-path lookup below read an
        # unintended file. is_valid_id also rejects path separators (the regex has
        # no '/' or '\'), so this subsumes the traversal guard.
        if not is_valid_id(asset.asset_type, asset.id):
            raise HTTPException(
                status_code=422, detail=f"asset id does not match the {asset.asset_type} convention"
            )

        # Reference-integrity check against the CURRENT on-disk corpus (reloaded, not
        # the startup snapshot), so a sequence of edits in one process cannot persist
        # a corpus that breaks integrity, and external edits are seen too.
        try:
            current = load_corpus(stack.corpus_root, db=stack.db)
            existing_assets = list(current.assets)
        except FileNotFoundError:
            existing_assets = []  # empty/new corpus tree: this asset is the first
        merged = [a for a in existing_assets if a.id != asset.id]
        merged.append(asset)
        findings = [str(f) for f in validate_corpus(merged)]

        # Canonical path only (no recursive glob): the asset's own file, never an
        # arbitrary *.yaml elsewhere under the tree.
        target = stack.corpus_root / stack.db / subdir_for_type(asset.asset_type) / f"{asset.id}.yaml"
        old_text = target.read_text(encoding="utf-8") if target.exists() else ""
        new_text = dump_asset(asset)
        diff = "".join(
            difflib.unified_diff(
                old_text.splitlines(keepends=True),
                new_text.splitlines(keepends=True),
                fromfile=f"a/{asset.id}.yaml",
                tofile=f"b/{asset.id}.yaml",
            )
        )

        if findings:  # fail closed: never write a corpus that breaks reference integrity
            return EditResponse(
                written=False,
                asset_id=asset.id,
                asset_type=asset.asset_type,
                path=None,
                findings=findings,
                diff=diff,
            )

        try:
            written = write_corpus(stack.corpus_root, stack.db, [asset])
        except OSError:
            logger.exception("corpus edit write failed (asset=%s)", asset.id)
            raise HTTPException(status_code=500, detail="failed to write the asset")
        return EditResponse(
            written=True,
            asset_id=asset.id,
            asset_type=asset.asset_type,
            path=str(written[0].relative_to(stack.corpus_root).as_posix()),
            findings=[],
            diff=diff,
        )

    @app.post("/chat", response_model=AnswerResponse, tags=["chat"])
    def chat(req: ChatRequest) -> AnswerResponse:
        """Answer one turn. Working memory is rebuilt from ``history`` (the API is
        stateless); the caller persists the transcript."""
        from ..gateway import Gateway
        from ..memory import InMemoryWorkingMemory
        from ..server import answer_question

        memory = InMemoryWorkingMemory()
        for turn in req.history:
            memory.append(req.session_id, turn.role, turn.text)

        try:
            connector = stack.open_connector()  # config-driven: SQLite or Postgres/Redshift
        except Exception:
            # Log server-side (may include a path/DSN); never leak it to clients.
            logger.exception("data source unavailable")
            raise HTTPException(status_code=503, detail="database unavailable")
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
        except Exception:
            # The serve flow is read-only and guardrailed by construction; a raise
            # here is model/IO failure at its edges (embed / generate). Contain it:
            # log server-side, return a clean error, never a traceback.
            logger.exception("chat turn failed (session=%s)", req.session_id)
            raise HTTPException(status_code=500, detail="failed to answer the question")
        finally:
            connector.close()
        return AnswerResponse.model_validate(presenter.answer_view(answer))

    return app
