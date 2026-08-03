"""Terminal path nodes — refuse / decline — before ``stamp``."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from governed_bi.govern.guard import GUARD_PUBLIC_MESSAGE

__all__ = ["refuse_node", "decline_node"]


def refuse_node(state: Mapping[str, Any]) -> dict[str, Any]:
    """Guard / negative-example refusal. Public text never leaks rule detail."""
    reason = state.get("terminal_reason")
    if not reason:
        guard = state.get("guard") or {}
        if guard.get("outcome") == "blocked":
            reason = "guard"
        else:
            reason = "negative_example"
    return {
        "path_kind": "refuse",
        "terminal_reason": reason,
        "messages": [{"role": "assistant", "content": GUARD_PUBLIC_MESSAGE}],
    }


def decline_node(state: Mapping[str, Any]) -> dict[str, Any]:
    """Retrieval / connect decline. ``terminal_reason`` carries the refused_by value."""
    reason = state.get("terminal_reason") or "no_schema_matched"
    return {
        "path_kind": "decline",
        "terminal_reason": reason,
    }
