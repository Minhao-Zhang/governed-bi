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

from governed_bi.serve.graph import build_graph
from governed_bi.serve.runtime import trust
from governed_bi.serve.session import Session

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

#: Reasoning effort, for models that take one. ``register/knobs.py`` has declared
#: ``llm_reasoning_effort`` as ``Role.comparability`` all along, with the reason attached: two
#: v1 ladders differed **only** in this field, it was recorded nowhere, so comparability cleared
#: the pair the second run existed to isolate — and effort moved the baseline arm **+2.5pp
#: against a 2.3pp detection threshold**. So this is not a convenience flag; it is a knob whose
#: absence has already invalidated an experiment once.
MODEL_EFFORT_VAR = "GOVERNED_BI_MODEL_EFFORT"

#: Where a seeded corpus is written when no curated one is given. Written rather than held in
#: memory because ``corpus_content_hash`` digests a tree, and because a corpus you cannot read
#: is one nobody can correct.
SEED_DIR_VAR = "GOVERNED_BI_SEED_DIR"

#: Where a curated corpus is dropped in for local serving. ``.gitignore`` excludes it: these
#: trees run to thousands of files (the gold semantic layer is 8035 files / 41 MB) and are the
#: output of a curator run, so git is the source of truth for the authored demo corpus and not
#: for these.
CORPORA_DIR = "corpora"

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

    from governed_bi.datasource.postgres import PostgresConnector
    from governed_bi.govern.policy import GovernancePolicy
    from governed_bi.serve import session as session_mod

    schema = os.environ.get(SCHEMA_VAR)
    corpus_dir = os.environ.get(CORPUS_DIR_VAR) or _dropped_in_corpus(root)
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

        # Two LangChain fields, passed straight through. No provider branching here: an
        # earlier draft chose between `reasoning_effort` and `temperature` and toggled the
        # Responses API itself, which is re-deciding what `langchain-openai` already decides.
        # Decision #1 records why that is wrong — v1 wrapped `BaseChatModel` in three layers
        # and the wrapper became the thing that broke.
        #
        # `use_responses_api` is unconditional because it is the API this agent needs, not a
        # tuning choice: it binds tools, and the provider refuses tools alongside
        # `reasoning_effort` on chat completions, saying so in its own words — *"To use
        # function tools, use /v1/responses."* Setting a LangChain field to reach the endpoint
        # that supports the feature is configuration; encoding the rule was not.
        #
        # `temperature` is simply not set. Asserting a default we do not need is what forced
        # the branch in the first place.
        kwargs_model: dict[str, Any] = {"model_provider": "openai", "use_responses_api": True}
        effort = os.environ.get(MODEL_EFFORT_VAR)
        if effort:
            kwargs_model["reasoning_effort"] = effort
        model = init_chat_model(model_id, **kwargs_model)

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


def _dropped_in_corpus(root: Path) -> str | None:
    """The one curated corpus under ``corpora/``, or ``None``. Ambiguity raises.

    **This exists so ``uv run langgraph dev`` needs no environment at all**, which is the shape
    a developer actually types. What it is *not* is a default that guesses: a single directory
    is an unambiguous answer to "which corpus does this checkout serve", and two is a question
    only the operator can settle — a server that picked one would make ``corpus_content_hash``,
    the field every quotability gate reads, depend on directory ordering.

    So: none → the caller's error naming both env vars. One → that one, announced on stdout,
    because a run whose corpus was chosen for it must still say which. More than one → raise and
    name them.
    """
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

    **One checkpointer, and the nested agent gets it through ``config``.** An earlier version
    of this function built an ``InMemorySaver`` here and passed it to *both* the outer graph
    and the nested ``create_agent``, under a comment reading "two savers is worse than none:
    the interrupt is written to one and looked for in the other". That comment described a
    mechanism that does not exist. A probe: inside a node, ``CONFIG_KEY_CHECKPOINTER`` is the
    **outer** saver; the agent's own saver ends the run with **zero** checkpoints; the outer
    one has three. LangGraph propagates the checkpointer into a graph invoked inside a node and
    namespaces it, so ``ask_user`` has always resumed from the graph's saver.

    So no checkpointer is passed at all, and that is what lets the server supply its own —
    which is what makes ``/threads`` work. ``compile_graph``'s in-memory default exists for the
    CLI and would shadow it.

    **The constants are also declared trusted, and that is a security fix, not tidiness.**
    ``with_config`` binds them as *defaults* and LangGraph merges caller config **over** a
    default — which is precisely why ``thread_id`` is excluded, and precisely what made the six
    keys beside it client-settable. A request to ``/threads/{id}/runs`` carrying
    ``config.configurable.policy`` replaced the ``GovernancePolicy`` for that run; one carrying
    ``assets_by_id`` replaced the corpus every tool licenses against. Reproduced.
    :func:`~governed_bi.serve.runtime.trust` makes the shared config reader force them back
    over anything a request names, which is the same rule ``accept`` applies to the record's
    provenance fields one layer in.
    """
    _warm_imports()
    conf = dict(session_from_environment().configurable()["configurable"])
    trust(conf)
    return build_graph(accept=_accept_node).compile().with_config({"configurable": conf})


def _warm_imports() -> None:
    """Import everything the request path imports lazily, **here**, at load time.

    Not a micro-optimisation. ``langgraph dev`` installs `blockbuster`, which raises on
    blocking I/O inside an async function, and it deliberately keeps ``os.getcwd`` armed while
    disabling ``os.path.*`` and file reads. Python's **import machinery** calls
    ``ntpath.realpath`` — hence ``os.getcwd`` — so *any* function-level import in a node turns
    the first request into `BlockingError: Blocking call to os.getcwd`, with no frame of ours in
    the traceback. That cost an hour to find, which is the argument for doing it here.

    Function-level imports exist throughout ``serve/`` on purpose — they keep import-time
    cycles impossible and let a model-free path avoid loading a provider SDK. This does not
    change that; it front-loads them for the one caller that runs inside an event loop, where
    the first request would otherwise pay for them.
    """
    from governed_bi.govern import guard as _guard  # noqa: F401
    from governed_bi.register.record import missing_required  # noqa: F401
    from governed_bi.retrieve.index import IndexEntry  # noqa: F401

    try:  # pragma: no cover - only present when a model is configured
        from langchain.chat_models import init_chat_model  # noqa: F401
    except ImportError:
        pass
