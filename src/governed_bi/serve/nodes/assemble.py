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
    """Render retrieval into ``delivery``, not ``messages``.

    ``messages`` is checkpointed and ``add_messages``-reduced, so the block would be re-sent
    and would add a second human message that ``turn_index`` counts. ``config`` carries the corpus.
    """
    if state.get("path_kind") in TERMINAL_PATH_KINDS:
        return {}

    cfg = runtime_config(config)
    assets_by_id = resolve_assets_by_id(cfg)
    retrieved = state.get("retrieved") or {}
    schemas = list(state.get("schemas") or ())
    budget = _budget_chars(state, cfg)

    # Filled only when the char budget bit. Without it, dropped bodies and tables leave no
    # signal, so a table that was routed, retrieved, and then evicted matches one that rendered.
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
    for source in (state, state.get("settings_resolved") or {}, cfg):
        if not isinstance(source, Mapping):
            continue
        value = source.get("context_budget_chars")
        if value is not None:
            return int(value)
    return DEFAULT_CONTEXT_BUDGET


