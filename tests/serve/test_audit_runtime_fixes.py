"""Pins from the 2026-08-08 LangGraph runtime audit.

Defects that already burned a turn or a measurement: accept soft-crash leaking into
guard, narrate able to crash an answered turn, rewrite fall-through after crash, and
create_agent's 9999 recursion ceiling.
"""

from __future__ import annotations

from typing import Any

import pytest

from governed_bi.serve.graph import (
    _CANCELLABLE,
    _after_accept,
    _after_rewrite,
    _node_timeout,
)
from governed_bi.serve.nodes.agent_core import _recursion_limit


def test_accept_soft_crash_routes_to_stamp_not_guard() -> None:
    assert _after_accept({"path_kind": "crashed"}) == "stamp"
    assert _after_accept({"path_kind": None}) == "guard"
    assert _after_accept({}) == "guard"


def test_rewrite_crash_short_circuits_to_stamp() -> None:
    assert _after_rewrite({"path_kind": "crashed"}) == "stamp"
    assert _after_rewrite({"path_kind": None}) == "negative_gate"


def test_narrate_is_not_a_cancellable_rail() -> None:
    assert "narrate" not in _CANCELLABLE
    assert _node_timeout("narrate") is None
    assert _node_timeout("guard") is not None


def test_narrate_swallows_errors_instead_of_crashing_an_answered_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio

    from governed_bi.serve.nodes import narrate as narrate_mod

    monkeypatch.setattr(
        narrate_mod,
        "last_ai_text",
        lambda _state: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    out = asyncio.run(
        narrate_mod.narrate_node(
            {
                "path_kind": "answered",
                "question": "how many?",
                "result_table": {"columns": ["n"], "rows": [[1]], "row_count": 1},
                "messages": [],
                "turn_index": 1,
            },
            {"configurable": {}},
        )
    )
    assert out == {}
    assert "failure" not in out
    assert "path_kind" not in out


def test_narrate_generate_failure_degrades_without_failure_channel() -> None:
    import asyncio

    from governed_bi.serve.nodes.narrate import narrate_node

    class Boom:
        async def ainvoke(self, *_a: Any, **_k: Any) -> Any:
            raise RuntimeError("provider down")

    out = asyncio.run(
        narrate_node(
            {
                "path_kind": "answered",
                "question": "how many?",
                "generated_sql": "select 1",
                "result_table": {"columns": ["n"], "rows": [[1]], "row_count": 1},
                "messages": [],
                "turn_index": 1,
            },
            {"configurable": {"utility_model": Boom()}},
        )
    )
    assert "failure" not in out
    assert "path_kind" not in out
    assert out.get("answer_text") is None


def test_agent_recursion_limit_defaults_and_env(monkeypatch: pytest.MonkeyPatch) -> None:
    from governed_bi.register.knobs import knob_default

    assert _recursion_limit({}) == int(knob_default("agent_recursion_limit"))
    assert _recursion_limit({"knobs_resolved": {"agent_recursion_limit": 17}}) == 17
    monkeypatch.setenv("GOVERNED_BI_AGENT_RECURSION_LIMIT", "9")
    assert _recursion_limit({"knobs_resolved": {"agent_recursion_limit": 17}}) == 9


def test_n_re_served_is_not_a_quotability_gate() -> None:
    from governed_bi.measure import gates
    from governed_bi.register.record import GATE_CONDITIONS

    assert "n_re_served" not in GATE_CONDITIONS
    assert "n_re_served" not in gates.GATE_IMPLEMENTATIONS


def test_proxy_chat_model_retries_default_matches_knob() -> None:
    import inspect

    from governed_bi.model.proxy_gateway import build_chat_model
    from governed_bi.register.knobs import knob_default

    default = inspect.signature(build_chat_model).parameters["max_retries"].default
    assert default == int(knob_default("llm_max_retries"))


def test_capabilities_admit_hitl_is_not_durable() -> None:
    """The projection takes its session, so there is no module global to swap for a stub."""
    from governed_bi.api.routes import capabilities_for

    class _S:
        connector = type("C", (), {"dialect": "postgres"})()
        agent_model = None
        knobs_resolved: dict[str, Any] = {}

    caps = capabilities_for(_S())
    assert caps["checkpoint_durable"] is False
    assert caps["hitl_survives_process_restart"] is False
