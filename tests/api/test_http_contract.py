"""The HTTP surface, per ADR 0007.

`routes.make_app(session, pending)` and `graph_app.build_serve_graph(session)` take their
dependencies, and the fixtures below are four assets in memory, so nothing here needs a database,
a model or a key. Two rules run through it: a client may not write the run's claims about itself
(`run_id`, `corpus_content_hash`, `knobs_resolved` and their siblings), and a field the engine does
not observe must not be invented at the boundary (ADR 0007 §3).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


#: `src/governed_bi`, for the structural assertions. Some of these must be structural: a
#: behavioural test that "the server does not synthesize a tier" passes for every input the
#: test happens to try, and says nothing about the next one.
SRC = Path(__file__).resolve().parent.parent.parent / "src" / "governed_bi"

REPO = Path(__file__).resolve().parent.parent.parent

#: A prompt the four-asset corpus below routes to `beer` on the lexical channel alone.
QUESTION = "how many root beer brands are there"


@pytest.fixture(autouse=True)
def _isolated():
    """The one piece of process-wide state this file touches, cleaned up around every test.

    ``build_serve_graph`` declares its session's constants trusted, and that registry is
    process-wide (``serve/runtime.trust``) — which is the design, since the server is one
    session per process. In a suite it is shared mutable state, and the failure mode is the
    worst kind: a test that passes because an earlier one registered a corpus.
    """
    from governed_bi.serve.runtime import trust

    trust()
    yield
    trust()


class _EchoConnector:
    """A connector double, for the one test whose subject is the *governance result*.

    Real enough that a refusal is a refusal: with ``connector=None`` a governed block is a
    wiring failure and proves nothing (``tests/serve/turn_contract_fixtures`` says so at
    length). Nothing here asserts what a database returned.
    """

    dialect = "postgres"

    def execute(self, sql: str, max_rows: int | None = None) -> Any:
        return (["count"], [(3,)], False)


def _served(session: Any) -> Any:
    """The served topology, sync-callable. What ``langgraph.json`` runs.

    ``as_sync`` only because this file's tests are sync ``def``; the platform drives
    ``ainvoke`` itself. The graph is otherwise exactly the process entry's.

    It takes no turn log: ``record`` returns the finished turn onto ``ServeState.turns`` and the
    checkpointer persists it, so there is no sink to inject.
    """
    from governed_bi.api.graph_app import build_serve_graph
    from governed_bi.serve.graph import as_sync

    return as_sync(build_serve_graph(session))


# ── the record is the run's, not the client's ─────────────────────────────────


def test_a_client_cannot_set_any_provenance_field() -> None:
    """ADR 0007 §2. The most important test here.

    The UI submits one key — `{messages: [{type: "human", content}]}` — and the record
    requires fifteen fields. Something server-side must mint the rest. The failure mode is
    not that it forgets: it is that it **merges** what the client sent.

    `run_id`, `turn_id`, `question_id`, `corpus_content_hash`, `prompt_set_hash`,
    `knobs_resolved` are the run's own claims about itself, and `measure/checks.py` reads them
    to decide whether a run is quotable. A client that can set `corpus_content_hash` can make
    two different corpora report as one, which is a forged comparison rather than a bad one.

    **Fixture shape.** Invoke the graph the way the server does, with a client payload that
    sets every required field to a recognisable sentinel. Assert **no sentinel reaches the
    record** — field by field, from `required_keys()`, not from a hand-written list. Assert
    the turn still succeeds: silently dropping the whole turn would also pass a
    "no sentinel survived" check.

    **Written 2026-08-11**, once `build_serve_graph` existed. It was a stub for the whole of
    v2 because the served topology could only be assembled from a process-wide session built
    out of the environment, so the graph this asserts about could not be constructed in a
    test at all (the 2026-08-11 review, C5).

    Two mechanisms carry the claim and both are exercised here. `ServeInput` drops undeclared
    keys at the entry, so most sentinels never reach a channel; `accept` then *overwrites* the
    turn identity from `Session.turn`, so the ones that are declared channels do not survive
    either. Asserting on the record rather than on either mechanism is deliberate — the record
    is what a check reads.
    """
    from langchain_core.messages import HumanMessage

    from governed_bi.registry.record import record_keys, required_keys

    session = _indexed_session()
    forged = {name: SENTINEL for name in required_keys()}
    assert forged, "precondition: the register declares no required fields"

    out = _served(session).invoke(
        {"messages": [HumanMessage(content=QUESTION)], **forged},
        {"configurable": {"thread_id": "t-forge"}},
    )
    record = out["answer"]["record"]

    survived = sorted(name for name in required_keys() if _mentions(record.get(name)))
    assert not survived, (
        f"{survived} reached the record from the client's payload. Every quotability check "
        "reads these, so a client-settable one is a forged record rather than a wrong one"
    )
    # Not merely dropped — minted. A turn that silently produced nothing would also have no
    # sentinel in it.
    assert out["answer"]["outcome"] != "crashed", out["answer"]
    assert record["corpus_content_hash"] == session.corpus_content_hash
    assert record["run_id"] == session.run_id
    assert record["turn_id"] and record["turn_id"] != SENTINEL
    assert set(record) == record_keys()


#: Recognisable in any field the register declares, whatever its type.
SENTINEL = "client-forged-sentinel"


def _mentions(value: Any) -> bool:
    """Whether ``value`` carries the sentinel anywhere a client could have put it."""
    if isinstance(value, str):
        return SENTINEL in value
    if isinstance(value, dict):
        return any(_mentions(v) for v in value.values()) or any(_mentions(k) for k in value)
    if isinstance(value, (list, tuple)):
        return any(_mentions(v) for v in value)
    return False


def test_the_graph_is_loadable_the_way_langgraph_json_loads_it() -> None:
    """`langgraph.json` names a factory by file path, and LangGraph Server can only put
    **JSON** in `config.configurable` — while every node needs live objects: `policy` (a
    dataclass, subscripted unguarded at `serve/nodes/guard.py::guard_node`), `agent_model`, `corpus`,
    `index`, `structure`, `connector`, `assets_by_id`.

    So the factory must close over a `Session`. Assert it does: build the graph the way the
    server entry does, invoke it with a `configurable` containing **only JSON**, and assert
    the turn reaches a terminal path. A `KeyError` on `policy` mid-run is the failure this
    catches, and it is the failure that would only appear on the first real request.

    Assert `langgraph.json` itself parses and its `graphs` key names `serve` — that string is
    the UI's `ASSISTANT_ID` default, so a rename silently 404s every chat request.

    **Written 2026-08-11.** The half of this that used to be checked was checked by reading
    `graph_app.py` as text and asserting the substring `"trust("` appeared in `make_graph`'s
    body — which passes for a call that is unreachable or handed the wrong mapping. It was
    written that way because `make_graph` builds a Postgres connector; `build_serve_graph`
    takes the session, so the claim runs.
    """
    import json

    from langchain_core.messages import HumanMessage

    config = json.loads((REPO / "langgraph.json").read_text(encoding="utf-8"))
    assert "serve" in config["graphs"], (
        f"the UI's default ASSISTANT_ID is 'serve'; langgraph.json declares "
        f"{sorted(config['graphs'])}, so every chat request 404s"
    )
    target = config["graphs"]["serve"]
    assert target.endswith(":make_graph") and "api/graph_app.py" in target, target
    assert config["http"]["app"].endswith("api/routes.py:app"), config["http"]["app"]

    # JSON only. Everything a node needs has to come from the closure, not the wire.
    only_json = {"configurable": {"thread_id": "t-json", "checkpoint_ns": ""}}
    out = _served(_indexed_session()).invoke(
        {"messages": [HumanMessage(content=QUESTION)]}, only_json
    )

    answer = out["answer"]
    assert answer["outcome"] != "crashed", (
        "the graph could not run on a JSON-only config, which is the only kind LangGraph "
        f"Server can send: failed_stage={answer['failed_stage']!r} "
        f"error_type={answer['error_type']!r}"
    )
    assert answer["record"]["licensed"], "no table was retrieved, so no node reached the corpus"


# ── /capabilities: observations, and false is a real answer ───────────────────


def test_capabilities_reports_what_is_true_not_what_is_configured() -> None:
    """It is the UI's **first** request; without it the chat panel pins at a skeleton
    forever, so this route failing is indistinguishable from the whole backend being down.

    `has_live_model` must be false when no model is configured, and `can_clarify` false when
    the `ask_user` tool is not bound. Assert the false cases, because the true ones pass for
    a server that hard-codes `true` — and a hard-coded `has_live_model` is the stub-path
    defect again, one layer out: the interface would promise a model that will never answer.

    `can_edit` is **false** for now: the curator is out of scope, and reporting it true would
    put an edit button in front of a route that does not exist.

    **Both directions, over the real route.** Two apps differing only in whether the session
    carries a model: a hard-coded `true` fails the first and a hard-coded `false` fails the
    second, so neither reading of the field can pass by accident. Reachable at all only since
    `make_app` — the route used to resolve its session from the environment.
    """
    from governed_bi.api.routes import make_app

    app = make_app(_indexed_session(), _Pending())
    modelless = _client(app).get("/capabilities")
    assert modelless.status_code == 200, modelless.text
    caps = modelless.json()
    assert caps["has_live_model"] is False, (
        "the interface was promised a model that will never answer"
    )
    assert caps["can_clarify"] is False, (
        "the clarification prompt mounts on a turn that can never raise one, and a turn that "
        "looks idle while nothing is pending is the deadlock ADR 0007 §6 is written against"
    )
    assert caps["can_edit"] is False and caps["edit_mode"] == "none"
    assert not caps.keys() & {"can_scope", "can_search"}, "both described deleted browse routes"

    with_model = _client(
        make_app(_indexed_session(model=object()), _Pending())
    ).get("/capabilities")
    assert with_model.json()["has_live_model"] is True, (
        "a configured model is reported absent, so the panel degrades on a server that works"
    )
    assert with_model.json()["can_clarify"] is True


def _client(app: Any) -> Any:
    """A ``TestClient`` over ``app``. No credential: the engine requires none (2026-08-13)."""
    from fastapi.testclient import TestClient

    return TestClient(app)


def _indexed_session(*, model: Any = None, connector: Any = None, policy: Any = None) -> Any:
    """Four assets over one schema, with a real index and structure.

    Small, and large enough to route: the term and the table both mention "root beer", so
    :data:`QUESTION` reaches ``beer`` on the lexical channel with no embedder. Built through
    ``from_assets`` rather than by hand, so the index, the structure and ``assets_by_id`` are
    the ones a real corpus produces.
    """
    from governed_bi.corpus.schema import (
        AssetType,
        Binding,
        ColumnAsset,
        SchemaAsset,
        TableAsset,
        TermAsset,
    )
    from governed_bi.govern.policy import GovernancePolicy
    from governed_bi.serve.session import from_assets

    assets = [
        SchemaAsset(id="beer", name="beer", summary="beer brands and sales"),
        TableAsset(
            id="beer.brands", schema="beer", physical_name="brands",
            summary="Brands of root beer.", columns=("beer.brands.name",),
        ),
        ColumnAsset(
            id="beer.brands.name", schema="beer", parent_table="beer.brands",
            physical_name="name", summary="Brand name.",
        ),
        TermAsset(
            id="term.root_beer", name="root beer", summary="root beer, sarsaparilla",
            binding=Binding(target_type=AssetType.column, target_id="beer.brands.name"),
        ),
    ]
    session = from_assets(
        assets,
        connector=connector,
        policy=policy or GovernancePolicy(guard_rules_enabled={}),
        db_id="beer",
        corpus_content_hash_="corpus-under-test",
        agent_model=model,
    )
    assert not session.fatal_problems, [str(p) for p in session.fatal_problems]
    return session


def test_the_app_declares_exactly_the_four_custom_routes() -> None:
    """The server serves what the chat page and a deployment probe call, and nothing else.

    Asserted on the app's own route table, because a status check cannot tell a deleted route
    from a path parameter that matched nothing. The platform's ``/threads`` routes are not here.
    """
    from governed_bi.api.routes import make_app

    app = make_app(_indexed_session(), _Pending())
    declared = {
        path for route in app.routes
        if (path := getattr(route, "path", None)) and not path.startswith(("/docs", "/openapi", "/redoc"))
    }
    assert declared == {"/livez", "/readyz", "/capabilities", "/clarifications/pending"}, declared


class _Pending:
    """What ``make_app`` asks of the pending-question reader: nothing is waiting.

    Empty rather than seeded. These tests are about the app's other surfaces, and a fake holding
    rows would turn any assertion about them into an assertion about this class.
    ``tests/api/test_the_pending_queue_reads_interrupt_state.py`` is where the queue itself is
    pinned, against a fake that enforces ``status`` and ``select``.

    Self-contained -- it does not import ``PendingPage`` -- because ``make_app`` is imported
    inside the test bodies here, so there is no module-level engine import to sit beside.
    """

    PENDING_FIELDS: tuple[str, ...] = ("asked_at", "question")

    class _Page:
        rows: tuple[Any, ...] = ()
        truncated = False
        threads_scanned = 0

    def pending(self, *, limit: int = 50, offset: int = 0) -> Any:
        return self._Page()
