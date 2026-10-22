"""Old keys are accepted only when reading a stored artifact.

An old key on a new row is not a second spelling.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

# Stored-artifact key for each current name. Live writes use the current name.
OLD_KEY = {
    "retrieved_tables": "licensed",
    "settings_resolved": "knobs_resolved",
    "channel_hits": "facet_hits",
    "channel_degraded": "facet_degraded",
    "channel_scoring": "facet_channels",
    "n_retrieved_tables": "n_licensed",
}

# Reason codes on stored files. The file is not rewritten.
OLD_REASON = {
    "nothing_licensed": "nothing_retrieved",
    "licensed_table_evicted": "retrieved_table_dropped",
    "r_table_not_licensed": "r_table_not_retrieved",
}


def artifact_has(row: Mapping[str, Any], key: str) -> bool:
    if key in row:
        return True
    old = OLD_KEY.get(key)
    return old is not None and old in row


def artifact_get(row: Mapping[str, Any], key: str, default: Any = None) -> Any:
    """A recorded ``None`` is a value; a missing key was never written.

    The current name wins when both are present.
    """
    if key in row:
        return row[key]
    old = OLD_KEY.get(key)
    if old is not None and old in row:
        return row[old]
    return default


def current_reason(reason: str) -> str:
    return OLD_REASON.get(reason, reason)


def alias_row(row: Mapping[str, Any]) -> Mapping[str, Any]:
    """Returned unchanged when the row already uses the current names, so a live row is not copied."""
    if not any(old in row for old in OLD_KEY.values()):
        return row
    out = dict(row)
    for new, old in OLD_KEY.items():
        if new not in out and old in out:
            out[new] = out[old]
    return out
