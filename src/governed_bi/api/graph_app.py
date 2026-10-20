"""Graph factory ``langgraph.json`` loads (ADR 0007 §1–§2), and the environment adapter.

Two things, and the split is the point. :func:`build_serve_graph` is the **constructor**: hand
it a session and a turn log and it returns the compiled topology the server runs — no globals,
no environment, no credentials. :func:`session_from_environment` and :func:`make_graph` are the
**adapter** the process entry calls, and every ``os.environ`` read is inside it bar one: the
module-level ``LANGSERVE_GRAPHS`` probe at the foot of this file, which asks whether the server
is the importer and warms the session eagerly if so.

Before 2026-08-11 there was only the adapter. The served topology was assembled from a
module-level ``_SESSION`` reached by five modules, so no test could construct it: ``make_graph``
was verified by splitting its own source string and asserting ``"trust("`` appeared in it, and
``accept`` was executed by nothing at all.

The factory closes over a :class:`~governed_bi.serve.session.Session` because LangGraph Server
can only put JSON in ``config.configurable``, so live objects cannot ride the wire. ``accept``
derives the turn server-side; client provenance fields are ignored.
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

#: Chat model id. Absent = no model (supported; graph still runs). The name comes from
#: ``model/provider.py`` because ``serve/__main__.py`` reads the same variable and cannot
#: import this module -- ``api`` is the outermost layer.
MODEL_VAR = SURFACE_MODEL_VARS["agent"]

#: Model for guard + facet rewriters. Unset → share :data:`MODEL_VAR`.
UTILITY_MODEL_VAR = SURFACE_MODEL_VARS["utility"]

#: Reasoning effort for the utility model (usually low/unset).
UTILITY_MODEL_EFFORT_VAR = "GOVERNED_BI_UTILITY_MODEL_EFFORT"

#: Embedding model id. Setting it turns the semantic channel on.
EMBEDDING_MODEL_VAR = SURFACE_MODEL_VARS["embedding"]

#: Provider SDK retry count. Reaches the agent, the utility model, and the OpenAI and Bedrock
#: embedders. **The proxy embedder drops it**: ``provider.embedder`` constructs
#: ``ProxyEmbedder`` from the model id and the width alone, so an arm on that gateway records
#: this knob in ``knobs_resolved`` and runs on the SDK's own default (audit N6).
RETRIES_VAR = "GOVERNED_BI_LLM_MAX_RETRIES"

#: Wall clock (seconds) for one agent call.
TIMEOUT_VAR = "GOVERNED_BI_LLM_TIMEOUT_S"

#: Wall clock for guard, rewriters, and embedder. Dropped by the proxy embedder, with
#: :data:`RETRIES_VAR` and for the same reason.
UTILITY_TIMEOUT_VAR = "GOVERNED_BI_UTILITY_TIMEOUT_S"

#: Reasoning effort for the agent model (comparability knob).
MODEL_EFFORT_VAR = "GOVERNED_BI_MODEL_EFFORT"

#: Where a seeded corpus is written when no curated one is given.
SEED_DIR_VAR = "GOVERNED_BI_SEED_DIR"

#: Drop-in directory for curated corpora (gitignored).
CORPORA_DIR = "corpora"


_SESSION: Session | None = None
_SESSION_LOCK = threading.Lock()


def session_from_environment() -> Session:
    """Build the run's session once from the environment and reuse it.

    Locked, because ``/readyz`` and the first requests can arrive together and each would
    otherwise load and index the corpus. A failed build is not cached, so a probe retries it.
    """
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

    credentials.load_into_environ()

    dsn = credentials.secret(*credentials.PG_DSN_NAMES)
    if not dsn:
        raise RuntimeError(
            f"no database: set one of {' / '.join(credentials.PG_DSN_NAMES)}. The server "
            "serves a corpus over a live connector; there is no offline mode."
        )

    from governed_bi.datasource.postgres import PostgresConnector
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
        from governed_bi.model import provider as provider_mod

        name = provider_mod.provider_for("agent")
        if not provider_mod.credentials_present(name):
            raise RuntimeError(
                f"{MODEL_VAR} is set to {model_id!r} and the agent surface resolves to "
                f"provider {name!r}, but no credential for it is available "
                f"({' / '.join(provider_mod.credential_names(name)) or 'none found'}). "
                f"Unset {MODEL_VAR} to serve without a model rather than starting a server "
                "that cannot answer."
            )
        # tools=True: agent_core binds tools, which on OpenAI selects the Responses API —
        # the only transport carrying tools and reasoning_effort together.
        model = provider_mod.chat_model(
            model_id,
            surface="agent",
            provider=name,
            effort=os.environ.get(MODEL_EFFORT_VAR) or None,
            timeout=_timeout(TIMEOUT_VAR, "llm_timeout_s"),
            max_retries=_retries(),
            max_output_tokens=_max_output_tokens(),
            tools=True,
        )

    utility = _utility_model(credentials)

    kwargs: dict[str, Any] = {
        "connector": PostgresConnector(dsn),
        "policy": serve_policy(),
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
        _log.info("vector cache: %s hit / %s total, %s — %s", cache.opened_with, len(cache), state, cache.uri)
    return _SESSION


def serve_policy() -> Any:
    """The governance policy the served surface runs under: production, not the benchmark.

    Every guard rule is on (they fire on none of the 1,351 benchmark questions), and a suspect
    column warns in the prompt instead of refusing. The benchmark entry points keep the
    refusing default so a measured number cannot rest on a suspect column."""
    from governed_bi.govern.policy import KNOWN_GUARD_RULE_IDS, GovernancePolicy

    return GovernancePolicy(
        # Built from the vocabulary rather than written out, so a new rule is on here the day
        # it exists: an id absent from this mapping is silently off.
        guard_rules_enabled={rule_id: True for rule_id in sorted(KNOWN_GUARD_RULE_IDS)},
        hard_block_suspect=False,
    )


def _utility_model(credentials: Any) -> Any:
    """Small-jobs model, or ``None`` to share the agent's. No Responses API (no tools)."""
    model_id = os.environ.get(UTILITY_MODEL_VAR)
    if not model_id:
        return None
    from governed_bi.model import provider as provider_mod

    name = provider_mod.provider_for("utility")
    if not provider_mod.credentials_present(name):
        raise RuntimeError(
            f"{UTILITY_MODEL_VAR} is set to {model_id!r} and the utility surface resolves to "
            f"provider {name!r}, but no credential for it is available "
            f"({' / '.join(provider_mod.credential_names(name)) or 'none found'}). "
            "Unset it to share the agent's model."
        )
    # tools=False: the scope gate and the rewriters return text, so no Responses API.
    return provider_mod.chat_model(
        model_id,
        surface="utility",
        provider=name,
        effort=os.environ.get(UTILITY_MODEL_EFFORT_VAR) or None,
        timeout=_timeout(UTILITY_TIMEOUT_VAR, "llm_utility_timeout_s"),
        max_retries=_retries(),
    )


def _retries() -> int:
    """Global retry count from env or the knob default. Non-numeric values raise."""
    from governed_bi.register.knobs import knob_default

    raw = os.environ.get(RETRIES_VAR)
    return int(raw) if raw else int(knob_default("llm_max_retries"))


def _max_output_tokens() -> int:
    """Agent output ceiling from the knob. No environment variable, on purpose.

    Every other knob here has one because an operator needs to move it per deployment; this
    one exists to stop a client default from truncating a turn, and a per-machine override is
    how two arms come to differ on it silently. Move the knob if it needs moving.
    """
    from governed_bi.register.knobs import knob_default

    return int(knob_default("llm_max_output_tokens"))


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
    from governed_bi.model import provider as provider_mod

    name = provider_mod.provider_for("embedding")
    if not provider_mod.credentials_present(name):
        raise RuntimeError(
            f"{EMBEDDING_MODEL_VAR} is set to {model_id!r} and the embedding surface resolves "
            f"to provider {name!r}, but no credential for it is available "
            f"({' / '.join(provider_mod.credential_names(name)) or 'none found'}). Unset it "
            "to serve with lexical retrieval only, rather than starting a server whose "
            "semantic channel reports failed on every turn."
        )
    from governed_bi.retrieve.vector_cache import vector_cache_from_environment

    embedder = provider_mod.embedder(
        model_id,
        provider=name,
        max_retries=_retries(),
        timeout=_timeout(UTILITY_TIMEOUT_VAR, "llm_utility_timeout_s"),
    )
    # The **requested** name, per that function's contract: reading `Embedder.model` on a cold
    # embedder issues a network probe, and this only names a directory. Two providers can share
    # that directory safely — `build_index` keys each entry on the provider-qualified
    # `embedder.model`, so the keys differ even where the folder does not.
    cache = vector_cache_from_environment(model=embedder.requested_model)
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
    _log.info("serving the corpus in %s (no %s set)", found[0].as_posix(), CORPUS_DIR_VAR)
    return str(found[0])


def _log_dropped_row(state: Any) -> None:
    """ERROR with the thread and turn when they can be read, and without them when they cannot:
    the handler runs on a state that already broke once, so reading it must not raise again."""
    try:
        raw_answer = state.get("answer")
        raw_record = raw_answer.get("record") if isinstance(raw_answer, Mapping) else None
        turn_id = raw_record.get("turn_id") if isinstance(raw_record, Mapping) else None
        thread_id, turn_id = state.get("thread_id"), turn_id or state.get("turn_id")
    except Exception:  # noqa: BLE001 — the ids are best effort; the log line is not
        thread_id = turn_id = "<unreadable>"
    _log.exception("record node dropped the audit row for thread %s turn %s", thread_id, turn_id)


def record_node() -> Any:
    """The ``record`` node: put the finished turn onto ``ServeState.turns``. Never raises.

    **It takes no sink.** It used to be handed a turn log to append to, and that argument is gone
    with the log: the turn is returned as state and the checkpointer persists it, so there is one
    store and nothing to inject. A test observes the node by reading what it returns.

    Sits after ``stamp`` and outside ``wrap_node``, so it swallows its own failures: there is
    nothing after it to receive a ``crashed`` stamp, and a turn that answered is not a turn that
    failed.

    **It writes the record whole and lets the channel bound it.** The row it returns carries the
    full ``answer["record"]``, because the newest turn is the one an operator opens and
    ``ServeState.turns``' reducer keeps the newest row verbatim.
    :func:`~governed_bi.serve.state.keep_turns` is what trims the row on the *next* turn, dedupes
    on ``turn_id`` and caps the history — so this node has no policy in it and a change to the
    retention rule is one edit, in the place that owns the channel.
    """

    def record(state: dict) -> dict:
        from governed_bi.serve.messages import surface_answer_text

        try:
            answer = state.get("answer") or {}
            record_dict = answer.get("record") or {}
            if not isinstance(record_dict, Mapping) or not record_dict.get("turn_id"):
                # A paused turn (``ask_user``) has no record yet. Nothing is appended: an
                # entry with no ``turn_id`` is unaddressable by ``get_turn``.
                return {}
            entry: TurnEntry = {
                # This node is the only place ``asked_at`` is set -- nothing else writes a turn
                # envelope. ``api/thread_turns`` sorts on it in both list paths, and
                # ``summarise_turn`` projects it onto the wire, so a row that omits it sorts on
                # the empty string.
                "asked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "question": str(state.get("question") or "") or None,
                "answer_text": surface_answer_text(answer, state),
                # ``answer["outcome"]`` is absent on some paths and the record carries its
                # own, so the fallback is resolved here rather than by a reader: this node is
                # the only writer, and a reader that re-derived it would be a second answer to
                # what the turn's outcome was.
                "outcome": (answer.get("outcome") if answer.get("outcome") is not None else record_dict.get("outcome")),
                "record": dict(record_dict),
            }
        except Exception:  # noqa: BLE001 — a turn that answered is not a turn that failed
            # Nothing after this node can stamp the failure, so the log line is the only trace
            # that an audit row was lost.
            _log_dropped_row(state)
            return {}
        return {"turns": [entry]}

    return record


def build_serve_graph(session: Session) -> Any:
    """The graph ``langgraph.json`` serves: ``accept`` before ``guard``, ``record`` after ``stamp``.

    It differs from ``serve.graph.compile_graph`` on purpose, at the trust boundary. ``trust()``
    is called here, so the server-side constants override whatever a request supplies and a
    client cannot swap the corpus; it is process-wide. No checkpointer: the server brings one.
    """
    trust(dict(session.configurable()["configurable"]))
    return build_graph(accept=accept_node(session), record=record_node()).compile()


def configure_logging() -> None:
    """Root logging for the API entry points, if the host has not configured it.

    ``basicConfig`` does nothing when the root logger already has a handler, so a host that set
    logging up (``langgraph dev``) keeps its own and bare ``uvicorn`` gets INFO on stderr.
    """
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def make_graph() -> Any:
    """What ``langgraph.json``'s ``graphs.serve`` points at: the environment adapter."""
    configure_logging()
    _warm_imports()
    return build_serve_graph(session_from_environment())


def _warm_imports() -> None:
    """Import request-path modules at load time (avoids blockbuster ``os.getcwd`` on first request)."""
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
