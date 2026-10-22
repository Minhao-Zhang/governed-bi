"""Direction is declared before any curve is drawn. A sign chosen after seeing the config is not a prediction.

``assert_no_signal_reads_the_grade`` runs at import.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable, Mapping

from ..registry.stages import Outcome
from .population import TurnRow

__all__ = [
    "Direction",
    "Signal",
    "SIGNALS",
    "READABLE_FIELDS",
    "assert_no_signal_reads_the_grade",
]

# Allowlist. A denylist only catches a field the probe row remembers.
# ``computed_fingerprint`` is on every real row and was missing from that probe.
# A new name is checked against ``_NEVER_READABLE``.
READABLE_FIELDS: frozenset[str] = frozenset(
    {"usage", "attempts", "retrieved_tables", "generated_sql"}
)

# Guards the allowlist: a gold-derived name fails import.
_NEVER_READABLE: tuple[str, ...] = ("correct", "grade", "gold", "fingerprint", "quality_flags")


class Direction(str, Enum):
    """Which end is claimed confident. Declared before the curve."""

    lower_first = "lower value delivered first"
    higher_first = "higher value delivered first"


@dataclass(frozen=True)
class Signal:
    """``why`` is the pre-registration. A direction with no mechanism is a sign fitted on the config being scored."""

    name: str
    direction: Direction
    why: str
    read: Callable[[TurnRow], float | None]


def _mappings(row: TurnRow, key: str) -> tuple[Mapping[str, object], ...]:
    """Absent and empty are not distinguished. A caller that needs both checks ``row.get(key) is None``."""
    value = row.get(key)
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(e for e in value if isinstance(e, Mapping))


def _agent_usage(row: TurnRow, key: str) -> float | None:
    """``agent_core`` aggregates the whole tool loop, so this is the turn total, not the first call."""
    for entry in _mappings(row, "usage"):
        if entry.get("stage") == "agent_core":
            value = entry.get(key)
            return None if value is None else float(value)  # type: ignore[arg-type]
    return None


def _total_usage(row: TurnRow, key: str) -> float | None:
    entries = _mappings(row, "usage")
    if not entries:
        return None
    total = 0.0
    for entry in entries:
        value = entry.get(key)
        if value is None:
            return None
        total += float(value)  # type: ignore[arg-type]
    return total


def _sql_length(row: TurnRow) -> float | None:
    sql = row.get("generated_sql")
    return None if sql is None else float(len(str(sql)))


def _n_retrieved_tables(row: TurnRow) -> float | None:
    """Stored artifacts may still use the old field name."""
    if "retrieved_tables" in row:
        return _size(row, "retrieved_tables")
    return _size(row, "licensed")


def _size(row: TurnRow, field: str) -> float | None:
    """An absent key and an empty list are different. Reading absence as zero attempts looks like a constant signal."""
    value = row.get(field)
    return None if value is None else float(len(value))  # type: ignore[arg-type]


def _failed_attempts(row: TurnRow) -> float | None:
    # ``is None`` first, on its own: no ledger is unmeasured, an empty ledger is zero failures,
    # and ``_mappings`` returns ``()`` for both.
    if row.get("attempts") is None:
        return None
    return float(sum(1 for a in _mappings(row, "attempts") if not a.get("passed")))


def _declare(
    name: str, direction: Direction, why: str, read: Callable[[TurnRow], float | None]
) -> Signal:
    return Signal(name=name, direction=direction, why=why, read=read)


# Gold-derived fields are absent on purpose, and so is anything that needs a file
# this repository does not ship, such as question text.
SIGNALS: Mapping[str, Signal] = {
    s.name: s
    for s in (
        _declare(
            "agent_out_tok", Direction.lower_first,
            "an agent that writes more has been round the retry loop more, and the volume "
            "of what it emitted is retry volume -- which the count of retries only "
            "approximates",
            lambda row: _agent_usage(row, "output_tokens"),
        ),
        _declare(
            "total_out_tok", Direction.lower_first,
            "the same claim with the four retrieval-channel rewriters added, which are near-constant "
            "per turn and so should carry slightly less",
            lambda row: _total_usage(row, "output_tokens"),
        ),
        _declare(
            "agent_in_tok", Direction.lower_first,
            "prompt size stands in for retrieved-table context size, and a turn that needed a "
            "large context is a turn retrieval could not narrow",
            lambda row: _agent_usage(row, "input_tokens"),
        ),
        _declare(
            "agent_model_calls", Direction.lower_first,
            "the count behind the volume: a turn that called the model repeatedly did not "
            "get it right the first time",
            lambda row: _agent_usage(row, "model_calls"),
        ),
        _declare(
            "n_attempts", Direction.lower_first,
            "ledger length -- how many statements the turn put through the layer stack",
            lambda row: _size(row, "attempts"),
        ),
        _declare(
            "n_failed_attempts", Direction.lower_first,
            "how many of those the governance stack refused. This is the layer stack's own "
            "thesis stated as a prediction, and it is the one the config falsifies",
            _failed_attempts,
        ),
        _declare(
            "n_retrieved_tables", Direction.lower_first,
            "a turn that retrieved many tables is a turn retrieval could not narrow",
            _n_retrieved_tables,
        ),
        _declare(
            "sql_len", Direction.lower_first,
            "statement length as a proxy for the question's structural difficulty",
            _sql_length,
        ),
    )
}


class _RecordingRow(dict):
    """``dict`` so ``in``, indexing, and iteration still work. Iteration is not a read of every key."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self.read_keys: set[str] = set()

    def get(self, key, default=None):  # type: ignore[override] # noqa: ANN001, ANN206
        self.read_keys.add(str(key))
        return super().get(key, default)

    def __getitem__(self, key):  # type: ignore[override] # noqa: ANN001, ANN204
        self.read_keys.add(str(key))
        return super().__getitem__(key)


def assert_no_signal_reads_the_grade(signals: Mapping[str, Signal]) -> None:
    """A signal may read only ``READABLE_FIELDS``. Moving the grade only catches a field the probe carries.

    The value check stays: an allowlisted field whose content is the grade would slip a name check.
    """
    forbidden = sorted(
        field
        for field in READABLE_FIELDS
        if any(banned in field for banned in _NEVER_READABLE)
    )
    if forbidden:
        raise AssertionError(
            f"READABLE_FIELDS names {forbidden}, which read as grade-derived. The allowlist is "
            "the thing being guarded here: widening it to the answer would make every signal "
            "below legal."
        )

    row: dict[str, object] = {
        "question_id": "probe",
        "outcome": Outcome.answered.value,
        "usage": [{"stage": "agent_core", "output_tokens": 7, "input_tokens": 9, "model_calls": 2}],
        "attempts": [{"passed": True}, {"passed": False}],
        "retrieved_tables": ["a", "b"],
        "generated_sql": "SELECT 1",
        "correct": True,
        "computed_correct": True,
        "grade_detail": "match",
        "gold_fingerprint": "aaa",
        "pred_fingerprint": "aaa",
        "gold_sql": "SELECT 1",
        "quality_flags": [],
        # On every real row. Omitting it makes the moved-grade check a denylist with a
        # hole: ``computed_correct`` is derived from this.
        "computed_fingerprint": "aaa",
    }
    for name, signal in sorted(signals.items()):
        probe = _RecordingRow(row)
        signal.read(probe)
        strayed = sorted(probe.read_keys - READABLE_FIELDS)
        if strayed:
            raise AssertionError(
                f"signal {name!r} read {strayed}, which is not in READABLE_FIELDS. A ranking "
                "signal may only read what the engine produced while answering; if one of "
                "these is genuinely not grade-derived, add it to the allowlist and say why. "
                "Gold-derived features belong in selective.oracle(), which is labelled for it."
            )

    moved = {
        **row,
        "correct": False,
        "computed_correct": False,
        "computed_fingerprint": "ddd",
        "grade_detail": "result_mismatch",
        "gold_fingerprint": "bbb",
        "pred_fingerprint": "ccc",
        "gold_sql": "SELECT 2",
        "quality_flags": ["degenerate"],
    }
    leaking = sorted(name for name, s in signals.items() if s.read(row) != s.read(moved))
    if leaking:
        raise AssertionError(
            f"{leaking} changed value when only the grade changed, so their curves would be "
            "fitted to the answer. Gold-derived features belong in selective.oracle(), which "
            "is labelled for it."
        )


assert_no_signal_reads_the_grade(SIGNALS)
