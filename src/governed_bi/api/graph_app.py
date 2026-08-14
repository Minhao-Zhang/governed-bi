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

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from governed_bi.paths import REPO_ROOT
from governed_bi.serve.accept import accept_node
from governed_bi.serve.graph import build_graph
from governed_bi.serve.runtime import trust
from governed_bi.serve.session import Session

__all__ = [
    "build_serve_graph",
    "record_node",
    "make_graph",
    "session_from_environment",
    "SCHEMA_VAR",
    "CORPUS_DIR_VAR",
    "MODEL_VAR",
]

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

#: A ``StaticRoleAccessPolicy`` TOML file (ADR 0012 §2). Unset ⇒ ``OpenAccessPolicy``, which
#: authorizes everything and is what this repository ships. Resolved against the repo root for
#: the reason ``CORPUS_DIR_VAR`` is: a policy path read relative to the process's cwd resolves
#: to nothing and would fail as "no such file" rather than "you are in the wrong directory".
ACCESS_POLICY_VAR = "GOVERNED_BI_ACCESS_POLICY"


_SESSION: Session | None = None


def session_from_environment() -> Session:
    """Build the run's session once from the environment and reuse it."""
    global _SESSION
    if _SESSION is not None:
        return _SESSION

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
            tools=True,
        )

    utility = _utility_model(credentials)

    from governed_bi.govern.guard import BI_SCOPE_RULE_ID

    kwargs: dict[str, Any] = {
        "connector": PostgresConnector(dsn),
        # Injection rules stay off (ADR 0006 OQ3); scope gate is on.
        "policy": GovernancePolicy(
            guard_rules_enabled={BI_SCOPE_RULE_ID: True},
            access_grant=resolve_access_grant(root),
        ),
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


def access_policy_from_environment(root: Path) -> Any:
    """The run's :class:`~governed_bi.ports.AccessPolicy`. ``OpenAccessPolicy`` unless configured.

    The composition root, and the only place in ``src/`` that chooses one. A path in
    :data:`ACCESS_POLICY_VAR` selects the reference adapter; anything else would be a fork's
    own adapter constructed here.

    **A missing file raises.** An operator who set the variable asked for a restriction, and
    falling back to the open policy would serve every table while the record said a policy
    file had been read — which is the shape ADR 0012 §7 refuses for the digest and this
    function would reproduce one layer up.
    """
    from governed_bi.govern.access import OpenAccessPolicy, StaticRoleAccessPolicy

    configured = os.environ.get(ACCESS_POLICY_VAR)
    if not configured:
        return OpenAccessPolicy()
    path = Path(configured)
    resolved = path if path.is_absolute() else (root / path).resolve()
    if not resolved.is_file():
        raise RuntimeError(
            f"{ACCESS_POLICY_VAR} is set to {configured!r}, which resolves to {resolved} and is "
            "not a file. Refusing to fall back to OpenAccessPolicy: a server that authorizes "
            "every table while its operator believes a policy file is in force is worse than "
            f"one that will not start. Unset {ACCESS_POLICY_VAR} to serve open."
        )
    return StaticRoleAccessPolicy.from_toml(resolved)


def resolve_access_grant(root: Path) -> Any:
    """This turn's :class:`~governed_bi.ports.Grant` — the wire ADR 0012 §8.1 owed.

    One policy, asked once, for the one principal :func:`~governed_bi.api.auth.authenticated_principal`
    resolves. ``GovernancePolicy`` carries the result, so ``check()`` and ``prepare()`` read the
    grant off the policy every serve node already threads (ADR 0012 §2's argument against a
    ``grant=`` keyword).

    A raising adapter propagates and the server does not start. That is deliberate and is the
    port's own contract: a directory that is down is a wiring failure, and turning it into a
    grant — of any width — would either lock every analyst out or open the doors, with the
    ledger recording neither.
    """
    from governed_bi.api.auth import authenticated_principal

    return access_policy_from_environment(root).grant_for(authenticated_principal())


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
    print(f"serving the corpus in {found[0].as_posix()} (no {CORPUS_DIR_VAR} set)")
    return str(found[0])


def record_node(turn_log: Any) -> Any:
    """The ``record`` node for ``turn_log``: append the finished turn. Never raises.

    Takes the log rather than importing one, so a test can watch what a served turn writes
    without pointing the repository's own ``runs/serve`` at a fixture. ``turn_log`` is anything
    exposing ``append_turn`` — :mod:`governed_bi.api.trace_store` is the production one.

    Sits after ``stamp`` and outside ``wrap_node``, so it swallows its own failures: there is
    nothing after it to receive a ``crashed`` stamp, and a turn that answered is not a turn that
    failed.
    """

    def record(state: dict) -> dict:
        from governed_bi.serve.messages import surface_answer_text

        try:
            answer = state.get("answer") or {}
            record_dict = answer.get("record") or {}
            if not isinstance(record_dict, Mapping) or not record_dict.get("turn_id"):
                return {}
            turn_log.append_turn(
                record_dict,
                question=str(state.get("question") or "") or None,
                answer_text=surface_answer_text(answer, state),
                outcome=answer.get("outcome"),
            )
        except Exception:  # noqa: BLE001 — logging must not fail a served turn
            return {}
        return {}

    return record


def build_serve_graph(session: Session, *, turn_log: Any) -> Any:
    """The served topology, compiled. **The constructor a test can call.**

    This is the graph ``langgraph.json`` runs: ``accept`` in front of ``guard`` (so the turn is
    derived from a client conversation rather than accepted from one) and ``record`` after
    ``stamp``. It is a *different topology* from
    :func:`~governed_bi.serve.graph.compile_graph`, which every other graph test uses — that one
    has no ``accept``, no ``record``, and passes the whole of ``ServeState`` in and out. The
    difference is deliberate and is the trust boundary (see ``ServeInput`` / ``ServeOutput``).

    :func:`~governed_bi.serve.runtime.trust` is called here and not by the caller, because it is
    what makes the graph loadable the way the server loads it: LangGraph Server puts only JSON on
    ``config["configurable"]``, and every node needs ``policy``, ``index``, ``connector`` and the
    rest. Forcing them over whatever a request supplies is also the reason a client cannot swap
    the corpus out from under a run. It is process-wide state; a caller that builds two graphs
    over two sessions gets the second one's constants, and ``trust()`` with no argument clears.

    No checkpointer — the server supplies its own (needed for ``/threads``).
    """
    trust(dict(session.configurable()["configurable"]))
    return build_graph(accept=accept_node(session), record=record_node(turn_log)).compile()


def make_graph() -> Any:
    """What ``langgraph.json``'s ``graphs.serve`` points at: the environment adapter."""
    from governed_bi.api import trace_store

    _warm_imports()
    return build_serve_graph(session_from_environment(), turn_log=trace_store)


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
