"""HTTP surface (ADR 0007), over four in-memory assets.

A client may not write the run's own claims. A field the engine does not observe is not invented (ADR 0007).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


#: Structural: a behaviour test that "no tier is synthesized" says nothing about the next input.
SRC = Path(__file__).resolve().parent.parent.parent / "src" / "governed_bi"

REPO = Path(__file__).resolve().parent.parent.parent

#: A prompt the four-asset corpus below routes to `beer` on the lexical channel alone.
QUESTION = "how many root beer brands are there"


@pytest.fixture(autouse=True)
def _isolated():
    """``trust()`` is process-wide. A test can pass because an earlier one registered a corpus."""
    from governed_bi.serve.runtime import trust

    trust()
    yield
    trust()


class _EchoConnector:
    """``connector=None`` turns a governed block into a wiring failure, which proves nothing."""

    dialect = "postgres"

    def execute(self, sql: str, max_rows: int | None = None) -> Any:
        return (["count"], [(3,)], False)


def _served(session: Any) -> Any:
    """Sync wrapper around the graph ``langgraph.json`` runs. ``as_sync`` is only for these tests."""
    from governed_bi.api.graph_app import build_serve_graph
    from governed_bi.serve.graph import as_sync

    return as_sync(build_serve_graph(session))


def test_a_client_cannot_set_any_provenance_field() -> None:
    """A client-set ``corpus_content_hash`` makes two corpora report as one. The record is what a check reads.

    A turn that produced nothing would also contain no sentinel, so the outcome is asserted too.
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
    if isinstance(value, str):
        return SENTINEL in value
    if isinstance(value, dict):
        return any(_mentions(v) for v in value.values()) or any(_mentions(k) for k in value)
    if isinstance(value, (list, tuple)):
        return any(_mentions(v) for v in value)
    return False


def test_the_graph_is_loadable_the_way_langgraph_json_loads_it() -> None:
    """Only JSON fits in ``configurable``. A ``KeyError`` on ``policy`` shows up on the first real request.

    ``graphs.serve`` is the UI's assistant id, so a rename 404s every chat request.
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
    assert answer["record"]["retrieved_tables"], "no table was retrieved, so no node reached the corpus"


def test_capabilities_reports_what_is_true_not_what_is_configured() -> None:
    """False is asserted too: a hard-coded true passes every test that only builds a model."""
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
    from fastapi.testclient import TestClient

    return TestClient(app)


def _indexed_session(*, model: Any = None, connector: Any = None, policy: Any = None) -> Any:
    """Built through ``from_assets`` so the index is the one a real corpus produces. Lexical only."""
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
    """The route table, not a status check: a deleted route and a missed path parameter look the same."""
    from governed_bi.api.routes import make_app

    app = make_app(_indexed_session(), _Pending())
    declared = {
        path for route in app.routes
        if (path := getattr(route, "path", None)) and not path.startswith(("/docs", "/openapi", "/redoc"))
    }
    assert declared == {"/livez", "/readyz", "/capabilities", "/clarifications/pending"}, declared


class _Pending:
    """Nothing is waiting. Rows here would make every other assertion about this class."""

    PENDING_FIELDS: tuple[str, ...] = ("asked_at", "question")

    class _Page:
        rows: tuple[Any, ...] = ()
        truncated = False
        threads_scanned = 0

    def pending(self, *, limit: int = 50, offset: int = 0) -> Any:
        return self._Page()
