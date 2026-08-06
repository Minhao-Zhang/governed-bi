"""Assemble node — render USER context + Delivery hashes (ADR 0005 §3.6)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from langchain_core.runnables import RunnableConfig

from governed_bi.serve.context import render_context
from governed_bi.serve.runtime import (
    DEFAULT_CONTEXT_BUDGET,
)
from governed_bi.serve.runtime import (
    assets_by_id as resolve_assets_by_id,
)
from governed_bi.serve.runtime import (
    configurable as runtime_config,
)
from governed_bi.serve.state import TERMINAL_PATH_KINDS

__all__ = ["assemble_node"]


def assemble_node(state: dict, config: RunnableConfig) -> dict:
    """Render retrieval context into ``delivery``. **Not into ``messages``.**

    It used to append the whole block to ``messages`` as a human turn, and that one line cost
    three separate things:

    * **Every prior turn's context was re-sent.** ``messages`` is checkpointed and
      ``add_messages``-reduced, so turn 3 handed the provider turn 1's and turn 2's context
      blocks again — paid for, and describing a retrieval that is no longer the current one.
    * **``turn_index`` came out at 2n-1.** Both ``api/graph_app.py`` and ``POST /chat`` number
      the turn by counting human messages, and this added a second one per turn. Every
      multi-turn record after the first was misnumbered, which is what ``usage``'s per-turn
      projection filters on.
    * ``messages`` stopped being the conversation and became the conversation plus its
      scaffolding, so no reader could tell the two apart.

    ``agent_core`` now passes the block into the agent as an ephemeral message that is never
    written back. The block itself is not lost: it is in ``delivery``, hashed, and the record
    publishes ``context_hash`` — which is where an audit was always meant to read it.

    Declares ``config`` so :func:`~governed_bi.serve.wrap.wrap_node` forwards
    ``RunnableConfig`` (corpus / assets live under ``configurable``).
    """
    if state.get("path_kind") in TERMINAL_PATH_KINDS:
        return {}

    cfg = runtime_config(config)
    assets_by_id = resolve_assets_by_id(cfg)
    retrieved = state.get("retrieved") or {}
    schemas = list(state.get("schemas") or ())
    budget = _budget_chars(state, cfg)

    # Out-parameter, filled only when the char budget actually bit. Before this the eviction
    # ladder dropped asset bodies and whole pulled-in tables with no signal anywhere, so a gold
    # table that was routed, licensed and then evicted for space was indistinguishable from one
    # that was rendered — the blind spot sitting exactly between "table selection" and
    # "generation".
    evicted: dict[str, Any] = {}
    block, context_hash = render_context(
        retrieved=retrieved,
        assets_by_id=assets_by_id,
        schemas=schemas,
        budget_chars=budget,
        evicted=evicted,
    )
    delivery: dict[str, Any] = {
        "context_block": block,
        "context_hash": context_hash,
        "tool_delivered": {},
        "delivery_hash": None,
    }
    if evicted:
        delivery["evicted"] = evicted
    return {"delivery": delivery}


def _budget_chars(state: Mapping[str, Any], cfg: Mapping[str, Any]) -> int:
    for source in (state, state.get("knobs_resolved") or {}, cfg):
        if not isinstance(source, Mapping):
            continue
        value = source.get("context_budget_chars")
        if value is not None:
            return int(value)
    return DEFAULT_CONTEXT_BUDGET


