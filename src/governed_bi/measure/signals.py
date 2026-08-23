"""What an eval row can be ranked on, and which way round.

Separated from :mod:`.selective` because the two answer different questions. This
module answers "what did the artifact actually record", which is a fact about
``eval/projection.py::project_turn`` (re-exported from ``eval/harness.py``);
:mod:`.selective` answers "what does ranking on it
buy", which is a fact about the arm.

Every entry declares its **direction** and the **mechanism** behind that direction
before any curve is drawn. That is the whole discipline here: a sign chosen after
seeing which way the arm went is not a prediction, and
``git-history:docs/analysis/risk-coverage-v4.md`` §4 was a page of AUCs sitting either side of 0.5
by a couple of points, where picking the winning side per signal would manufacture
separation out of noise.

:func:`assert_no_signal_reads_the_grade` runs at import and is the only thing standing
between this registry and a leaked feature.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable, Mapping

from ..register.stages import Outcome
from .population import TurnRow

__all__ = [
    "Direction",
    "Signal",
    "SIGNALS",
    "READABLE_FIELDS",
    "assert_no_signal_reads_the_grade",
]

#: **The fields a ranking signal may read off a row. Everything else is refused.**
#:
#: An allowlist, because the denylist it replaces had a hole with a name. The old check
#: moved seven grade-bearing fields on a probe row and required each signal to return
#: the same value -- which catches a signal reading a field the probe *remembers*, and
#: catches nothing reading one it forgot. ``computed_fingerprint`` is on every real row,
#: is what ``computed_correct`` is derived from, and was not in the probe: a signal
#: reading it would have leaked the counterfactual grade and passed the guard.
#:
#: Adding a name here is the deliberate act the guard exists to force. It is checked
#: against :data:`_NEVER_READABLE` below, so the addition cannot be a grade field.
READABLE_FIELDS: frozenset[str] = frozenset(
    {"usage", "attempts", "licensed", "generated_sql", "reflect_verdict"}
)

#: Substrings no allowlisted field may contain. The second lock: it guards the
#: *allowlist*, not the signals, so widening ``READABLE_FIELDS`` to a gold-derived field
#: fails the import rather than the review.
_NEVER_READABLE: tuple[str, ...] = ("correct", "grade", "gold", "fingerprint", "quality_flags")


class Direction(str, Enum):
    """Which end of a signal is claimed to be the confident end."""

    lower_first = "lower value delivered first"
    higher_first = "higher value delivered first"


@dataclass(frozen=True)
class Signal:
    """A ranking signal, its declared direction, and the claim behind the direction.

    ``why`` is not decoration. It is the pre-registration: a direction with no stated
    mechanism is a sign fitted on the arm being scored and then written down.
    """

    name: str
    direction: Direction
    why: str
    read: Callable[[TurnRow], float | None]


def _agent_usage(row: TurnRow, key: str) -> float | None:
    """One counter off the ``agent_core`` usage entry.

    Per ``docs/measurement.md``, ``agent_core`` aggregates a whole tool loop into one
    entry, so this is the agent's total for the turn and not its first call.
    """
    for entry in row.get("usage") or ():
        if isinstance(entry, Mapping) and entry.get("stage") == "agent_core":
            value = entry.get(key)
            return None if value is None else float(value)
    return None


def _total_usage(row: TurnRow, key: str) -> float | None:
    """The same counter summed over every stage, or ``None`` if any stage lacks it."""
    entries = [e for e in (row.get("usage") or ()) if isinstance(e, Mapping)]
    if not entries:
        return None
    values = [e.get(key) for e in entries]
    if any(v is None for v in values):
        return None
    return float(sum(float(v) for v in values if v is not None))


def _sql_length(row: TurnRow) -> float | None:
    sql = row.get("generated_sql")
    return None if sql is None else float(len(str(sql)))


def _size(row: TurnRow, field: str) -> float | None:
    """Length of a recorded list, or ``None`` when the field was never written.

    **The empty list and the absent key are different facts**, and this repository has
    lost that distinction before (L-R1, ``Population.count``'s import-time guard). Both
    states are on disk right now: ``attempts`` is *absent* on all 1,351 rows of run1 and
    run2, and *empty* on the two v4 turns that delivered an answer with no statement at
    all. Reading the first as "zero attempts" gives those two arms a constant signal with
    a tidy AUC of exactly 0.5000, which reads as "the ledger carries no information"
    when the truth is that the ledger was never recorded.
    """
    value = row.get(field)
    return None if value is None else float(len(value))  # type: ignore[arg-type]


def _failed_attempts(row: TurnRow) -> float | None:
    ledger = row.get("attempts")
    if ledger is None:
        return None
    return float(sum(1 for a in ledger if isinstance(a, Mapping) and not a.get("passed")))


#: Ordinal encoding of the reflector's verdict, worst first, so ``higher_first`` means
#: "deliver the ones it endorsed". Three values, so this signal offers three operating
#: points and nothing between them.
_VERDICT_RANK: Mapping[str, float] = {"wrong": 0.0, "unsure": 1.0, "answered": 2.0}


def _reflect_verdict(row: TurnRow) -> float | None:
    verdict = row.get("reflect_verdict")
    if not isinstance(verdict, Mapping):
        return None
    return _VERDICT_RANK.get(str(verdict.get("verdict")))


def _declare(
    name: str, direction: Direction, why: str, read: Callable[[TurnRow], float | None]
) -> Signal:
    return Signal(name=name, direction=direction, why=why, read=read)


#: Every ranking signal an eval row supports, with its direction declared up front.
#:
#: Absent on purpose: anything derived from the gold statement or the grade
#: (``correct``, ``computed_correct``, ``grade_detail``, ``quality_flags``, the
#: fingerprint comparison), and anything needing a file this repository does not ship
#: -- question text lives in ``../BIRD-Data-Obfuscation``, so ``q_chars`` is not an
#: artifact signal however cheap it looks.
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
            "the same claim with the four facet rewriters added, which are near-constant "
            "per turn and so should carry slightly less",
            lambda row: _total_usage(row, "output_tokens"),
        ),
        _declare(
            "agent_in_tok", Direction.lower_first,
            "prompt size stands in for licensed-context size, and a turn that needed a "
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
            "thesis stated as a prediction, and it is the one the arm falsifies",
            _failed_attempts,
        ),
        _declare(
            "n_licensed", Direction.lower_first,
            "a turn licensed many tables is a turn retrieval could not narrow",
            lambda row: _size(row, "licensed"),
        ),
        _declare(
            "sql_len", Direction.lower_first,
            "statement length as a proxy for the question's structural difficulty",
            _sql_length,
        ),
        _declare(
            "reflect_verdict", Direction.higher_first,
            "the only signal here that reads the statement against the question. Present "
            "on the reflect arm only; every other arm ran with the observer off",
            _reflect_verdict,
        ),
    )
}


class _RecordingRow(dict):
    """A row that remembers which keys were asked for.

    ``dict`` rather than a ``Mapping`` wrapper so a signal doing anything a dict supports
    -- ``in``, ``[]``, iteration -- still works; the two access paths a signal actually
    uses are overridden. Iteration is *not* recorded as a read of every key, and does not
    need to be: :func:`assert_no_signal_reads_the_grade` checks the keys against the
    allowlist, and a signal that iterated the whole row to find the grade would have to
    name it to use it.
    """

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
    """Every signal must read only :data:`READABLE_FIELDS`, and must ignore the grade.

    A declaration that a signal is not gold-derived is worth nothing -- ``open-work.md``
    §3.9 is eight tests that assert a constant equals itself.

    **Two checks, and the first is the general one.** Every signal is run against a row
    that records which keys it asked for, and a key outside the allowlist fails the
    import. That closes the hole the second check had: moving the grade only catches a
    reader of a field the probe row happens to carry, and ``computed_fingerprint`` -- on
    every real row, and what ``computed_correct`` is derived from -- was not on it, so a
    signal reading it changed nothing between the two probes and passed.

    The second check stays because it is the one that catches a *value* leak rather than
    a *name* leak: an allowlisted field whose content is grade-derived would slip the
    first. Between them, a reader that reached for the answer fails the import rather
    than the review.
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
        "licensed": ["a", "b"],
        "generated_sql": "SELECT 1",
        "reflect_verdict": {"verdict": "unsure"},
        "correct": True,
        "computed_correct": True,
        "grade_detail": "match",
        "gold_fingerprint": "aaa",
        "pred_fingerprint": "aaa",
        "gold_sql": "SELECT 1",
        "quality_flags": [],
        # On every real row and absent from this probe until 2026-08-12, which is what made
        # the moved-grade check alone a denylist with a hole: `computed_correct` is derived
        # from this, so a signal reading it reads the counterfactual grade one step early.
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
