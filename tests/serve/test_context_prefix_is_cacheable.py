"""The context block is re-sent on every model call. It is not a system message or a user turn.
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from governed_bi.serve.nodes.agent_core import _cache_point, _with_block


def _convo() -> list[Any]:
    return [
        HumanMessage("how many customers"),
        AIMessage("let me look"),
        ToolMessage(content="3 rows", tool_call_id="c1"),
    ]




def test_the_block_is_byte_identical_on_every_call() -> None:
    """A prefix that differs per call is uncacheable everywhere, whatever else is wired."""
    block = "CONTEXT:\ntable a\ntable b"
    first = _with_block(_convo(), block)
    second = _with_block(_convo() + [AIMessage("more"), ToolMessage("x", tool_call_id="c2")], block)

    assert first[0].content == second[0].content
    assert first[0].content == block


def test_the_block_precedes_the_question_so_the_prefix_is_a_prefix() -> None:
    """Caching reads a *leading* run of tokens. Appending the block after the conversation
    would put a 22k-token wall behind a growing tail and cache nothing — and it is also the
    arrangement that made the agent reply "Understood. I'll use the specified joins…"."""
    out = _with_block(_convo(), "BLOCK")

    assert out[0].content == "BLOCK"
    assert [m.content for m in out[1:]] == [m.content for m in _convo()]




class _Converse:
    """A model exposing a cache-point factory."""

    @classmethod
    def create_cache_point(cls, cache_type: str = "default") -> dict[str, Any]:
        return {"cachePoint": {"type": cache_type}}


class _Plain:
    """OpenAI / the internal proxy: no breakpoint, caching is automatic or absent."""


def test_only_a_converse_model_gets_a_breakpoint() -> None:
    """Sending ``cachePoint`` to an OpenAI-shaped client puts an unknown dict in the content
    list. Duck-typed on the documented factory rather than on a class name, so a wrapper or a
    version bump does not silently stop producing one."""
    assert _cache_point(_Converse()) == {"cachePoint": {"type": "default"}}
    assert _cache_point(_Plain()) is None
    assert _cache_point(None) is None


def test_a_provider_whose_factory_raises_gets_no_breakpoint() -> None:
    """Fail soft: an unusable factory must cost the saving, not the turn."""

    class _Broken:
        @classmethod
        def create_cache_point(cls) -> dict[str, Any]:
            raise RuntimeError("nope")

    assert _cache_point(_Broken()) is None


def test_the_breakpoint_rides_after_the_block_not_before_it() -> None:
    """The marker means "cache everything up to here", so it belongs at the end of the stable
    prefix. Before the text it would cache nothing."""
    point = _Converse.create_cache_point()
    out = _with_block(_convo(), "BLOCK", point)

    assert out[0].content == ["BLOCK", point]


def test_without_a_breakpoint_the_content_stays_a_plain_string() -> None:
    """The OpenAI and proxy configs must be byte-identical to before this existed."""
    assert _with_block(_convo(), "BLOCK")[0].content == "BLOCK"




def test_the_usage_row_counts_the_model_calls_it_aggregated() -> None:
    """An agent loop reports one usage row, so without this the repeated share is a guess.

    It is the only route to an exact figure here: the internal proxy reports no
    ``cache_read_tokens`` at all, in any of the three full runs to date.
    """
    from governed_bi.serve.usage import reported_tokens

    def _msg(i: int, o: int) -> AIMessage:
        m = AIMessage("x")
        m.usage_metadata = {"input_tokens": i, "output_tokens": o, "total_tokens": i + o}
        return m

    got = reported_tokens([_msg(100, 5), AIMessage("no metadata"), _msg(200, 7)])

    assert got is not None
    assert got["input_tokens"] == 300 and got["output_tokens"] == 12
    assert got["model_calls"] == 2, "a message carrying no usage metadata is not a paid call"


def test_no_reported_usage_still_means_unmeasured_not_zero_calls() -> None:
    """``None`` is the existing contract for "the provider said nothing"; a ``model_calls: 0``
    beside it would read as a free turn, which is the shape ``NO_TOKEN_USAGE`` exists to refuse.
    """
    from governed_bi.serve.usage import reported_tokens

    assert reported_tokens([AIMessage("x")]) is None




def test_the_delivery_channel_carries_what_the_budget_evicted() -> None:
    """Merging delivery must keep ``evicted``. Rebuilding the dict dropped it."""
    from governed_bi.serve.delivery import DeliveryTracker
    from governed_bi.serve.state import merge_delta

    evicted = {"bodies_dropped": 3, "tables_dropped": 1, "dropped_ids": ["s.t"]}
    assembled = {"context_block": "B", "context_hash": "h", "tool_delivered": {}, "evicted": evicted}
    merged = merge_delta(assembled, DeliveryTracker({"a": "1"}).merge_into(assembled))

    assert merged["evicted"] == evicted
    assert merged["context_hash"] == "h" and merged["tool_delivered"] == {"a": "1"}
    assert merged["context_block"] == "B", "the rendered block must survive the tool writes too"


def test_a_turn_whose_block_fit_carries_no_evicted_key() -> None:
    """Absent and empty must stay distinguishable: ``{}`` would read as "we checked and dropped
    nothing", which is a different fact from "the budget never bit"."""
    from governed_bi.serve.delivery import DeliveryTracker
    from governed_bi.serve.state import merge_delta

    for assembled in (
        {"context_block": "B", "context_hash": "h", "tool_delivered": {}},
        {"context_block": "B", "context_hash": "h", "tool_delivered": {}, "evicted": {}},
    ):
        merged = merge_delta(assembled, DeliveryTracker().merge_into(assembled))
        assert not merged.get("evicted"), merged


def test_the_delivery_channel_starts_each_turn_empty() -> None:
    """The hazard a merge rule introduces, closed by ``PER_TURN_RESET``."""
    from governed_bi.serve.state import PER_TURN_RESET, merge_delta

    stale = {"context_block": "B", "context_hash": "h", "evicted": {"tables_dropped": 1}}
    assert merge_delta(stale, PER_TURN_RESET["delivery"]) is None


def test_the_eval_row_reports_what_was_evicted() -> None:
    """The consumer half. ``project_turn`` reads it off ``state["delivery"]``."""
    from governed_bi.eval.harness import project_turn

    evicted = {"tables_dropped": 2, "over_budget": 512}
    row = project_turn(
        {
            "answer": {"outcome": "answered", "record": {}},
            "delivery": {"context_hash": "h", "evicted": evicted},
            "messages": [],
        },
        question={"question_id": "q1", "db_id": "d"},
        config="a",
    )
    assert row["context_evicted"] == evicted


def test_assemble_records_an_eviction_only_when_the_budget_bites() -> None:
    """The producer half, against the real renderer, so the three cannot drift apart."""
    from governed_bi.serve.context import render_context

    pieces = {
        "by_type": {"table": [f"s.t{i}" for i in range(40)]},
        "schema_ranking": [],
    }

    roomy: dict = {}
    render_context(retrieved=pieces, assets_by_id=_wide_tables(), schemas=["s"],
                   budget_chars=10**7, evicted=roomy)
    assert roomy == {}, "a block that fits must record no eviction"

    tight: dict = {}
    render_context(retrieved=pieces, assets_by_id=_wide_tables(), schemas=["s"],
                   budget_chars=800, evicted=tight)
    assert tight, "the budget bit and nothing was recorded"


def _wide_tables(n: int = 40) -> dict[str, Any]:
    """``n`` table assets whose bodies are the first rung of the eviction ladder."""
    return {
        f"s.t{i}": type(
            "T", (), {
                "id": f"s.t{i}", "asset_type": type("A", (), {"value": "table"})(),
                "physical_name": f"t{i}", "schema": "s", "columns": [],
                "summary": "x" * 400, "body": "y" * 400,
            },
        )()
        for i in range(n)
    }


def test_assemble_writes_the_eviction_onto_the_delivery_it_returns() -> None:
    """The link between the renderer and everything downstream, which nothing covered."""
    from governed_bi.serve.nodes.assemble import assemble_node

    state = {
        "question": "how many customers",
        "schemas": ["s"],
        "retrieved": {"by_type": {"table": [f"s.t{i}" for i in range(40)]}, "schema_ranking": []},
    }
    config = {"configurable": {"thread_id": "t-evict", "assets_by_id": _wide_tables()}}

    tight = assemble_node({**state, "context_budget_chars": 800}, config)["delivery"]
    assert tight.get("evicted"), (
        "assemble dropped what the budget evicted, so nothing downstream can see it: "
        f"{sorted(tight)}"
    )
    assert tight["evicted"].get("bodies_dropped"), tight["evicted"]

    roomy = assemble_node({**state, "context_budget_chars": 10**7}, config)["delivery"]
    assert "evicted" not in roomy, (
        "a turn whose block fit records an eviction; absent and empty must stay different facts"
    )


def test_the_served_record_carries_what_the_budget_evicted(
    two_schema_index, two_schema_assets
) -> None:
    """End to end into ``record``, which is what the audit surface serves and what an eval row is projected from."""
    from governed_bi.govern.policy import GovernancePolicy
    from governed_bi.serve.graph import compile_graph

    def _serve(budget: int | None, thread: str) -> dict[str, Any]:
        settings: dict[str, Any] = {"route_top_n": 1, "candidate_depth": 50}
        if budget is not None:
            settings["context_budget_chars"] = budget
        state = {
            "question": "customer", "thread_id": thread, "turn_index": 1, "run_id": "r-evict",
            "turn_id": f"turn-{thread}", "question_id": "q-evict", "db_id": "sales_a",
            "attempt_id": "a-evict", "corpus_content_hash": "c", "prompt_set_hash": "p",
            "settings_resolved": settings, "n_re_served": 0, "messages": [], "usage": [],
            "route_top_n": 1, "candidate_depth": 50, "identity": {"token": "tok"},
        }
        config = {
            "configurable": {
                "thread_id": thread,
                "policy": GovernancePolicy(guard_rules_enabled={}),
                "index": two_schema_index,
                "assets_by_id": two_schema_assets,
            }
        }
        return compile_graph().invoke(state, config)["answer"]["record"]

    tight = _serve(120, "t-evict-tight")
    assert tight["evicted"], (
        "the served record does not say the budget bit, so nothing downstream of it — the "
        "audit surface, an eval row — can distinguish a table that was rendered from one "
        "dropped for space"
    )

    roomy = _serve(None, "t-evict-roomy")
    assert roomy["evicted"] is None, "a turn whose block fit must record no eviction"
