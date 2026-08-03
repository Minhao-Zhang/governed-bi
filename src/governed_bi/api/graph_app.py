"""The graph factory ``langgraph.json`` loads. ADR 0007 §1 and §2.

**Why a factory and not a compiled object.** LangGraph Server can only put **JSON** in
``config.configurable``, and every node here needs live objects: ``policy`` (a
`GovernancePolicy` dataclass, subscripted unguarded in ``guard``), ``agent_model``,
``corpus``, ``index``, ``structure``, ``connector``, ``assets_by_id``. ``serve/state.py``
already records the same constraint for the policy — *"the checkpointer cannot msgpack the
dataclass"*. So the constants cannot ride the wire, and the factory closes over a
:class:`~governed_bi.serve.session.Session` built once at server start.

That is the whole reason the session seam had to exist before the server could: the server is
simply its second caller, after ``python -m governed_bi.serve``.

**Why an ``accept`` node.** The client submits one key — ``{messages: [{type: "human",
content}]}`` — and the record requires fifteen fields. Something must derive the turn, and per
ADR 0007 §2 it must be **server-side**: ``run_id``, ``corpus_content_hash``,
``prompt_set_hash`` and ``knobs_resolved`` are the run's own claims about itself, every
quotability gate reads them, and a client that could set ``corpus_content_hash`` could make
two different corpora report as one — a *forged* comparison rather than a wrong one. Same rule
as ADR 0006's "no tool writes to ``licensed``".

So ``accept`` reads the last human message and calls ``Session.turn``. Anything a client sends
in a provenance field is **ignored, not merged**.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ..serve.graph import build_graph
from ..serve.session import Session

__all__ = ["make_graph", "session_from_environment", "SCHEMA_VAR", "CORPUS_DIR_VAR", "MODEL_VAR"]

#: Which schema to serve. A server serves one corpus; pointing it at another is a restart,
#: which is correct — the corpus content hash is a run constant.
SCHEMA_VAR = "GOVERNED_BI_SCHEMA"

#: A curated corpus on disk. Takes precedence over seeding from the live schema, because a
#: curated corpus is the point and a seeded one is the fallback.
CORPUS_DIR_VAR = "GOVERNED_BI_CORPUS_DIR"

#: The chat model id. Absent means **no model**, and that is a supported configuration: the
#: graph still runs, retrieval and governance are real, and `/capabilities` reports
#: `has_live_model: false` rather than promising a model that will never answer.
MODEL_VAR = "GOVERNED_BI_MODEL"

#: Where a seeded corpus is written when no curated one is given. Written rather than held in
#: memory because ``corpus_content_hash`` digests a tree, and because a corpus you cannot read
#: is one nobody can correct.
SEED_DIR_VAR = "GOVERNED_BI_SEED_DIR"

_SESSION: Session | None = None


def session_from_environment() -> Session:
    """Build the run's session once, from the environment, and reuse it.

    Cached at module scope on purpose: the session **is** the run constants, so building a
    second one per request would mean two requests of one run disagreeing about the corpus
    they served — the failure ADR 0005 §2.8.2.2's seam exists to make unrepresentable.
    """
    global _SESSION
    if _SESSION is not None:
        return _SESSION

    root = Path(__file__).resolve().parent.parent.parent.parent
    import sys

    sys.path.insert(0, str(root / "tools"))
    import credentials

    credentials.load_into_environ()

    dsn = credentials.secret(*credentials.PG_DSN_NAMES)
    if not dsn:
        raise RuntimeError(
            f"no database: set one of {' / '.join(credentials.PG_DSN_NAMES)}. The server "
            "serves a corpus over a live connector; there is no offline mode."
        )

    from ..datasource.postgres import PostgresConnector
    from ..govern.policy import GovernancePolicy
    from ..serve import session as session_mod

    schema = os.environ.get(SCHEMA_VAR)
    corpus_dir = os.environ.get(CORPUS_DIR_VAR)
    if not schema and not corpus_dir:
        raise RuntimeError(f"set {SCHEMA_VAR} (seed from a live schema) or {CORPUS_DIR_VAR}")

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

        model = init_chat_model(model_id, model_provider="openai", temperature=0)

    kwargs: dict[str, Any] = {
        "connector": PostgresConnector(dsn),
        "policy": GovernancePolicy(guard_rules_enabled={}),
        "agent_model": model,
    }
    if corpus_dir:
        _SESSION = session_mod.from_corpus_dir(corpus_dir, schemas=[schema] if schema else None, **kwargs)
    else:
        seed_dir = Path(os.environ.get(SEED_DIR_VAR) or (root / "runs" / "seeded-corpus" / str(schema)))
        seed_dir.mkdir(parents=True, exist_ok=True)
        _SESSION = session_mod.from_live_schema(str(schema), corpus_root=seed_dir, **kwargs)
    return _SESSION


def _accept_node(state: dict, config: Any) -> dict:
    """Derive a turn from the conversation. The client's provenance fields are ignored.

    Returns the turn's fields as a state update, so ``guard`` finds ``state["question"]`` and
    ``stamp`` finds the fifteen the record requires — regardless of what the client sent.
    """
    session = session_from_environment()
    question = _last_human(state)
    if not question:
        # No question is not a refusal and not an answer: there is nothing to serve. Routed as
        # a crash so `stamp` records it against `accept` rather than against `guard`, which
        # never ran.
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
    # `messages` is `add_messages`-reduced and the client's human message is already in the
    # channel; returning the empty list from `turn()` would be a no-op, but dropping the key
    # makes that explicit rather than relying on the reducer's behaviour.
    turn.pop("messages", None)
    return turn


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

    The live constants are **bound as config defaults**, which is what closes the gap ADR
    0007 §1 describes: the server can only send JSON, and every node reads ``policy``,
    ``index``, ``structure``, ``corpus``, ``connector`` and ``assets_by_id`` off
    ``configurable``. Without the binding, the first real request dies on ``guard``'s
    unguarded ``config["configurable"]["policy"]`` — verified, and it is a `KeyError` three
    frames into a node rather than anything a client could read.

    ``with_config`` returns a ``CompiledStateGraph``, not a wrapper, so the server treats it as
    an ordinary graph. Caller config merges **over** these defaults, which is why
    ``thread_id`` is deliberately excluded: a bound default would silently collapse every
    conversation into one thread, and the checkpointer would make that look like memory
    working rather than failing.

    No checkpointer is passed: the server supplies its own, and that is what makes ``/threads``
    and the ``ask_user`` interrupt resume work. ``compile_graph`` defaults to an in-memory
    saver for the CLI's benefit, which would shadow it.
    """
    conf = dict(session_from_environment().configurable()["configurable"])
    conf.pop("thread_id", None)
    return build_graph(accept=_accept_node).compile().with_config({"configurable": conf})
