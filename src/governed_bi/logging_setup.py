"""Process-wide logging setup with ``run_id`` / ``turn_id`` on every record.

Nothing in ``src/`` used to call ``logging.basicConfig``, so every ``logger.*``
was dead under the default configuration and diagnostics had to be ``print``.
:func:`configure_logging` is the one entry that turns logging on; a ContextVar
filter injects correlation ids without changing any function signatures.
"""

from __future__ import annotations

import logging
import sys
from contextvars import ContextVar, Token
from pathlib import Path

_run_id: ContextVar[str | None] = ContextVar("governed_bi_run_id", default=None)
_turn_id: ContextVar[str | None] = ContextVar("governed_bi_turn_id", default=None)

_CONFIGURED = False

#: Format used after :func:`configure_logging`. Includes correlation ids so a
#: log line can be joined to ``stage_events.jsonl`` and a Langfuse session.
_LOG_FORMAT = (
    "%(asctime)s %(levelname)s [run=%(run_id)s turn=%(turn_id)s] "
    "%(name)s: %(message)s"
)
_DATE_FORMAT = "%Y-%m-%dT%H:%M:%S"


class _ContextFilter(logging.Filter):
    """Stamp ``run_id`` / ``turn_id`` onto every LogRecord from ContextVars."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.run_id = _run_id.get() or "-"  # type: ignore[attr-defined]
        record.turn_id = _turn_id.get() or "-"  # type: ignore[attr-defined]
        return True


def peek_run_id() -> str | None:
    """Current bound ``run_id``, or None if unbound."""
    return _run_id.get()


def peek_turn_id() -> str | None:
    """Current bound ``turn_id``, or None if unbound."""
    return _turn_id.get()


def bind_log_context(
    *,
    run_id: str | None = None,
    turn_id: str | None = None,
) -> list[Token]:
    """Bind correlation ids for the current context; return tokens for reset."""
    tokens: list[Token] = []
    if run_id is not None:
        tokens.append(_run_id.set(run_id))
    if turn_id is not None:
        tokens.append(_turn_id.set(turn_id))
    return tokens


def reset_log_context(tokens: list[Token]) -> None:
    """Undo :func:`bind_log_context` in reverse order."""
    for token in reversed(tokens):
        token.var.reset(token)


def configure_logging(
    *,
    level: int = logging.INFO,
    log_path: Path | str | None = None,
) -> None:
    """Install a root handler with timestamps and ContextVar correlation ids.

    The stream handler is installed once. A ``log_path`` may be supplied on a
    later call (e.g. once the run directory exists) and adds a file handler for
    that path without resetting the stream setup.
    """
    global _CONFIGURED
    root = logging.getLogger()
    root.setLevel(level)
    fmt = logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT)
    filt = _ContextFilter()

    if not _CONFIGURED:
        stream = logging.StreamHandler(sys.stderr)
        stream.setFormatter(fmt)
        stream.addFilter(filt)
        root.addHandler(stream)
        logging.getLogger("governed_bi").addFilter(filt)
        _CONFIGURED = True

    if log_path is not None:
        path = Path(log_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        already = any(
            isinstance(h, logging.FileHandler)
            and Path(getattr(h, "baseFilename", "")) == path.resolve()
            for h in root.handlers
        )
        if not already:
            file_handler = logging.FileHandler(path, encoding="utf-8")
            file_handler.setFormatter(fmt)
            file_handler.addFilter(filt)
            root.addHandler(file_handler)


def _reset_for_tests() -> None:
    """Test helper: allow :func:`configure_logging` to run again."""
    global _CONFIGURED
    _CONFIGURED = False
