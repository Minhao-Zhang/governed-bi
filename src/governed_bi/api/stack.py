"""Build the serve stack for the API from configuration/environment.

One place that assembles everything a request needs — corpus (full + server
view), settings, identity, the SQLite path, and the model stack (SQL generator +
embedder + narrator) — driven by config, so the same API binary runs the three
profiles (local-dev / public-demo / internal) by configuration alone. The model
stack is live (LangChain, needs the ``agents`` extra + a key) when
``OPENAI_API_KEY`` is set, else the deterministic offline default (template SQL,
no narration), the same live-vs-offline split the engine uses elsewhere.

Near-term the data source is SQLite (the committed fixture). The connector seam
is where a Postgres/Redshift profile plugs in later; nothing else here changes.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..config import DataSourceConfig, load_settings, resolve_corpus_root
from ..corpus import load_corpus
from ..gateway import Identity

if TYPE_CHECKING:
    from ..config import Settings
    from ..corpus import Corpus
    from ..gateway.connectors.base import Connector
    from ..llm import Embedder
    from ..server import AnswerNarrator
    from ..server.sqlgen import SqlGenerator

logger = logging.getLogger("governed_bi.api")


def _env_flag(name: str, default: bool) -> bool:
    """Read a boolean env override; ``default`` when unset."""
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class ServeStack:
    """Everything the API needs to answer + describe one deployment."""

    corpus_full: "Corpus"  # audit view (Facts + Inference + Audit, excluded shown)
    corpus_server: "Corpus"  # for_server() view (what SQL-gen may see)
    settings: "Settings"
    dialect: str
    sqlite_path: Path
    identity: Identity
    generator: "SqlGenerator | None"
    embedder: "Embedder | None"
    narrator: "AnswerNarrator | None"
    model_name: str | None
    has_live_model: bool
    # Capability flags + corpus location (defaults keep ad-hoc construction simple).
    corpus_root: Path = Path("corpus")  # where the editable YAML tree lives
    can_stream: bool = False  # a streaming chat graph is reachable (LangGraph server)
    can_scope: bool = True  # the summary/detail/scoping schema routes are served
    can_search: bool = False  # a server-side FTS endpoint exists (False: client Fuse)
    can_edit: bool = False  # corpus editing is exposed (dev file-write)
    edit_mode: str | None = None  # "file" (dev) | "pr" (prod, deferred) | None
    datasource: DataSourceConfig | None = None  # which DB the serve path executes against

    def open_connector(self, *, connect_timeout: float | None = None) -> "Connector":
        """Open a fresh read-only connector for one request (caller closes it).

        Built from ``datasource`` (config-driven, D-datasource) so the serve path
        can target SQLite or a Postgres/Redshift instance without a code change.
        Falls back to the SQLite fixture path for ad-hoc stacks that set no
        datasource (e.g. some tests).

        ``connect_timeout`` (seconds) is forwarded to Postgres/Redshift dials;
        omit it to use the factory default (a short fail-fast timeout).
        """
        ds = self.datasource
        if ds is None or ds.kind == "sqlite":
            # sqlite: open the stack's path (kept in sync with the datasource), so
            # an ad-hoc ``replace(stack, sqlite_path=...)`` still selects the DB.
            from ..gateway import SqliteConnector

            return SqliteConnector(self.sqlite_path)
        from ..gateway import build_connector

        if connect_timeout is None:
            return build_connector(ds)
        return build_connector(ds, connect_timeout=connect_timeout)

    def verify_datasource(self, *, connect_timeout: float = 5.0) -> None:
        """Probe the configured DB; raise if unreachable.

        Opens a connector, runs a cheap catalog call, and closes it. Intended for
        startup so a down Postgres (docker not running) fails immediately with a
        clear log instead of hanging on the first ``/chat`` / LangGraph turn.
        """
        ds = self.datasource
        kind = (ds.kind if ds is not None else "sqlite").lower()
        if ds is not None and ds.dsn_env:
            label = f"{kind} (via ${ds.dsn_env})"
        elif ds is not None and kind == "sqlite":
            label = f"sqlite ({self.sqlite_path})"
        else:
            label = kind
        try:
            connector = self.open_connector(connect_timeout=connect_timeout)
            try:
                connector.list_schemas()
            finally:
                connector.close()
        except Exception as exc:
            logger.error(
                "Datasource %s unreachable at startup: %s. "
                "Start the DB (e.g. docker compose for pg_rename_decoy) or switch "
                "back to the SQLite fixture (unset GOVERNED_BI_DB_KIND / kind=sqlite).",
                label,
                exc,
            )
            raise RuntimeError(f"datasource {label} unavailable: {exc}") from exc


def _build_model_stack(settings) -> tuple[Any, Any, Any, str | None, bool]:
    """(generator, embedder, narrator, model_name, has_live_model).

    Live LangChain clients when a key + the ``agents`` extra are present; else the
    deterministic offline default (template generator, no embedder/narrator).
    """
    if os.environ.get("OPENAI_API_KEY"):
        try:
            from ..llm import LangChainChatClient, LangChainEmbedder
            from ..server import LlmAnswerNarrator, LlmSqlGenerator

            models = settings.models
            ds = settings.datasource
            chat = LangChainChatClient.from_config(models)
            return (
                LlmSqlGenerator(
                    chat,
                    dialect=ds.kind,
                    multi_schema=ds.is_multi_schema(),
                ),
                LangChainEmbedder.from_config(models),
                LlmAnswerNarrator(chat),
                models.llm_model,
                True,
            )
        except Exception:  # missing agents extra / bad config -> offline fallback
            # A key was set (live intended) but the stack failed to build; make the
            # silent downgrade to offline observable rather than a mystery.
            logger.warning(
                "OPENAI_API_KEY is set but the live model stack failed to build; "
                "falling back to the offline profile",
                exc_info=True,
            )
    return (None, None, None, None, False)


def build_stack() -> ServeStack:
    """Assemble the serve stack from environment + ``governed_bi.toml``.

    Env overrides (all optional): ``GOVERNED_BI_CORPUS`` (corpus root; a relative
    value resolves against the repo root, so the D13 corpus repo is reachable as
    ``../BIRD-corpus``); and the data source: ``GOVERNED_BI_DB_KIND``
    (sqlite|postgres|redshift), ``GOVERNED_BI_DB_DSN`` / ``GOVERNED_BI_DB_DSN_ENV``
    (the libpq DSN or the env var holding it), and ``GOVERNED_BI_SQLITE`` (the
    SQLite file for the sqlite kind). The corpus always loads every schema
    subtree under the root. Postgres/Redshift default to multi-schema (D15); no
    schema pin is required. The committed default is the SQLite fixture; a local
    ``.env`` points the server at Postgres.
    """
    root = resolve_corpus_root()
    settings = load_settings()

    # Data source: which DB the serve path executes against. Start from the
    # [datasource] config (committed default = the SQLite fixture, so tests/offline
    # are unchanged) and layer env overrides on top, so a local .env can point the
    # running server at Postgres/Redshift without editing the committed config.
    # Postgres/Redshift span all user schemas by default (D15); pin with
    # ``multi_schema = false`` + ``schema = "..."`` in toml when needed.
    base_ds = settings.datasource
    datasource = replace(
        base_ds,
        kind=os.environ.get("GOVERNED_BI_DB_KIND", base_ds.kind),
        dsn=os.environ.get("GOVERNED_BI_DB_DSN", base_ds.dsn),
        dsn_env=os.environ.get("GOVERNED_BI_DB_DSN_ENV", base_ds.dsn_env),
        sqlite_path=os.environ.get("GOVERNED_BI_SQLITE", base_ds.sqlite_path),
    )
    # Keep settings.datasource in sync with env overrides so the serve path
    # (flow/graph) reads the effective kind / multi_schema flag.
    settings = replace(settings, datasource=datasource)

    # Corpus: always every schema subtree under the root (D15 — no env pin).
    corpus_full = load_corpus(root)
    generator, embedder, narrator, model_name, has_live = _build_model_stack(settings)

    # Capability flags the frontend adapts to. can_stream defaults False: this
    # shared factory also builds the plain REST app, which has no streaming
    # endpoint, so streaming is opted in by whoever actually fronts the chat graph
    # (routes.py, mounted on the LangGraph server) or by the env override. Editing
    # is dev-only file-write (prod PR is deferred).
    can_stream = _env_flag("GOVERNED_BI_CAN_STREAM", False)
    can_edit = _env_flag("GOVERNED_BI_ALLOW_EDIT", settings.environment.value == "dev")

    # Resolve sqlite_path against the repo root when relative, matching
    # build_connector, so the stack's path and the probe agree.
    sqlite_path = Path(datasource.sqlite_path)
    if not sqlite_path.is_absolute():
        from ..config import _repo_root

        sqlite_path = _repo_root() / sqlite_path

    stack = ServeStack(
        corpus_full=corpus_full,
        corpus_server=corpus_full.for_server(),
        settings=settings,
        dialect=datasource.kind,  # sqlite | postgres | redshift (matches the Dialect enum)
        sqlite_path=sqlite_path,
        identity=Identity(user="demo", all_access=True),
        generator=generator,
        embedder=embedder,
        narrator=narrator,
        model_name=model_name,
        has_live_model=has_live,
        corpus_root=root,
        can_stream=can_stream,
        can_scope=True,  # the summary/detail/scoping schema routes are always served
        can_search=False,  # no server-side FTS; the client Fuse index is the default
        can_edit=can_edit,
        edit_mode="file" if can_edit else None,
        datasource=datasource,
    )
    # Fail fast when .env points at Postgres/Redshift that isn't up (or a missing
    # SQLite file). Without this, the first chat turn hangs on TCP connect.
    stack.verify_datasource()
    return stack
