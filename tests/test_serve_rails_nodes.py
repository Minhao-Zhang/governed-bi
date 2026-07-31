"""The serve rails nodes are addressable (N18).

Every one of these functions used to be a closure inside ``build_serve_rails``,
so no code outside that 1032-line function could name one — which meant none of
them could be tested, stubbed, or replaced without driving a whole governed turn.
This file is the proof that changed: it imports rails nodes by name, hands them a
``ServeRuntime`` it built itself, and asserts on what they return. If someone
folds them back into the builder, this file stops importing.

It is deliberately NOT another end-to-end turn — the eighteen ``FakeToolModel``
files already cover that, and they would keep passing whether or not the nodes
are reachable.
"""

from governed_bi.analyst.agent import (
    ServeDeployment,
    ServeRuntime,
    _tool_start_detail,
    after_assemble,
    after_refuse,
    ingest_node,
    refuse_gate_node,
)
from governed_bi.analyst.governance import GovEventStream, StageRecorder
from governed_bi.config import Environment, Settings
from governed_bi.corpus import Corpus
from governed_bi.corpus.schemas import NegativeExampleAsset
from governed_bi.gateway import Identity


def _runtime(corpus: Corpus, events: list) -> ServeRuntime:
    """A runtime assembled by hand, with no gateway, no model and no database.

    ``ServeRuntime.build`` would need a live connector for the dialect; the nodes
    under test need neither, and being able to say so in a constructor call is
    exactly the addressability this file is asserting.
    """
    stages = StageRecorder()
    return ServeRuntime(
        deployment=ServeDeployment(
            corpus=corpus,
            gateway=None,
            settings=Settings.for_env(Environment.dev),
            identity=Identity(user="dev", all_access=True),
            model=None,
            on_event=events.append,
            session_id="rails-test",
        ),
        dialect="sqlite",
        default_schema=None,
        agent_core_prompt="",
        schema_pick_prompt="",
        graph_obj=None,
        allowlist={},
        corpus_schemas=set(),
        spans_schemas=False,
        route_top_k=1,
        router_chat=None,
        router_schema_vectors=None,
        index_cache=None,
        stages=stages,
        events=GovEventStream(events.append, finalize_ctx=None, stages=stages),
    )


def test_ingest_node_seeds_provenance_and_counts_the_turn():
    events: list = []
    rt = _runtime(Corpus(assets=[]), events)

    first = ingest_node(rt, {"question": "how many customers?"})

    assert first["base_provenance"] == {
        "session_id": "rails-test",
        "user": "dev",
        "runtime": "agent",
    }
    assert first["session_id"] == "rails-test"
    # `session_id` on the state wins over the deployment's default.
    second = ingest_node(rt, {"question": "and last month?", "session_id": "chat-7"})
    assert second["session_id"] == "chat-7"
    # One `route` rail event per call, and the turn counter advanced twice — that
    # counter is what keeps a reused graph from UPSERT-colliding on `eval:1`.
    assert [e.get("step") for e in events] == ["route", "route"]
    assert rt.turn_n[0] == rt.deployment.n_human + 1


def test_refuse_gate_node_refuses_on_a_curated_negative_example():
    corpus = Corpus(
        assets=[
            NegativeExampleAsset(
                id="neg_headcount",
                pattern="questions about employees, staffing, or headcount",
                reason="HR data is out of scope for this corpus",
                escalation="Ask the People team.",
            )
        ]
    )
    events: list = []
    rt = _runtime(corpus, events)

    refused = refuse_gate_node(
        rt,
        {"question": "what is the total headcount?", "base_provenance": {}},
    )
    assert refused["outcome"] == "refuse"
    assert refused["answer"].provenance["refused_by"] == "refuse_gate"
    assert refused["answer"].provenance["negative_example"] == "neg_headcount"

    allowed = refuse_gate_node(
        rt, {"question": "total revenue by region?", "base_provenance": {}}
    )
    assert allowed["outcome"] == "continue"
    assert "answer" not in allowed


def test_the_conditional_edges_route_on_outcome():
    assert after_refuse({"outcome": "refuse"}) == "__end__"
    assert after_refuse({"outcome": "continue"}) == "assemble"
    assert after_assemble({"outcome": "refuse"}) == "__end__"
    assert after_assemble({"outcome": "continue"}) == "agent_core"


def test_tool_start_detail_names_the_argument_each_tool_is_judged_on():
    assert _tool_start_detail("search_corpus", {"query": "revenue"}) == {
        "query": "revenue"
    }
    assert _tool_start_detail("inspect_schema", {"table_id": "tbl_s_orders"}) == {
        "table_id": "tbl_s_orders"
    }
    assert _tool_start_detail("sample_rows", {"table_id": "tbl_s_orders"}) == {
        "table_id": "tbl_s_orders"
    }
    assert _tool_start_detail("run_query", {"sql": "SELECT 1"}) == {"sql": "SELECT 1"}
    assert _tool_start_detail("ask_user", {"question": "which year?", "why": "ambiguous"}) == {
        "question": "which year?",
        "why": "ambiguous",
    }
    # An unknown tool still emits a start row, just without a detail payload.
    assert _tool_start_detail("some_new_tool", {"anything": 1}) == {}
