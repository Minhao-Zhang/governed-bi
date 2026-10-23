"""Factory ``langgraph.json`` loads (ADR 0007). The session is closed over: configurable is JSON only.
``accept`` derives the turn server-side; client provenance is ignored.
"""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from governed_bi.model.provider import SURFACE_MODEL_VARS
from governed_bi.paths import REPO_ROOT
from governed_bi.serve.accept import accept_node
from governed_bi.serve.graph import build_graph
from governed_bi.serve.runtime import trust
from governed_bi.serve.session import Session
from governed_bi.serve.state import TurnEntry

_log = logging.getLogger(__name__)

__all__ = [
    "configure_logging",
    "CORPORA_DIR",
    "CORPUS_DIR_VAR",
    "MODEL_EFFORT_VAR",
    "MODEL_VAR",
    "SCHEMA_VAR",
    "SEED_DIR_VAR",
    "TIMEOUT_VAR",
    "UTILITY_MODEL_VAR",
    "build_serve_graph",
    "make_graph",
    "record_node",
    "serve_policy",
    "session_from_environment",
]

#: Schema to serve. Changing corpus requires a restart.
SCHEMA_VAR = "GOVERNED_BI_SCHEMA"

#: Curated corpus on disk; takes precedence over live-schema seeding.
CORPUS_DIR_VAR = "GOVERNED_BI_CORPUS_DIR"

#: Shared with ``serve/__main__.py``, which cannot import this module. Absent means no model.
MODEL_VAR = SURFACE_MODEL_VARS["agent"]

#: Model for guard + channel rewriters. Unset → share :data:`MODEL_VAR`.
UTILITY_MODEL_VAR = SURFACE_MODEL_VARS["utility"]

UTILITY_MODEL_EFFORT_VAR = "GOVERNED_BI_UTILITY_MODEL_EFFORT"

#: Embedding model id. Setting it turns the semantic channel on.
EMBEDDING_MODEL_VAR = SURFACE_MODEL_VARS["embedding"]

#: One retry count for the agent, the utility model, and the embedder.
RETRIES_VAR = "GOVERNED_BI_LLM_MAX_RETRIES"

TIMEOUT_VAR = "GOVERNED_BI_LLM_TIMEOUT_S"

UTILITY_TIMEOUT_VAR = "GOVERNED_BI_UTILITY_TIMEOUT_S"

MODEL_EFFORT_VAR = "GOVERNED_BI_MODEL_EFFORT"

SEED_DIR_VAR = "GOVERNED_BI_SEED_DIR"

CORPORA_DIR = "corpora"


_SESSION: Session | None = None
_SESSION_LOCK = threading.Lock()


def session_from_environment() -> Session:
    """Build once under a lock. A failed build is not cached, so a probe retries it."""
    if _SESSION is not None:
        return _SESSION
    with _SESSION_LOCK:
        if _SESSION is not None:
            return _SESSION
        return _build_session_from_environment()


def _build_session_from_environment() -> Session:
    global _SESSION

    root = REPO_ROOT
    from governed_bi import credentials
    settings = resolve_run()

    dsn = credentials.secret(*credentials.PG_DSN_NAMES)
    if not dsn:
        raise RuntimeError(
            f"no database: set {credentials.PG_DSN_NAMES[0]}. The server "
            "serves a corpus over a live connector; there is no offline mode."
        )

    from governed_bi.datasource.postgres import PostgresConnector
    from governed_bi.serve import session as session_mod

    schema = settings["schema"] or None
    corpus_dir = _resolve_corpus_dir(settings["corpus_dir"] or None, root) or _dropped_in_corpus(root)
    if not schema and not corpus_dir:
        raise RuntimeError(
            f"nothing to serve: set {CORPUS_DIR_VAR} (a curated corpus), or drop one into "
            f"{CORPORA_DIR}/, or set {SCHEMA_VAR} to seed from a live schema"
        )

    model = None
    model_id = settings["chat_model"] or None
    if model_id:
        from governed_bi.model import provider as provider_mod

        if not provider_mod.credentials_present():
            raise RuntimeError(
                f"{MODEL_VAR} is set to {model_id!r} but "
                f"{' / '.join(provider_mod.credential_names())} is not set. "
                f"Unset {MODEL_VAR} to serve without a model rather than starting a server "
                "that cannot answer."
            )
        # tools=True selects the Responses API, which carries tools and reasoning effort.
        model = provider_mod.chat_model(
            model_id,
            surface="agent",
            effort=settings["llm_reasoning_effort"] or None,
            timeout=float(settings["llm_timeout_s"]),
            max_retries=int(settings["llm_max_retries"]),
            max_output_tokens=int(settings["llm_max_output_tokens"]),
            tools=True,
        )

    utility = _utility_model(settings)

    kwargs: dict[str, Any] = {
        "connector": PostgresConnector(dsn),
        "policy": serve_policy(),
        "agent_model": model,
        "utility_model": utility,
    }

    cache = _embedder_into(kwargs, settings)
    if corpus_dir:
        _SESSION = session_mod.from_corpus_dir(corpus_dir, schemas=[schema] if schema else None, **kwargs)
    else:
        seed_dir = Path(settings["seed_dir"] or (root / "runs" / "seeded-corpus" / str(schema)))
        seed_dir.mkdir(parents=True, exist_ok=True)
        _SESSION = session_mod.from_live_schema(str(schema), corpus_root=seed_dir, **kwargs)
    if cache is not None:
        state = "unchanged" if cache.written == 0 else f"wrote {cache.written}"
        _log.info("vector cache: %s hit / %s total, %s — %s", cache.opened_with, len(cache), state, cache.uri)
    return _SESSION


def serve_policy() -> Any:
    """Every rule on (none of 1,351 questions fire); suspect columns warn. The benchmark still refuses."""
    from governed_bi.govern.policy import KNOWN_GUARD_RULE_IDS, GovernancePolicy

    return GovernancePolicy(
        # Built from the vocabulary rather than written out, so a new rule is on here the day
        # it exists: an id absent from this mapping is silently off.
        guard_rules_enabled={rule_id: True for rule_id in sorted(KNOWN_GUARD_RULE_IDS)},
        hard_block_suspect=False,
    )


def _utility_model(settings: Mapping[str, Any]) -> Any:
    """Small-jobs model, or ``None`` to share the agent's. No Responses API (no tools)."""
    model_id = settings["llm_utility_model"] or None
    if not model_id:
        return None
    from governed_bi.model import provider as provider_mod

    if not provider_mod.credentials_present():
        raise RuntimeError(
            f"{UTILITY_MODEL_VAR} is set to {model_id!r} but "
            f"{' / '.join(provider_mod.credential_names())} is not set. "
            "Unset it to share the agent's model."
        )
    return provider_mod.chat_model(
        model_id,
        surface="utility",
        effort=settings["llm_utility_reasoning_effort"] or None,
        timeout=float(settings["llm_utility_timeout_s"]),
        max_retries=int(settings["llm_max_retries"]),
    )


def _embedder_into(kwargs: dict[str, Any], settings: Mapping[str, Any]) -> Any:
    """Pass ``requested_model``: reading ``Embedder.model`` on a cold embedder probes the network."""
    model_id = settings["embedding_model"] or None
    if not model_id:
        return None
    from governed_bi.model import provider as provider_mod

    if not provider_mod.credentials_present():
        raise RuntimeError(
            f"{EMBEDDING_MODEL_VAR} is set to {model_id!r} but "
            f"{' / '.join(provider_mod.credential_names())} is not set. Unset it "
            "to serve with lexical retrieval only, rather than starting a server whose "
            "semantic channel reports failed on every turn."
        )
    from governed_bi.retrieve.vector_cache import vector_cache_from_environment

    embedder = provider_mod.embedder(
        model_id,
        max_retries=int(settings["llm_max_retries"]),
        timeout=float(settings["llm_utility_timeout_s"]),
    )
    cache = vector_cache_from_environment(model=embedder.requested_model)
    kwargs["embedder"] = embedder
    kwargs["vector_cache"] = cache
    return cache


def _resolve_corpus_dir(value: str | None, root: Path) -> str | None:
    """Against the repo root, not cwd. Default ``../BIRD-corpus`` leaves the tree.
    Cwd-relative, a wrong directory fails as "nothing to serve". Absolute paths pass through."""
    if not value:
        return None
    path = Path(value)
    return str(path if path.is_absolute() else (root / path).resolve())


def _dropped_in_corpus(root: Path) -> str | None:
    """One directory is served. Two would make ``corpus_content_hash`` depend on directory order."""
    base = root / CORPORA_DIR
    if not base.is_dir():
        return None
    found = sorted(p for p in base.iterdir() if p.is_dir() and not p.name.startswith("_"))
    if not found:
        return None
    if len(found) > 1:
        raise RuntimeError(
            f"{CORPORA_DIR}/ holds {len(found)} corpora ({', '.join(p.name for p in found)}); "
            f"set {CORPUS_DIR_VAR} to the one to serve. Choosing for you would make "
            "corpus_content_hash depend on directory order."
        )
    _log.info("serving the corpus in %s (no %s set)", found[0].as_posix(), CORPUS_DIR_VAR)
    return str(found[0])


def _log_dropped_row(state: Any) -> None:
    """Log the drop. Reading ids must not raise: this runs on a state that already broke."""
    try:
        raw_answer = state.get("answer")
        raw_record = raw_answer.get("record") if isinstance(raw_answer, Mapping) else None
        turn_id = raw_record.get("turn_id") if isinstance(raw_record, Mapping) else None
        thread_id, turn_id = state.get("thread_id"), turn_id or state.get("turn_id")
    except Exception:  # noqa: BLE001 — the ids are best effort; the log line is not
        thread_id = turn_id = "<unreadable>"
    _log.exception("record node dropped the audit row for thread %s turn %s", thread_id, turn_id)


def record_node() -> Any:
    """Never raises; the checkpointer is the store. Outside ``wrap_node``, after ``finish``.
    Returns the full record; ``keep_turns`` trims it on the next turn."""

    def record(state: dict) -> dict:
        from governed_bi.serve.messages import surface_answer_text

        try:
            answer = state.get("answer") or {}
            record_dict = answer.get("record") or {}
            if not isinstance(record_dict, Mapping) or not record_dict.get("turn_id"):
                # A paused ``ask_user`` has no ``turn_id`` yet, and ``keep_turns`` cannot upsert one.
                return {}
            assumptions = record_dict.get("assumptions")
            entry: TurnEntry = {
                "asked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "question": str(state.get("question") or "") or None,
                "answer_text": surface_answer_text(answer, state),
                # ``answer["outcome"]`` can be absent. This is the only writer, so the fallback
                # is resolved here; a reader re-deriving it would be a second answer.
                "outcome": (answer.get("outcome") if answer.get("outcome") is not None else record_dict.get("outcome")),
                "record": dict(record_dict),
                "assumptions": list(assumptions) if isinstance(assumptions, list) else [],
            }
        except Exception:  # noqa: BLE001 — a turn that answered is not a turn that failed
            _log_dropped_row(state)
            return {}
        return {"turns": [entry]}

    return record


def build_serve_graph(session: Session) -> Any:
    """``trust()`` is process-wide, so a request cannot swap the corpus. No checkpointer: the server brings one."""
    trust(dict(session.configurable()["configurable"]))
    return build_graph(accept=accept_node(session), record=record_node()).compile()


def configure_logging() -> None:
    """``basicConfig`` is a no-op once the root logger has a handler, so ``langgraph dev`` keeps its own."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def resolve_run() -> dict[str, Any]:
    from governed_bi.boot import resolve_settings

    return resolve_settings()


def make_graph() -> Any:
    configure_logging()
    _warm_imports()
    return build_serve_graph(session_from_environment())


def _warm_imports() -> None:
    """Import request-path modules at load time (avoids blockbuster ``os.getcwd`` on first request)."""
    from governed_bi.govern import guard as _guard  # noqa: F401
    from governed_bi.registry.record import missing_required  # noqa: F401
    from governed_bi.retrieve.index import IndexEntry  # noqa: F401

    try:  # pragma: no cover - only present when a model is configured
        from langchain.chat_models import init_chat_model  # noqa: F401
    except ImportError:
        pass


#: LangGraph sets LANGSERVE_GRAPHS when it imports the graph. Not a GOVERNED_BI setting.
if os.environ.get("LANGSERVE_GRAPHS"):
    _warm_imports()
    session_from_environment()
