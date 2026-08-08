"""Graph factory ``langgraph.json`` loads (ADR 0007 §1–§2).

Factory closes over a :class:`~governed_bi.serve.session.Session` — LangGraph Server can
only put JSON in ``config.configurable``, so live objects cannot ride the wire.
``accept`` derives the turn server-side; client provenance fields are ignored.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from governed_bi.paths import REPO_ROOT, TOOLS_DIR
from governed_bi.serve.graph import build_graph
from governed_bi.serve.runtime import trust
from governed_bi.serve.session import Session

__all__ = ["make_graph", "session_from_environment", "SCHEMA_VAR", "CORPUS_DIR_VAR", "MODEL_VAR"]

#: Schema to serve. Changing corpus requires a restart.
SCHEMA_VAR = "GOVERNED_BI_SCHEMA"

#: Curated corpus on disk; takes precedence over live-schema seeding.
CORPUS_DIR_VAR = "GOVERNED_BI_CORPUS_DIR"

#: Chat model id. Absent = no model (supported; graph still runs).
MODEL_VAR = "GOVERNED_BI_MODEL"

#: Model for guard + facet rewriters. Unset → share :data:`MODEL_VAR`.
UTILITY_MODEL_VAR = "GOVERNED_BI_UTILITY_MODEL"

#: Reasoning effort for the utility model (usually low/unset).
UTILITY_MODEL_EFFORT_VAR = "GOVERNED_BI_UTILITY_MODEL_EFFORT"

#: Embedding model id. Setting it turns the semantic channel on.
EMBEDDING_MODEL_VAR = "GOVERNED_BI_EMBEDDING_MODEL"

#: Provider SDK retry count for agent, utility, and embedder.
RETRIES_VAR = "GOVERNED_BI_LLM_MAX_RETRIES"

#: Wall clock (seconds) for one agent call.
TIMEOUT_VAR = "GOVERNED_BI_LLM_TIMEOUT_S"

#: Wall clock for guard, rewriters, and embedder.
UTILITY_TIMEOUT_VAR = "GOVERNED_BI_UTILITY_TIMEOUT_S"

#: Reasoning effort for the agent model (comparability knob).
MODEL_EFFORT_VAR = "GOVERNED_BI_MODEL_EFFORT"

#: Where a seeded corpus is written when no curated one is given.
SEED_DIR_VAR = "GOVERNED_BI_SEED_DIR"

#: Drop-in directory for curated corpora (gitignored).
CORPORA_DIR = "corpora"


_SESSION: Session | None = None


def session_from_environment() -> Session:
    """Build the run's session once from the environment and reuse it."""
    global _SESSION
    if _SESSION is not None:
        return _SESSION

    root = REPO_ROOT
    import sys

    sys.path.insert(0, str(TOOLS_DIR))
    import credentials

    credentials.load_into_environ()

    dsn = credentials.secret(*credentials.PG_DSN_NAMES)
    if not dsn:
        raise RuntimeError(
            f"no database: set one of {' / '.join(credentials.PG_DSN_NAMES)}. The server "
            "serves a corpus over a live connector; there is no offline mode."
        )

    from governed_bi.datasource.postgres import PostgresConnector
    from governed_bi.govern.policy import GovernancePolicy
    from governed_bi.serve import session as session_mod

    schema = os.environ.get(SCHEMA_VAR)
    corpus_dir = _resolve_corpus_dir(os.environ.get(CORPUS_DIR_VAR), root) or _dropped_in_corpus(root)
    if not schema and not corpus_dir:
        raise RuntimeError(
            f"nothing to serve: set {CORPUS_DIR_VAR} (a curated corpus), or drop one into "
            f"{CORPORA_DIR}/, or set {SCHEMA_VAR} to seed from a live schema"
        )

    model = None
    model_id = os.environ.get(MODEL_VAR)
    if model_id:
        if not credentials.have(*credentials.OPENAI_KEY_NAMES):
            raise RuntimeError(
                f"{MODEL_VAR} is set to {model_id!r} but no model credential is available "
                f"({' / '.join(credentials.OPENAI_KEY_NAMES)}). Unset {MODEL_VAR} to serve "
                "without a model rather than starting a server that cannot answer."
            )
        from langchain.chat_models import init_chat_model

        # Responses API is required for tools + reasoning_effort together.
        kwargs_model: dict[str, Any] = {
            "model_provider": "openai",
            "use_responses_api": True,
            "max_retries": _retries(),
            "timeout": _timeout(TIMEOUT_VAR, "llm_timeout_s"),
        }
        effort = os.environ.get(MODEL_EFFORT_VAR)
        if effort:
            kwargs_model["reasoning_effort"] = effort
        model = init_chat_model(model_id, **kwargs_model)

    utility = _utility_model(credentials)

    from governed_bi.govern.guard import BI_SCOPE_RULE_ID

    kwargs: dict[str, Any] = {
        "connector": PostgresConnector(dsn),
        # Injection rules stay off (ADR 0006 OQ3); scope gate is on.
        "policy": GovernancePolicy(guard_rules_enabled={BI_SCOPE_RULE_ID: True}),
        "agent_model": model,
        "utility_model": utility,
    }

    cache = _embedder_into(kwargs, credentials)
    if corpus_dir:
        _SESSION = session_mod.from_corpus_dir(corpus_dir, schemas=[schema] if schema else None, **kwargs)
    else:
        seed_dir = Path(os.environ.get(SEED_DIR_VAR) or (root / "runs" / "seeded-corpus" / str(schema)))
        seed_dir.mkdir(parents=True, exist_ok=True)
        _SESSION = session_mod.from_live_schema(str(schema), corpus_root=seed_dir, **kwargs)
    if cache is not None:
        state = "unchanged" if cache.written == 0 else f"wrote {cache.written}"
        print(f"vector cache: {cache.opened_with} hit / {len(cache)} total, {state} — {cache.uri}")
    return _SESSION


def _utility_model(credentials: Any) -> Any:
    """Small-jobs model, or ``None`` to share the agent's. No Responses API (no tools)."""
    model_id = os.environ.get(UTILITY_MODEL_VAR)
    if not model_id:
        return None
    if not credentials.have(*credentials.OPENAI_KEY_NAMES):
        raise RuntimeError(
            f"{UTILITY_MODEL_VAR} is set to {model_id!r} but no model credential is available "
            f"({' / '.join(credentials.OPENAI_KEY_NAMES)}). Unset it to share the agent's model."
        )
    from langchain.chat_models import init_chat_model

    kwargs: dict[str, Any] = {
        "model_provider": "openai",
        "max_retries": _retries(),
        "timeout": _timeout(UTILITY_TIMEOUT_VAR, "llm_utility_timeout_s"),
    }
    effort = os.environ.get(UTILITY_MODEL_EFFORT_VAR)
    if effort:
        kwargs["reasoning_effort"] = effort
    return init_chat_model(model_id, **kwargs)


def _retries() -> int:
    """Global retry count from env or the knob default. Non-numeric values raise."""
    from governed_bi.register.knobs import knob_default

    raw = os.environ.get(RETRIES_VAR)
    return int(raw) if raw else int(knob_default("llm_max_retries"))


def _timeout(var: str, knob: str) -> float:
    """One tier's wall clock from env or the knob default."""
    from governed_bi.register.knobs import knob_default

    raw = os.environ.get(var)
    return float(raw) if raw else float(knob_default(knob))


def _embedder_into(kwargs: dict[str, Any], credentials: Any) -> Any:
    """Add ``embedder`` and ``vector_cache`` when configured. Returns the cache (or None)."""
    model_id = os.environ.get(EMBEDDING_MODEL_VAR)
    if not model_id:
        return None
    if not credentials.have(*credentials.OPENAI_KEY_NAMES):
        raise RuntimeError(
            f"{EMBEDDING_MODEL_VAR} is set to {model_id!r} but no embedding credential is "
            f"available ({' / '.join(credentials.OPENAI_KEY_NAMES)}). Unset it to serve with "
            "lexical retrieval only, rather than starting a server whose semantic channel "
            "reports failed on every turn."
        )
    from governed_bi.model.openai_embedder import OpenAIEmbedder
    from governed_bi.retrieve.vector_cache import vector_cache_from_environment

    embedder = OpenAIEmbedder(
        model=model_id,
        max_retries=_retries(),
        timeout=_timeout(UTILITY_TIMEOUT_VAR, "llm_utility_timeout_s"),
    )
    cache = vector_cache_from_environment(model=model_id)
    kwargs["embedder"] = embedder
    kwargs["vector_cache"] = cache
    return cache


def _resolve_corpus_dir(value: str | None, root: Path) -> str | None:
    """Resolve ``GOVERNED_BI_CORPUS_DIR`` against the **repo root**, not the process's cwd.

    The configured value is ``../BIRD-corpus`` (D13, 2026-08-07), a path that leaves this tree.
    Left cwd-relative it resolves to whatever sits beside the start directory — usually
    nothing, failing as "nothing to serve" rather than "you are in the wrong directory".
    Absolute values are returned untouched.
    """
    if not value:
        return None
    path = Path(value)
    return str(path if path.is_absolute() else (root / path).resolve())


def _dropped_in_corpus(root: Path) -> str | None:
    """The one curated corpus under ``corpora/``, or ``None``. Ambiguity raises."""
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
    print(f"serving the corpus in {found[0].as_posix()} (no {CORPUS_DIR_VAR} set)")
    return str(found[0])


def _accept_node(state: dict, config: Any) -> dict:
    """Derive a turn from the conversation. Client provenance fields are ignored."""
    session = session_from_environment()
    question = _last_human(state)
    if not question:
        return {
            "path_kind": "crashed",
            "failure": {
                "stage": "accept",
                "error_type": "ValueError",
                "detail": "no human message in the conversation",
            },
        }
    prior = sum(1 for m in state.get("messages") or [] if _kind(m) == "human")
    turn = session.turn(question, turn_index=max(1, prior), thread_id=_thread_id(config))
    # Per-turn query vector: streamed path binds config once with no question.
    if session.embedder is not None:
        try:
            turn["query_vector"] = list(session.embedder.embed([question])[0])
        except Exception:  # noqa: BLE001 — a dead embedder must not cost the turn its answer
            pass
    turn.pop("messages", None)
    return turn


def _record_node(state: dict) -> dict:
    """Append the finished turn to the audit log. After ``stamp``; never raises."""
    from governed_bi.api.trace_store import append_turn
    from governed_bi.serve.messages import last_ai_text

    try:
        answer = state.get("answer") or {}
        record = answer.get("record") or {}
        if not isinstance(record, Mapping) or not record.get("turn_id"):
            return {}
        append_turn(
            record,
            question=str(state.get("question") or "") or None,
            answer_text=(answer.get("answer_text") or last_ai_text(state)),
            outcome=answer.get("outcome"),
        )
    except Exception:  # noqa: BLE001 — logging must not fail a served turn
        return {}
    return {}


def _kind(message: Any) -> str:
    return str(getattr(message, "type", "") or (message.get("type", "") if isinstance(message, dict) else ""))


def _last_human(state: dict) -> str:
    for message in reversed(state.get("messages") or []):
        if _kind(message) == "human":
            content = getattr(message, "content", None)
            if content is None and isinstance(message, dict):
                content = message.get("content")
            if content:
                return str(content)
    return ""


def _thread_id(config: Any) -> str | None:
    try:
        return str((config or {}).get("configurable", {}).get("thread_id") or "") or None
    except AttributeError:
        return None


def make_graph() -> Any:
    """What ``langgraph.json``'s ``graphs.serve`` points at.

    Run constants reach nodes via :func:`~governed_bi.serve.runtime.trust`, not ``with_config``.
    No checkpointer here — the server supplies its own (needed for ``/threads``).
    """
    _warm_imports()
    trust(dict(session_from_environment().configurable()["configurable"]))
    return build_graph(accept=_accept_node, record=_record_node).compile()


def _warm_imports() -> None:
    """Import request-path modules at load time (avoids blockbuster ``os.getcwd`` on first request)."""
    from governed_bi.api.trace_store import append_turn  # noqa: F401
    from governed_bi.govern import guard as _guard  # noqa: F401
    from governed_bi.register.record import missing_required  # noqa: F401
    from governed_bi.retrieve.index import IndexEntry  # noqa: F401

    try:  # pragma: no cover - only present when a model is configured
        from langchain.chat_models import init_chat_model  # noqa: F401
    except ImportError:
        pass


#: Eager session build when loaded by the server (`LANGSERVE_GRAPHS` set).
#: Moves blocking I/O before the event loop so blockbuster does not fire.
if os.environ.get("LANGSERVE_GRAPHS"):
    _warm_imports()
    session_from_environment()
