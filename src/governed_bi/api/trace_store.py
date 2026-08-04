"""Durable local turn log, so a served turn can be audited after the fact.

**The record already exists; nothing could read it twice.** ``stamp`` builds the full
record for every turn and ``/chat`` returns it inline — so the one caller who can see it
is the caller who asked the question, once. There was no way to list what a server has
served, no way to fetch a past turn, and therefore no audit surface at all: the
governance ledger, the layer verdicts, the licensed set and the retrieval attributions
were produced, published to a single HTTP response, and dropped.

**JSONL is read and written, with no in-memory index beside it.** A process-local cache
would be faster and would be a second answer to "what did this server serve" — one that
disagrees with the file after a restart, and one whose disagreement is invisible. One
source, appended under ``runs/serve/``, newest file first.

Append-only and best-effort on the write path: a turn that answered must not be turned
into a failure because the audit log could not be written. The failure is *reported* to
the caller as ``logged: false`` rather than swallowed, because "no turns are listed"
and "no turns were served" must not be the same observation.
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
    "last_ai_text",
]


def last_ai_text(state: Mapping[str, Any]) -> str | None:
    """The model's answer, via LangChain's own ``AIMessage.text``.

    Lives here because this module owns the ``answer_text`` concept — it is
    :func:`append_turn`'s parameter — and because it has **two** callers that must not disagree:
    ``routes._shape``, which supplies it to a REST caller that has no message channel to read,
    and ``graph_app._record_node``, which supplies it to the log. It was copied into the second
    one, and ``tools/check_one_implementation.py`` refused that — correctly. Two readers of
    "what did the model say" is how the audit list and the response drift.

    Not hand-flattened. The Responses API returns content as blocks (``[{"type": "text", ...},
    {"type": "reasoning", ...}]``), and an earlier draft walked them itself — which is
    re-implementing something ``langchain-core`` owns, and decision #1 records that v1's three
    layers over ``BaseChatModel`` were a mistake for exactly this reason. ``.text`` already
    concatenates the text blocks and ignores the rest.

    ``human`` and ``tool`` messages are skipped rather than filtered on ``type == "ai"``: a
    provider message type this code has not seen is more likely to be the answer than to be a
    turn of someone else's, and reading it is recoverable where skipping it is silent.
    """
    for message in reversed(state.get("messages") or []):
        kind = str(getattr(message, "type", "") or "")
        if not kind and isinstance(message, Mapping):
            kind = str(message.get("type") or "")
        if kind in ("human", "tool"):
            continue
        text = getattr(message, "text", None)
        if text:
            return str(text)
    return None

#: Where turns land. Overridable so a test never writes into the repository's own log.
TURN_LOG_DIR = Path(os.environ.get("GOVERNED_BI_TURN_LOG_DIR") or (REPO_ROOT / "runs" / "serve"))

#: The columns a list view needs, in display order.
#:
#: Deliberately a **subset of the record's own field names** rather than new ones: the
#: list and the detail view then cannot disagree about what a column means, and a reader
#: who looks up ``terminal_reason`` in ``register/record.py`` finds the declaration that
#: governs both.
SUMMARY_FIELDS: tuple[str, ...] = (
    "turn_id",
    "run_id",
    "thread_id",
    "question_id",
    "db_id",
    "outcome",
    # Why a decline declined. It was in this list before it was a *record* field, so the
    # column existed and was always null -- `record.get("terminal_reason")` read a key
    # nothing wrote, because the value lived in graph state only. Declaring it and
    # stamping it is what made the column mean something.
    "terminal_reason",
    "schemas",
    "generated_sql",
    "cost_est_usd",
    "latency_sec",
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


def list_turns(limit: int = 50) -> list[dict[str, Any]]:
    """Newest turns first, as summaries.

    ``missing_required`` is computed here rather than stored, so an entry written before a
    register row existed is judged by today's register — the point of the column is "is
    this turn quotable", and that is a question about the current declaration.
    """
    from ..register.record import missing_required

    out: list[dict[str, Any]] = []
    for entry in _entries():
        record = entry.get("record") or {}
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
