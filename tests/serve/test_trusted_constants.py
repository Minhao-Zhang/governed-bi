"""A request must not be able to name a run constant. ADR 0007 §2, one layer out.

``accept`` already refuses the client's *state*: ``run_id``, ``corpus_content_hash`` and the
other provenance fields are minted server-side and anything a client sends in them is ignored
rather than merged. The seven keys on ``configurable`` had no such rule, and they are the ones
that decide what governance is: ``policy``, ``corpus``, ``assets_by_id``, ``connector``,
``index``, ``structure``, ``agent_model``.

``make_graph`` binds them with ``with_config``, and LangGraph merges caller config **over** a
bound default. That is deliberate and load-bearing for ``thread_id`` — which is exactly why the
binding is used at all — and it applied identically to the six keys beside it. A request to
``/threads/{id}/runs`` carrying ``config.configurable.policy`` replaced the
``GovernancePolicy``; one carrying ``assets_by_id`` replaced the corpus every tool licenses
against.
"""

from __future__ import annotations

from typing import Any

import pytest

from governed_bi.govern.policy import GovernancePolicy
from governed_bi.serve.runtime import configurable, trust, trusted


@pytest.fixture(autouse=True)
def _no_trust_leak():
    """Clear the registry around every test.

    Process-level state in a test suite is a shared mutable, and the failure mode is the worst
    kind: a test that passes only because an earlier one registered something.
    """
    trust()
    yield
    trust()


def test_without_registration_nothing_changes():
    """The CLI and every in-repo caller pass their own config and register nothing."""
    request = {"configurable": {"policy": "CLIENT", "thread_id": "t-1"}}
    assert configurable(request) == {"policy": "CLIENT", "thread_id": "t-1"}
    assert trusted() == {}


def test_a_request_cannot_replace_the_governance_policy():
    """The reproduction, reduced to the reader every node goes through."""
    real = GovernancePolicy(guard_rules_enabled={"g_instruction_override": True})
    trust({"policy": real, "assets_by_id": {"sales.customers": object()}})

    hostile = {"configurable": {
        "policy": GovernancePolicy(guard_rules_enabled={}),   # every rule disarmed
        "assets_by_id": {"pwned": 1},                          # a corpus of its own
        "thread_id": "t-1",
    }}
    seen = configurable(hostile)

    assert seen["policy"] is real, (
        "a request replaced the GovernancePolicy for the run. Every guard rule in the "
        "substituted policy is disarmed, and `guard` reads this key."
    )
    assert list(seen["assets_by_id"]) == ["sales.customers"], (
        f"a request replaced the corpus: {list(seen['assets_by_id'])}. `tool_bounds_from_state` "
        "licenses against it, so this is what `run_query` would have checked SQL against."
    )
    assert seen["thread_id"] == "t-1", (
        "thread_id must still come from the request — it is per conversation, not a run "
        "constant, and forcing it would collapse every conversation into one."
    )


def test_an_absent_config_still_yields_the_trusted_constants():
    """A node called with no config at all must not see an empty world.

    Before, ``configurable(None)`` returned ``{}`` and a node then took its "no index means
    lexical only" or "no corpus means an empty one" branch — degrading rather than failing,
    which is the shape ``session.py``'s docstring is entirely about.
    """
    real = GovernancePolicy(guard_rules_enabled={})
    trust({"policy": real})
    assert configurable(None)["policy"] is real
    assert configurable({})["policy"] is real
    assert configurable({"configurable": "not a mapping"})["policy"] is real


def test_every_node_reads_config_through_the_shared_reader():
    """One merge is only enough if there is one reader.

    Two nodes subscripted ``config["configurable"]`` directly — ``guard`` for the policy, of
    all keys — and each was a way around the check above. Structural because the behavioural
    version would need a node that does the wrong thing in order to observe it.
    """
    import re
    from pathlib import Path

    src = Path(__file__).resolve().parent.parent.parent / "src" / "governed_bi" / "serve"
    allowed = {"runtime.py", "session.py", "__main__.py", "resume.py"}
    pattern = re.compile(r"""(?<!``)config(?:\s*or\s*\{\})?\[?["']?configurable""")
    offenders: dict[str, list[int]] = {}
    for path in sorted(src.rglob("*.py")):
        if path.name in allowed:
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith(("#", "*", '"', "'")) or "``" in stripped:
                continue
            if pattern.search(stripped):
                offenders.setdefault(path.name, []).append(n)
    assert not offenders, (
        f"{offenders} reach around governed_bi.serve.runtime.configurable, which is where a "
        "request's attempt to name a run constant is refused. Read through it instead."
    )


def test_trust_is_registered_by_the_server_factory():
    """``make_graph`` is the only caller, and it must actually call.

    Structural rather than executed: ``make_graph`` builds a Postgres connector and seeds a
    corpus, so running it needs a live server and a credential. The assertion that matters is
    that the call is there at all — the merge above is already covered.
    """
    from pathlib import Path

    source = (
        Path(__file__).resolve().parent.parent.parent
        / "src" / "governed_bi" / "api" / "graph_app.py"
    ).read_text(encoding="utf-8")
    # Code only, not the docstring: `make_graph`'s prose explains at length *why* the
    # `with_config` binding was removed, and a naive substring check reads its own explanation
    # as the defect. Caught by this test failing on the commit that fixed the thing it tests.
    after_def = source.split("def make_graph()", 1)[1]
    body = after_def.split('"""', 2)[2]
    assert "trust(" in body, (
        "make_graph does not declare the session's constants trusted, so a request can name "
        "any of them and the shared reader has nothing to force back."
    )
    assert "with_config" not in body, (
        "make_graph binds the live constants onto config again. They are not JSON, so the "
        "server 500s serialising the assistant config for /assistants/{id}/schemas -- and a "
        "caller's config merges *over* a bound default, which is the override this fixes."
    )


def test_model_id_prefers_the_provider_id_over_the_langchain_class_label():
    """``usage[].model`` and ``knobs_resolved["chat_model"]`` are compared, so they must agree.

    The usage row asked ``_llm_type`` first and recorded ``"openai-chat"`` for every OpenAI
    model ever served, while the knob beside it held the real id. One turn reporting two
    different models, on a ``Role.comparability`` field.
    """
    from governed_bi.serve.runtime import model_id

    class Fake:
        model_name = "gpt-5.6-luna"
        _llm_type = "openai-chat"

    assert model_id(Fake()) == "gpt-5.6-luna"

    class NoId:
        _llm_type = "scripted"

    assert model_id(NoId()) is None, (
        "an object carrying no model id must say so, not fall back to a class label here — "
        "the caller decides what to record when there is nothing to record"
    )


def test_the_usage_row_and_the_knob_report_the_same_model(
    two_schema_index, two_schema_assets, guard_off_policy
):
    """End to end: one turn, one model, one answer to what it was."""
    from langchain_core.messages import AIMessage

    from governed_bi.serve.graph import compile_graph
    from governed_bi.serve.scripted_model import ScriptedChatModel
    from governed_bi.serve.session import from_assets

    class Named(ScriptedChatModel):
        model_name: str = "gpt-5.6-luna"

    model: Any = Named(responses=[AIMessage(content="one sensor")])
    # Through `from_assets`, not by constructing `Session` directly: `chat_model` is set
    # there, and the property under test is that the two writers agree. The knob used to be
    # spelled `llm_model`, which `KNOB_REGISTER` never declared.
    session = from_assets(
        list(two_schema_assets.values()), connector=None, policy=guard_off_policy,
        db_id="ops_b", corpus_content_hash_="c", agent_model=model,
    )
    config = session.configurable()
    config["configurable"]["thread_id"] = "t-model"
    out = compile_graph().invoke(
        {**session.turn("sensors voltage reading per device"), "route_top_n": 1}, config
    )
    record = out["answer"]["record"]
    usage = list(record.get("usage") or [])
    assert usage, f"no usage row: path_kind={out.get('path_kind')!r}"
    assert usage[0]["model"] == session.knobs_resolved["chat_model"] == "gpt-5.6-luna", (
        f"usage says {usage[0]['model']!r}, knobs_resolved says "
        f"{session.knobs_resolved.get('chat_model')!r}"
    )


# --- The other caller-writable channel: the graph's own `input` (audit §4.3) -----------------
#
# `trust()` above closes `configurable`. It was the only one closed. `langgraph_api` forwards
# the client's `input` dict to the graph unfiltered, `PER_TURN_RESET` does not clear
# `TEST_HOOKS`, and `int_knob` reads state before `knobs_resolved` -- so a request could set
# its own `route_top_n` and the record would then publish the default it did not use.


def _stop_at(seen: dict) -> Any:
    """An `accept` that records what reached it and ends the turn immediately."""

    def accept(state: dict, config: Any) -> dict:
        seen.update(
            route_top_n=state.get("route_top_n"),
            retrieve_hooks=state.get("retrieve_hooks"),
            identity=state.get("identity"),
            messages=len(state.get("messages") or []),
        )
        return {
            "path_kind": "crashed",
            "failure": {"stage": "accept", "error_type": "Stop", "detail": "stop"},
        }

    return accept


def test_a_request_cannot_write_a_knob_into_graph_state():
    """The served graph declares `input_schema`, so undeclared keys never enter state.

    Dropped at the entry rather than policed: an allowlist has to be maintained against every
    channel added later, and the failure mode of forgetting one is silent.
    """
    from governed_bi.serve.graph import as_sync, build_graph

    seen: dict = {}
    as_sync(build_graph(accept=_stop_at(seen)).compile()).invoke(
        {
            "messages": [{"role": "user", "content": "how many sensors"}],
            "route_top_n": 99,
            "retrieve_hooks": {"forced": True},
            "identity": {"token": "someone-else"},
        }
    )
    assert seen["messages"] == 1, "the conversation must still get through"
    assert seen["route_top_n"] is None, f"client set route_top_n={seen['route_top_n']!r}"
    assert seen["retrieve_hooks"] is None, "client reached a test hook"
    assert seen["identity"] is None, "client named its own identity"


def test_the_in_process_graph_still_takes_a_whole_turn():
    """The paired negative, and the reason `input_schema` is not applied to both variants.

    `serve/__main__`, `eval/` and `/chat` build the turn themselves through `Session.turn()`
    and pass the whole of `ServeState`. Restricting that entry too would not be a hardening,
    it would delete the only way those three callers can run.
    """
    from governed_bi.serve.graph import as_sync, build_graph

    seen: dict = {}

    def guard_stub(state: dict) -> dict:
        seen["route_top_n"] = state.get("route_top_n")
        seen["question"] = state.get("question")
        return {
            "path_kind": "crashed",
            "failure": {"stage": "guard", "error_type": "Stop", "detail": "stop"},
        }

    graph = build_graph()
    graph.nodes.pop("guard")
    graph.add_node("guard", guard_stub)
    as_sync(graph.compile()).invoke({"question": "how many sensors", "route_top_n": 7})
    assert seen == {"route_top_n": 7, "question": "how many sensors"}
