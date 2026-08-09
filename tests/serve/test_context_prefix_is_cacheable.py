"""The repeated context prefix: what makes it cacheable, and what we can and cannot wire.

The agent re-sends a ~22k-token context block on every model call, by design — it cannot be a
``SystemMessage`` (that would make ``prompt_set_hash`` describe text never sent) and cannot sit
in ``messages`` as the client's own turn (the SDK renders it as the user's bubble). Measured on
the 2026-08-09 v3 arm, the repeated prefix is **at most 66.6%** of all input tokens.

Caching removes that repeat without touching any of those three constraints. Only two halves of
it are ours:

* the prefix must be **byte-identical** on every call, or no gateway can cache it;
* the Converse API takes an explicit breakpoint, so ``ChatBedrockConverse`` gets one.

OpenAI and the OpenAI-compatible internal proxy cache a long prefix automatically and accept no
breakpoint. The proxy reports no ``cache_read_tokens`` in any run so far, so the repeated share
is computed from ``usage.model_calls`` rather than from the provider — which is why that field
exists and is tested here too.
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


# ── the property every gateway needs ──────────────────────────────────────────


def test_the_block_is_byte_identical_on_every_call() -> None:
    """A prefix that differs per call is uncacheable everywhere, whatever else is wired.

    The middleware closes over one string for the whole turn, so this is really a regression
    guard: rendering per call, or stamping anything per call into the block, would silently
    remove the entire saving with nothing failing.
    """
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


# ── the half that is provider-specific ────────────────────────────────────────


class _Converse:
    """A model exposing the Converse factory, as ``ChatBedrockConverse`` does."""

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
    """The OpenAI and proxy arms must be byte-identical to before this existed."""
    assert _with_block(_convo(), "BLOCK")[0].content == "BLOCK"


# ── the measurement that replaces the provider's cache counters ───────────────


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


# ── what the budget dropped, which was being destroyed mid-turn ───────────────


def test_the_delivery_merge_carries_what_the_budget_evicted() -> None:
    """``merge_into`` rebuilt a fresh four-key dict, so ``assemble``'s ``evicted`` was destroyed
    in ``agent_core`` on every turn that had one — a silent data loss that lived undetected.

    It is the only record that a table was routed, licensed, *counted as covered* by
    ``table_coverage``, and then dropped for space before the model saw it. Reconstructing 25
    turns of the 2026-08-09 v3 arm offline, the 80 000-char budget bit on 16 of them.
    """
    from governed_bi.serve.delivery import DeliveryTracker

    evicted = {"bodies_dropped": 3, "tables_dropped": 1, "dropped_ids": ["s.t"]}
    merged = DeliveryTracker({"a": "1"}).merge_into(
        {"context_block": "B", "context_hash": "h", "tool_delivered": {}, "evicted": evicted}
    )

    assert merged["evicted"] == evicted
    assert merged["context_hash"] == "h" and merged["tool_delivered"] == {"a": "1"}


def test_a_turn_whose_block_fit_carries_no_evicted_key() -> None:
    """Absent and empty must stay distinguishable: ``{}`` would read as "we checked and dropped
    nothing", which is a different fact from "the budget never bit"."""
    from governed_bi.serve.delivery import DeliveryTracker

    assert "evicted" not in DeliveryTracker().merge_into(
        {"context_block": "B", "context_hash": "h", "tool_delivered": {}}
    )
    assert "evicted" not in DeliveryTracker().merge_into(
        {"context_block": "B", "context_hash": "h", "tool_delivered": {}, "evicted": {}}
    )


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
        arm="a",
    )
    assert row["context_evicted"] == evicted


def test_assemble_records_an_eviction_only_when_the_budget_bites() -> None:
    """The producer half, against the real renderer, so the three cannot drift apart."""
    from governed_bi.serve.context import render_context

    pieces = {
        "by_type": {"table": [f"s.t{i}" for i in range(40)]},
        "schema_ranking": [],
    }
    assets = {
        f"s.t{i}": type(
            "T", (), {
                "id": f"s.t{i}", "asset_type": type("A", (), {"value": "table"})(),
                "physical_name": f"t{i}", "schema": "s", "columns": [],
                "summary": "x" * 400, "body": "y" * 400,
            },
        )()
        for i in range(40)
    }

    roomy: dict = {}
    render_context(retrieved=pieces, assets_by_id=assets, schemas=["s"], budget_chars=10**7,
                   evicted=roomy)
    assert roomy == {}, "a block that fits must record no eviction"

    tight: dict = {}
    render_context(retrieved=pieces, assets_by_id=assets, schemas=["s"], budget_chars=800,
                   evicted=tight)
    assert tight, "the budget bit and nothing was recorded"
