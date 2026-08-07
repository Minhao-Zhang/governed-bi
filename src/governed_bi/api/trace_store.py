"""Durable local turn log for post-serve audit.

Append-only JSONL under ``runs/serve/``. Write failures are reported, never raised —
a served turn must not become a failure because the log could not be written.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping

from ..paths import REPO_ROOT

__all__ = [
    "TURN_LOG_DIR",
    "append_turn",
    "list_turns",
    "get_turn",
    "SUMMARY_FIELDS",
]


#: Where turns land. Overridable so a test never writes into the repository's own log.
TURN_LOG_DIR = Path(os.environ.get("GOVERNED_BI_TURN_LOG_DIR") or (REPO_ROOT / "runs" / "serve"))

#: List-view columns (subset of record field names), display order.
SUMMARY_FIELDS: tuple[str, ...] = (
    "turn_id",
    "run_id",
    "thread_id",
    "question_id",
    "db_id",
    "outcome",
    "terminal_reason",
    "schemas",
    "generated_sql",
    "latency_sec",
    # The attempt ledger. Here because a transcript rebuilt from this log has to show the same
    # governance badge the live turn showed: without it an earlier turn rendered "no SQL
    # attempted" directly above its own SQL panel — one row of the artifact contradicting
    # itself, which is the shape this repository keeps re-finding.
    "execution",
)


def _log_file(when: datetime) -> Path:
    return TURN_LOG_DIR / f"{when.strftime('%Y-%m-%d')}.jsonl"


def append_turn(
    record: Mapping[str, Any],
    *,
    question: str | None = None,
    answer_text: str | None = None,
    outcome: str | None = None,
) -> tuple[str | None, str | None]:
    """Append one turn. Returns ``(turn_id, error)`` — never raises.

    ``question`` and ``answer_text`` are carried **beside** the record rather than merged
    into it. ``undeclared_keys`` exists precisely to catch a key nobody declared appearing
    in a record, and a log entry that quietly grew two of them would make every record
    read out of this file fail that check for a reason the reader cannot see.
    """
    turn_id = str(record.get("turn_id") or "") or None
    entry = {
        "asked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "question": question,
        "answer_text": answer_text,
        "outcome": outcome if outcome is not None else record.get("outcome"),
        "record": dict(record),
    }
    try:
        TURN_LOG_DIR.mkdir(parents=True, exist_ok=True)
        with _log_file(datetime.now(timezone.utc)).open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, default=str) + "\n")
    except OSError as err:
        # A turn that answered is not a turn that failed. Reported, not swallowed.
        return turn_id, f"{type(err).__name__}: {err}"
    return turn_id, None


def _entries() -> Iterator[dict[str, Any]]:
    """Every logged turn, newest file first and newest line first within a file."""
    if not TURN_LOG_DIR.is_dir():
        return
    for path in sorted(TURN_LOG_DIR.glob("*.jsonl"), reverse=True):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                # One truncated line must not hide every turn behind it. v1's loader
                # raised on the first bad file and discarded a paid 69-schema build.
                continue
            if isinstance(parsed, dict):
                yield parsed


def list_turns(limit: int = 50, thread_id: str | None = None) -> list[dict[str, Any]]:
    """Newest turns first, as summaries. ``thread_id`` narrows to one conversation.

    **Why the filter exists.** A turn's governed record lives in per-turn graph state
    (``answer``, ``generated_sql``, ``execution``), which ``PER_TURN_RESET`` clears each turn —
    so a thread's checkpoint can only ever describe its *newest* turn. Reopening a two-turn
    conversation showed two questions and one audit card, because there was one record to show.
    This log is the only place every turn of a conversation survives, so it has to be askable
    per conversation; without the filter a client would page the whole log and filter by hand.

    ``missing_required`` is computed here rather than stored, so an entry written before a
    register row existed is judged by today's register — the point of the column is "is
    this turn quotable", and that is a question about the current declaration.
    """
    from ..register.record import missing_required

    out: list[dict[str, Any]] = []
    for entry in _entries():
        record = entry.get("record") or {}
        if thread_id is not None and record.get("thread_id") != thread_id:
            continue
        summary = {name: record.get(name) for name in SUMMARY_FIELDS}
        summary["asked_at"] = entry.get("asked_at")
        summary["question"] = entry.get("question")
        summary["answer_text"] = entry.get("answer_text")
        summary["outcome"] = entry.get("outcome") or record.get("outcome")
        summary["licensed_count"] = len(record.get("licensed") or ())
        execution = record.get("execution") or {}
        attempts = execution.get("attempts") or ()
        summary["attempts"] = len(attempts)
        summary["attempts_passed"] = sum(1 for a in attempts if (a or {}).get("passed"))
        summary["incomplete_fields"] = len(missing_required(record))
        out.append(summary)
        if len(out) >= max(1, int(limit)):
            break
    return out


def get_turn(turn_id: str) -> dict[str, Any] | None:
    """One logged turn in full, or ``None``.

    A linear scan, newest first. There is no index, because an index over an append-only
    local log is a second source of truth for a lookup that takes milliseconds over the
    volume one developer's machine produces.
    """
    wanted = str(turn_id)
    for entry in _entries():
        if str((entry.get("record") or {}).get("turn_id") or "") == wanted:
            return entry
    return None
