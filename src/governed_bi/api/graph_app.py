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
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from governed_bi.paths import REPO_ROOT, TOOLS_DIR
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

#: The model for the turn's small jobs: the guard's scope gate and the five facet query
#: rewriters. Unset means "use :data:`MODEL_VAR`", so a one-model deployment is unchanged.
#:
#: **Named for the role, not the tier.** These are six short calls — one word out of the gate,
#: one line out of each rewriter — and five of them run concurrently on the critical path before
#: any retrieval, so latency there is the whole perceived speed of the product. That is an
#: argument for a fast model, not for a bad one, and ``GOVERNED_BI_WEAK_MODEL`` would have
#: encoded a relative capability claim that stops being true when the models move.
#:
#: It is a **comparability knob** (``llm_utility_model``) and it is recorded even when it falls
#: back, because what these calls produce is *what gets retrieved at all*: a cheaper rewriter that
#: phrases the schema query worse moves routing recall, and routing recall moves everything after
#: it. Two runs that differ only here differ in their answers.
UTILITY_MODEL_VAR = "GOVERNED_BI_UTILITY_MODEL"

#: Reasoning effort for the utility model, separately from the agent's. Usually you want this
#: low or unset even when the agent's is high — a yes/no classification does not need a budget,
#: and the point of the split is speed.
UTILITY_MODEL_EFFORT_VAR = "GOVERNED_BI_UTILITY_MODEL_EFFORT"

#: The embedding model id, and setting it is what turns the **semantic channel on**.
#:
#: **Absent, every facet reported a failed channel on every turn, and nothing said so until the
#: live stage stream did.** The semantic half of retrieval has been fully built the whole time —
#: `Embedder` port, an OpenAI adapter, `UnifiedIndex.vectors`, `build_index(embedder=...)`,
#: `Session.configurable` adding `query_vector` — and this module simply never passed an embedder.
#: So `_channels_for` marked every facet's declared `semantic` channel `failed`, `facet_degraded`
#: was true for every turn, and the interface called it clean until ADR 0010 made channel states
#: visible. Retrieval was lexical-only in production while the corpus thesis is about meaning.
#:
#: Absent is still a supported configuration rather than a broken one — `DeterministicEmbedder`
#: exists so the model-free path never pays for tokens — but it is now a configuration somebody
#: chose rather than one nobody noticed.
EMBEDDING_MODEL_VAR = "GOVERNED_BI_EMBEDDING_MODEL"

#: How many times the provider SDK retries **one** call. Applies to every model surface —
#: the agent, the utility model and the embedder — which is what makes it global rather than
#: three settings that drift.
#:
#: **This repository believed it had eight and had two.** `governed_bi.toml` carries
#: `max_retries = 8` under a comment reading *"this repo has NO rate limiter, NO token bucket
#: and NO 429 backoff of its own — the provider SDK's exponential retry is the entire defence,
#: and these two numbers size it"*. v2 deleted the reader for that file, so the defence it sized
#: was never installed: measured on the real objects, `ChatOpenAI.max_retries` is `None` and the
#: underlying `openai` client falls back to its own default of 2.
RETRIES_VAR = "GOVERNED_BI_LLM_MAX_RETRIES"

#: Wall clock for one **agent** call, in seconds.
#:
#: Separate from the retry count on purpose: timeout answers "how long may a legitimate call
#: take" and retries answer "how flaky is the provider". They move for different reasons — but
#: they are not decided apart, because the worst case for a single call is
#: ``timeout × (retries + 1)``. The SDK's 600s default at three retries is a **40-minute** hang.
TIMEOUT_VAR = "GOVERNED_BI_LLM_TIMEOUT_S"

#: Wall clock for the small calls: the scope gate, the five facet rewriters, and the embedder.
#:
#: **Split from the agent's because the two tiers now run at different efforts.** These are
#: 1.2–1.5s calls and every one of them happens *before anything appears on screen*, so the
#: 600s default meant a single hung call stalled the turn for ten minutes — while each of those
#: call sites already has a graceful degradation written for exactly this case. Failing fast
#: into a path the code already handles beats waiting for a call that is not coming.
UTILITY_TIMEOUT_VAR = "GOVERNED_BI_UTILITY_TIMEOUT_S"

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
        kwargs_model: dict[str, Any] = {
            "model_provider": "openai",
            "use_responses_api": True,
            # Passed explicitly rather than left to the SDK, which is the whole point: unset,
            # `ChatOpenAI.max_retries` is `None` and the `openai` client applies its own 2 with
            # no timeout of its own either. Both are now recorded knobs, so a run cannot change
            # its own crash rate and still compare.
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
        # **The five injection rules stay off; the scope gate is on.** They are off for the
        # reason ADR 0006 OQ3 gives — no rule ships enabled without red-team recall *and* a
        # benign firing rate, and neither number exists — and that argument does not reach
        # `g_bi_scope`, which is not an injection defence. It answers "is this a BI question at
        # all", the maintainer asked for it explicitly, and its failure mode is a refusal the
        # user can see and rephrase rather than a silent block on a legitimate question.
        #
        # It costs one model call per turn, before any retrieval, which is the cheapest place to
        # spend it: an out-of-scope question otherwise pays for five facets, a route, a Steiner
        # connect and a full agent loop before producing nothing anyone wanted.
        "policy": GovernancePolicy(guard_rules_enabled={BI_SCOPE_RULE_ID: True}),
        "agent_model": model,
        # `None` when unset; `Session.configurable` resolves the fallback to `agent_model` once,
        # rather than leaving six call sites to each write their own `or`.
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
        # After the index is built, because that is when the misses have been written. There is
        # no flush: `VectorStore.add` writes as it goes, so this line reports rather than acts.
        #
        # It is still printed, and that is the point the JSON cache's `hits` property was added
        # for — a cache nobody can measure is one that can silently stop working. `written == 0`
        # is also the property `langgraph dev` depends on: the store is under `runs/`, inside the
        # watched tree, and writing on an unchanged corpus made the server restart, re-import,
        # write again and never become ready. Opening and searching write nothing; only `add`
        # does, and it is called with the miss set or not at all.
        state = "unchanged" if cache.written == 0 else f"wrote {cache.written}"
        print(f"vector cache: {cache.opened_with} hit / {len(cache)} total, {state} — {cache.uri}")
    return _SESSION


def _utility_model(credentials: Any) -> Any:
    """The small-jobs model, or ``None`` to share the agent's.

    ``use_responses_api`` is **not** set here, and that is the one real difference from the agent
    model's construction. It is set there because the agent binds tools and the provider refuses
    tools alongside ``reasoning_effort`` on chat completions. Nothing this model does binds a
    tool — it answers one word, or writes one line of search text — so asking for the heavier
    endpoint would be carrying a constraint from a caller that does not exist here.
    """
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
        # The *utility* timeout, which is the split's reason for existing — see
        # `UTILITY_TIMEOUT_VAR`. These calls are on the critical path before first paint.
        "timeout": _timeout(UTILITY_TIMEOUT_VAR, "llm_utility_timeout_s"),
    }
    effort = os.environ.get(UTILITY_MODEL_EFFORT_VAR)
    if effort:
        kwargs["reasoning_effort"] = effort
    return init_chat_model(model_id, **kwargs)


def _retries() -> int:
    """The global retry count, from the environment or the knob's declared default.

    ``int()`` is left to raise on a non-numeric value. A retry budget that silently falls back
    because someone typed ``three`` is the class of defect the register exists to end: the run
    would record the default while running something else.
    """
    from governed_bi.register.knobs import knob_default

    raw = os.environ.get(RETRIES_VAR)
    return int(raw) if raw else int(knob_default("llm_max_retries"))


def _timeout(var: str, knob: str) -> float:
    """One tier's wall clock, from the environment or the knob's declared default."""
    from governed_bi.register.knobs import knob_default

    raw = os.environ.get(var)
    return float(raw) if raw else float(knob_default(knob))


def _embedder_into(kwargs: dict[str, Any], credentials: Any) -> Any:
    """Add ``embedder`` and ``vector_cache`` to ``kwargs`` when configured. Returns the cache.

    **Switching this on is what makes the semantic channel exist.** Every piece of it was already
    built — the ``Embedder`` port, the OpenAI adapter, ``UnifiedIndex.vectors``,
    ``build_index(embedder=...)``, ``Session.configurable`` adding ``query_vector`` — and nothing
    passed an embedder, so ``_channels_for`` marked every facet's declared ``semantic`` channel
    ``failed`` on every turn and ``facet_degraded`` was true for the whole deployment. ADR 0010's
    stage stream is what made that visible; before it, retrieval was lexical-only in production
    while the corpus thesis is about meaning.

    Absent stays a **supported** configuration and not a broken one: ``DeterministicEmbedder``
    exists so the model-free path never pays for tokens, and the facets will say so honestly.
    What changes is that it is now a choice somebody makes rather than one nobody noticed.
    """
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

    # The embedder shares the **utility** timeout, not the agent's: it is the same latency class
    # on the same critical path — `accept` embeds the question before a single facet runs — and
    # a fifth knob for one more small call would be a knob nobody would ever set differently.
    embedder = OpenAIEmbedder(
        model=model_id,
        max_retries=_retries(),
        timeout=_timeout(UTILITY_TIMEOUT_VAR, "llm_utility_timeout_s"),
    )
    # `model_id` and not `embedder.model`: the latter probes the provider to report what it
    # actually served, and a directory name is not worth a network call at boot.
    cache = vector_cache_from_environment(model=model_id)
    kwargs["embedder"] = embedder
    kwargs["vector_cache"] = cache
    return cache


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
    # **The question's vector, computed here because here is the only per-turn server-side node.**
    # `Session.configurable(question=...)` supplies one to callers who build a config per
    # question; `make_graph` binds the config once at load time with no question, so on the
    # streamed path the key was never present and the facets' semantic channel reported `failed`
    # however many vectors the index held. Embedding failure is non-fatal and unrecorded here on
    # purpose: the facets observe the absence and `_channels_for` reports `failed`, which is the
    # honest outcome and the one the degradation gate already reads.
    if session.embedder is not None:
        try:
            turn["query_vector"] = list(session.embedder.embed([question])[0])
        except Exception:  # noqa: BLE001 — a dead embedder must not cost the turn its answer
            pass
    # `messages` is `add_messages`-reduced and the client's human message is already in the
    # channel; returning the empty list from `turn()` would be a no-op, but dropping the key
    # makes that explicit rather than relying on the reducer's behaviour.
    turn.pop("messages", None)
    return turn


def _record_node(state: dict) -> dict:
    """Append the finished turn to the audit log. Placed after ``stamp``; never raises.

    **Why this exists at all.** The log was written by ``POST /chat``'s ``_logged``, and once
    ADR 0010 turned streaming on, that route stopped serving real traffic — so ``/audit/turns``
    listed only stale REST turns and nothing anyone actually asked. Measured: three streamed
    turns, zero rows. "No turns are listed" and "no turns were served" must not be the same
    observation, which is the exact rule ``_logged``'s ``audit_logged`` field states.

    **Why here and not in ``stamp``.** ``stamp`` is the natural home — sole writer of ``answer``,
    every path funnels through it — but ``tools/check_imports.py`` orders ``serve`` before
    ``api``, and the log lives in ``api/trace_store.py``. Injecting the recorder from the module
    that mounts the graph keeps that order and mirrors ``accept`` at the other end of the graph.

    **Why it swallows.** A turn that answered is not a turn that failed. The client already has
    the answer over the ``values`` stream by the time this runs, so raising here would report an
    error for a turn that succeeded. ``append_turn`` already never raises on ``OSError`` and
    returns the error instead; this catches the rest for the same reason.

    A paused turn never reaches this node — the interrupt suspends inside ``agent_core`` — so the
    "do not log a turn with no record" rule ``_logged`` documents is satisfied by the topology
    rather than by a check. The ``turn_id`` guard stays anyway, because a record without one
    cannot be looked up and would be a row nobody can open.
    """
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
            # ``narrate``'s sentence when there is one, and the raw last message otherwise.
            # ``answer["text"]`` is *system* copy and null on the answered path (ADR 0007 §4), so
            # it is not the field to log. Preferring the stage's output rather than recomputing
            # keeps the log saying what the client was shown; the fallback covers a turn logged
            # from a graph built without the node.
            answer_text=(answer.get("answer_text") or last_ai_text(state)),
            outcome=answer.get("outcome"),
        )
    except Exception:  # noqa: BLE001 — see the docstring: logging must not fail a served turn
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

    The live constants reach the nodes through :func:`~governed_bi.serve.runtime.trust`, and
    **not** through ``with_config``. They were bound as config defaults, and that was the wrong
    shape twice over.

    It was wrong on ADR 0007 §1's own terms: the section says the constants *cannot ride the
    wire*, and binding a ``GovernancePolicy``, a ``UnifiedIndex`` and a live ``psycopg``
    connector into ``config.configurable`` puts them on the wire's data structure anyway. The
    server then serialises an assistant's config to answer ``/assistants/{id}/schemas`` and
    ``/assistants/{id}/subgraphs``, so both returned **HTTP 500** —
    ``TypeError: Object of type GovernancePolicy is not JSON serializable`` — which is how
    LangGraph Studio failed to open against a server whose own REST routes worked.

    And it was wrong on security: caller config merges **over** bound defaults, which is
    load-bearing for ``thread_id`` and catastrophic for the six keys beside it, since a request
    naming ``policy`` replaced governance for that run. ``trust`` was added to force them back;
    once it exists, the binding it was defending has nothing left to do.

    So the config stays JSON-clean and empty, the nodes read the constants from the shared
    reader, and ``thread_id`` still comes from the caller — the one key that must.

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
    trust(dict(session_from_environment().configurable()["configurable"]))
    return build_graph(accept=_accept_node, record=_record_node).compile()


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
    from governed_bi.api.trace_store import append_turn  # noqa: F401
    from governed_bi.govern import guard as _guard  # noqa: F401
    from governed_bi.register.record import missing_required  # noqa: F401
    from governed_bi.retrieve.index import IndexEntry  # noqa: F401

    try:  # pragma: no cover - only present when a model is configured
        from langchain.chat_models import init_chat_model  # noqa: F401
    except ImportError:
        pass


#: Build the session **at import time when this module is being loaded by the server.**
#:
#: `langgraph dev` installs `blockbuster`, which raises on blocking I/O reached from the event
#: loop — and `langgraph_api` calls this module's factory *synchronously from inside an async
#: handler*. The build is blocking by declaration: it resolves paths, reads `.env`, scans and
#: parses 8035 YAML files, digests that tree and opens a synchronous `psycopg` connection. There
#: is no ordering that satisfies the detector, only a sequence of tripwires — `os.getcwd`, then
#: `ScandirIterator.__next__`, then file reads. Offloading to a thread does not help either: the
#: factory is synchronous, so the loop must wait on the join, and blockbuster arms
#: `lock.acquire` too.
#:
#: So the work moves to before the loop exists, which is what LangGraph does for the identical
#: problem in its own code — `langgraph_api/graph.py` eagerly initialises ddtrace at import
#: "so its blocking os.getcwd() call runs synchronously before the event loop starts, not
#: lazily on the first request (which would trigger a blockbuster BlockingError)".
#:
#: Gated on `LANGSERVE_GRAPHS` because that variable exists only inside the server process
#: (`langgraph_api/cli.py` patches it in). Importing this module from a test or from
#: `python -m governed_bi.serve` must stay free of Postgres and of a 30-second corpus load.
#:
#: A failure here crashes the server at startup instead of on the first request, which is the
#: better of the two: a misconfigured corpus should not present as a 500 on someone's question.
if os.environ.get("LANGSERVE_GRAPHS"):
    _warm_imports()
    session_from_environment()
