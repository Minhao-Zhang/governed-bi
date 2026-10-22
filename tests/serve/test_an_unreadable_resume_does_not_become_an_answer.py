"""``parse_resume`` fails closed on a payload it cannot read.

An empty map, ``None``, a number, or a list is ``malformed`` and must not be recorded as
``answered``. ``malformed`` stays distinct from ``declined``: a transport fault is not an
analyst's decision.
"""

from __future__ import annotations

import pytest

from governed_bi.serve.clarification import (
    RESOLUTION_ANSWERED,
    RESOLUTION_DECLINED,
    RESOLUTION_DEFERRED,
    RESOLUTION_MALFORMED,
    parse_resume,
)


@pytest.mark.parametrize(
    ("payload", "what"),
    [
        pytest.param({}, "an empty map", id="empty_map"),
        pytest.param({"unrelated": "x"}, "a map with no recognised key", id="unknown_key"),
        pytest.param(None, "a null resume", id="none"),
        pytest.param(123, "a number", id="number"),
        pytest.param([1, 2, 3], "a list", id="list"),
        pytest.param("", "an empty string", id="empty_string"),
        pytest.param("   ", "whitespace", id="blank_string"),
        pytest.param({"answer": ""}, "an empty answer", id="empty_answer"),
    ],
)
def test_an_unreadable_payload_fails_closed(payload: object, what: str) -> None:
    """An unreadable payload is ``malformed`` and ends the turn."""
    text, resolution, fail_closed = parse_resume(payload, why="w")

    assert fail_closed is True, f"{what} did not end the turn"
    assert resolution == RESOLUTION_MALFORMED, f"{what} was recorded as {resolution!r}"
    assert "do not call run_query" in text.lower(), text


def test_malformed_is_not_declined() -> None:
    """A transport fault and an analyst's decline stay different resolutions."""
    declined = parse_resume({"declined": True}, why="w")
    malformed = parse_resume({}, why="w")

    assert declined[2] is malformed[2] is True
    assert declined[1] == RESOLUTION_DECLINED
    assert malformed[1] == RESOLUTION_MALFORMED
    assert "declined" in declined[0].lower()
    assert "declined" not in malformed[0].lower(), (
        "the model is told the analyst declined when nothing arrived at all"
    )


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        pytest.param({"answer": "three"}, RESOLUTION_ANSWERED, id="answer"),
        pytest.param({"choice_id": "c2"}, RESOLUTION_ANSWERED, id="choice_id"),
        pytest.param("three", RESOLUTION_ANSWERED, id="bare_string"),
        pytest.param({"declined": True}, RESOLUTION_DECLINED, id="declined"),
        pytest.param({"cancelled": True}, RESOLUTION_DECLINED, id="cancelled"),
        pytest.param({"deferred": True}, RESOLUTION_DEFERRED, id="deferred"),
    ],
)
def test_every_documented_shape_still_works(payload: object, expected: str) -> None:
    """Closing the fall-through still accepts a bare string, which the protocol promises."""
    assert parse_resume(payload, why="w")[1] == expected


def test_a_resume_naming_another_clarification_is_refused() -> None:
    """An id is checked only when the payload names one; a bare answer is still the protocol."""
    stale = parse_resume(
        {"clarification_id": "clar-old", "answer": "three"}, why="w", expected_id="clar-now"
    )
    assert stale[1] == RESOLUTION_MALFORMED and stale[2] is True

    matching = parse_resume(
        {"clarification_id": "clar-now", "answer": "three"}, why="w", expected_id="clar-now"
    )
    assert matching == ("three", RESOLUTION_ANSWERED, False)

    unnamed = parse_resume({"answer": "three"}, why="w", expected_id="clar-now")
    assert unnamed == ("three", RESOLUTION_ANSWERED, False), (
        "a payload that names no clarification is the documented shape, not a mismatch"
    )
