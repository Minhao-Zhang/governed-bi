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

    ``messages`` is checkpointed and ``add_messages``-reduced, so appending the block re-sends
    every prior turn's context to the provider; it also adds a second human message per turn,
    and both ``api/graph_app.py`` and ``POST /chat`` derive ``turn_index`` by counting those.
    ``agent_core`` passes the block to the agent as an ephemeral message instead. The block is
    not lost — it is in ``delivery``, hashed, and the record publishes ``context_hash``.

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

    # Out-parameter, filled only when the char budget bit. Without it the eviction ladder drops
    # asset bodies and whole pulled-in tables with no signal anywhere, so a gold table that was
    # routed, licensed and then evicted for space is indistinguishable from one that rendered.
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


