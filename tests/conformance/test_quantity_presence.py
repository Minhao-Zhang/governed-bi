"""The presence test must recognise an explicit non-measurement.

Separate file rather than folded into ``test_register_closure.py`` because it tests a
hole that **the fix for the previous hole opened**, and that lineage is the point.

Three appearances of one shape in this project:

1. v1's ``corpus_content_hash == "unknown"`` compared equal to itself, so two runs
   with no recorded treatment passed the comparability gate.
2. ``missing_required`` checked key-presence only, and ``project`` writes every key —
   so a record of nulls passed. Fixed by treating ``None`` as absent.
3. Introducing ``Measured`` reopened it: ``Measured.unmeasured(why)`` is not ``None``,
   so a required field carrying an explicit non-measurement passed again.

Each fix was correct and each left the next instance reachable, because the defect is
not any one sentinel — it is that **a check for absence has to know every way absence
can be spelled**. That is the argument for the value being a declared type in the same
layer as the register, instead of a convention.
"""

from __future__ import annotations

from governed_bi.register.quantity import Measured
from governed_bi.register.record import (
    RECORD_REGISTER,
    Absence,
    missing_required,
    required_keys,
)


def _fully_stubbed() -> dict[str, object]:
    """A record where every required field carries a real value."""
    return {
        f.name: ([] if f.name == "usage" else "stub")
        for f in RECORD_REGISTER
        if f.absence is Absence.never
    }


def test_a_fully_stubbed_record_passes() -> None:
    """The control. Without it, "rejects everything" would satisfy the tests below."""
    assert not missing_required(_fully_stubbed())


def test_an_unmeasured_required_field_counts_as_missing() -> None:
    """The third instance of the shape, and the one this file exists for."""
    record = _fully_stubbed()
    victim = sorted(required_keys())[0]
    record[victim] = Measured.unmeasured("provider reported no value")
    assert missing_required(record) == frozenset({victim})


def test_a_measured_zero_is_a_value_and_must_not_count_as_missing() -> None:
    """The complement, and it is L-R1 read in the other direction.

    A gate that rejected ``Measured.of(0)`` would make a genuine zero unreportable —
    which is how "absent is not zero" gets over-applied into "zero is not real", and
    then the fix gets reverted for being too strict.
    """
    record = _fully_stubbed()
    victim = sorted(required_keys())[0]
    record[victim] = Measured.of(0)
    assert not missing_required(record)


def test_an_inapplicable_required_field_counts_as_missing() -> None:
    """``not_applicable`` is a legal *declaration*, not a legal value for a
    ``never`` field. A producer that hands one to a field declared always-written has
    disagreed with the register, and the register wins."""
    record = _fully_stubbed()
    victim = sorted(required_keys())[0]
    record[victim] = Measured.inapplicable("stage did not run")
    assert missing_required(record) == frozenset({victim})
