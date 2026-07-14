"""Build the serve stack for the API from ``load_settings()``.

One place that assembles everything a request needs — corpus (full + server
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
    from ..server import AnswerNarrator

logger = logging.getLogger("governed_bi.api")


@dataclass(frozen=True)
class ServeStack:
    """Everything the API needs to answer + describe one deployment."""

    corpus_full: "Corpus"  # audit view (Facts + Inference + Audit, excluded shown)
    corpus_server: "Corpus"  # for_server() view (what SQL-gen may see)
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
            from ..server import LlmAnswerNarrator

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
    )
    # Fail fast when TOML points at Postgres/Redshift that isn't up (or a missing
    # SQLite file). Without this, the first chat turn hangs on TCP connect.
    stack.verify_datasource()
    return stack
