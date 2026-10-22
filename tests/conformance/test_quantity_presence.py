"""``Measured.unmeasured`` is absence. A check for absence has to know every spelling of it, and a measured zero is a
value."""

from __future__ import annotations

from governed_bi.registry.quantity import Measured
from governed_bi.registry.record import (
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
    """A measured zero is a value. Treating it as absence makes the check too strict to keep."""
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
