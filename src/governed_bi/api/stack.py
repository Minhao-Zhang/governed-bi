"""Build the serve stack for the API from ``load_settings()``.

One place that assembles everything a request needs — corpus (full + analyst
view), settings, identity, the SQLite path, and the model stack (SQL generator +
embedder + narrator) — driven by project TOML (+ optional local overlay). The
model stack is live (LangChain, needs the ``agents`` extra + a key) when the
configured API key is set, else the deterministic offline default.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..config import DataSourceConfig, Settings, load_settings, resolve_corpus_root
from ..corpus import load_corpus
from ..gateway import Identity

if TYPE_CHECKING:
    from ..corpus import Corpus
    from ..gateway.connectors.base import Connector
    from ..llm import Embedder
    from ..analyst import AnswerNarrator

logger = logging.getLogger("governed_bi.api")


@dataclass(frozen=True)
class ServeStack:
    """Everything the API needs to answer + describe one deployment."""

    corpus_full: "Corpus"  # audit view (Facts + Inference + Audit, excluded shown)
    corpus_analyst: "Corpus"  # for_analyst() view (what SQL-gen may see)
    settings: "Settings"
    dialect: str
    sqlite_path: Path
    identity: Identity
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
    chat_model: Any | None = None  # raw LangChain BaseChatModel driving the agent core
    can_clarify: bool = False  # serve-time HITL (ask_user) is available (streaming + live model)
    clarify_checkpointer: Any | None = None  # inner-agent saver for interrupt/resume (in-mem, per process)
    conversation_checkpointer: Any | None = None  # durable outer-chat saver (ADR 0004 L3)

    def open_connector(self, *, connect_timeout: float | None = None) -> "Connector":
        """Open a fresh read-only connector for one request (caller closes it).

        Built from ``datasource`` (config-driven) so the serve path can target
        SQLite or a Postgres/Redshift instance without a code change. Falls back
        to the SQLite fixture path for ad-hoc stacks that set no datasource.

        ``connect_timeout`` (seconds) is forwarded to Postgres/Redshift dials;
        omit it to use the factory default (a short fail-fast timeout).
        """
        ds = self.datasource
        if ds is None or ds.kind == "sqlite":
            # sqlite: open the stack's path (kept in sync with the datasource), so
            # an ad-hoc ``replace(stack, sqlite_path=...)`` still selects the DB.
            # Pass the serving schema (the ATTACH alias); None falls back to the
            # file-name stem inside the connector.
            from ..gateway import SqliteConnector

            schema = ds.serving_schema() if ds is not None else None
            return SqliteConnector(self.sqlite_path, schema=schema)
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
                "Start the DB (e.g. docker compose for pg_rename_decoy) or set "
                "[datasource] kind = \"sqlite\" in governed_bi.toml / "
                "governed_bi.local.toml.",
                label,
                exc,
            )
            raise RuntimeError(f"datasource {label} unavailable: {exc}") from exc


def _build_model_stack(settings: Settings) -> tuple[Any, Any, str | None, bool, Any]:
    """(embedder, narrator, model_name, has_live_model, chat_model).

    Live LangChain clients when a key + the ``agents`` extra are present; else all
    ``None`` / ``has_live_model=False``. The agentic serve core needs a real
    model, so a no-model stack builds fine for the read-only audit API but cannot
    answer questions — the serve entry points fail closed (``make_graph`` raises
    at startup, ``/chat`` returns 503). ``chat_model`` is the raw LangChain model
    the agent core drives; the narrator wraps the same client.
    """
    if settings.models.api_key():
        try:
            from ..llm import LangChainChatClient, LangChainEmbedder
            from ..analyst import LlmAnswerNarrator

            models = settings.models
            chat = LangChainChatClient.from_config(models)
            return (
                LangChainEmbedder.from_config(models),
                LlmAnswerNarrator(chat),
                models.llm_model,
                True,
                chat.model,
            )
        except Exception:  # missing agents extra / bad config -> no-model stack
            # A key was set (live intended) but the stack failed to build; make the
            # silent downgrade observable rather than a mystery.
            logger.warning(
                "%s is set but the live model stack failed to build; "
                "serve will fail closed until it is fixed",
                settings.models.api_key_env,
                exc_info=True,
            )
    return (None, None, None, False, None)


def build_stack(settings: Settings | None = None) -> ServeStack:
    """Assemble the serve stack from :func:`load_settings` (or an explicit Settings).

    Corpus root, datasource, and serve flags all come from Settings — edit
    ``governed_bi.toml`` or ``governed_bi.local.toml``, not the environment.
    Secrets (API key, DSN) remain in the environment / ``.env``.
    """
    settings = settings if settings is not None else load_settings()
    datasource = settings.datasource
    root = resolve_corpus_root(settings.corpus_root)

    # Corpus: always every schema subtree under the root (D15).
    corpus_full = load_corpus(root)
    embedder, narrator, model_name, has_live, chat_model = _build_model_stack(settings)

    can_edit = settings.allow_edit

    # Serve-time HITL (ask_user -> interrupt) needs a checkpointer for the inner
    # agent to pause/resume; it is only reachable via the streaming chat graph
    # (graph_app), never the REST /chat path (no outer checkpointer there). The
    # saver is in-memory / per-process for v1 (durable Postgres is deferred to F7).
    clarify_checkpointer = None
    can_clarify = False
    if has_live:
        from langgraph.checkpoint.memory import InMemorySaver

        clarify_checkpointer = InMemorySaver()
        can_clarify = bool(settings.can_stream)

    # conversation_checkpointer is lazy — built only by build_standalone_chat_graph
    # (L3). Eager construction here opened a sqlite file on every build_stack /
    # test import while make_graph never used it.

    # Identity is a DEMO seam (D1: showcase, not a product). This repo serves a
    # single all-access identity; per-user identity + gateway RLS live in the private
    # enterprise fork (D7), not here. Make the ``single_all_access_identity`` toggle
    # honest instead of dead: if a deployment turns it off (the documented
    # "prod = real user + RLS" mode), FAIL LOUD rather than silently keep serving
    # all-access — that path is deliberately unimplemented in this demo.
    if not settings.single_all_access_identity:
        raise NotImplementedError(
            "per-user identity + gateway RLS is not implemented in this demo repo; it "
            "is deferred to the private enterprise fork (D1/D7). Set "
            "[runtime].single_all_access_identity = true for the showcase, or run the "
            "enterprise fork for real per-user identity scoping."
        )

    # Resolve sqlite_path against the repo root when relative, matching
    # build_connector, so the stack's path and the probe agree.
    sqlite_path = Path(datasource.sqlite_path)
    if not sqlite_path.is_absolute():
        from ..config import _repo_root

        sqlite_path = _repo_root() / sqlite_path

    stack = ServeStack(
        corpus_full=corpus_full,
        corpus_analyst=corpus_full.for_analyst(),
        settings=settings,
        dialect=datasource.kind,  # sqlite | postgres | redshift (matches the Dialect enum)
        sqlite_path=sqlite_path,
        identity=Identity(user="demo", all_access=True),  # demo seam (see the guard above)
        embedder=embedder,
        narrator=narrator,
        model_name=model_name,
        has_live_model=has_live,
        corpus_root=root,
        can_stream=settings.can_stream,
        can_scope=True,  # the summary/detail/scoping schema routes are always served
        can_search=False,  # no server-side FTS; the client Fuse index is the default
        can_edit=can_edit,
        edit_mode="file" if can_edit else None,
        datasource=datasource,
        chat_model=chat_model,
        can_clarify=can_clarify,
        clarify_checkpointer=clarify_checkpointer,
        conversation_checkpointer=None,
    )
    # Fail fast when TOML points at Postgres/Redshift that isn't up (or a missing
    # SQLite file). Without this, the first chat turn hangs on TCP connect.
    stack.verify_datasource()
    return stack
