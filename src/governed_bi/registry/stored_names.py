"""Old field names on eval artifacts already stored under ``runs/``.

Live writes and the HTTP boundary use the current names only. A reader of a
file written before the plain-name rename accepts the previous key, and only
there: passing an old key on a new row is not a second spelling.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

# Previous record key, for files under runs/ that this tree no longer writes.
OLD_KEY = {
    "retrieved_tables": "licensed",
    "settings_resolved": "knobs_resolved",
    "channel_hits": "facet_hits",
    "channel_degraded": "facet_degraded",
    "channel_scoring": "facet_channels",
    "n_retrieved_tables": "n_licensed",
}

# Previous reason codes. The histogram joins them to the stage table under the
# current spelling; the file on disk is not rewritten.
OLD_REASON = {
    "nothing_licensed": "nothing_retrieved",
    "licensed_table_evicted": "retrieved_table_dropped",
    "r_table_not_licensed": "r_table_not_retrieved",
}


def artifact_has(row: Mapping[str, Any], key: str) -> bool:
    """True when the row carries ``key`` or, for an old file, its previous name."""
    if key in row:
        return True
    old = OLD_KEY.get(key)
    return old is not None and old in row


def artifact_get(row: Mapping[str, Any], key: str, default: Any = None) -> Any:
    """``row[key]``, else the previous name when that is the key the file used.

    Membership, not ``.get``: a recorded ``None`` is a value, and a missing key
    is a file that never wrote the field. The new name wins when both are present.
    """
    if key in row:
        return row[key]
    old = OLD_KEY.get(key)
    if old is not None and old in row:
        return row[old]
    return default


def current_reason(reason: str) -> str:
    """The stage-table spelling of a reason code an old artifact may still store."""
    return OLD_REASON.get(reason, reason)


def alias_row(row: Mapping[str, Any]) -> Mapping[str, Any]:
    """A copy that carries the current key when the file still has the previous one.

    Returned unchanged when the row already uses the current names, so a live row
    is not copied and an old key on it is not invented.
    """
    if not any(old in row for old in OLD_KEY.values()):
        return row
    out = dict(row)
    for new, old in OLD_KEY.items():
        if new not in out and old in out:
            out[new] = out[old]
    return out
