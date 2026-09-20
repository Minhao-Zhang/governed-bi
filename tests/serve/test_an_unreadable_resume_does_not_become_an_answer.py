"""``parse_resume`` fails closed on a payload it cannot read, and did not until 2026-09-18.

The ``declined`` branch was already fail-closed and the fall-through was not, so the function
held two opposite opinions about what an uninterpretable payload means. What the open half
produced:

* ``{}``            → the model was told the analyst's answer was the empty string
* ``None``          → told the answer was the literal ``"None"``
* ``123`` / a list  → ``"123"`` / ``"[1, 2, 3]"``

each stamped ``resolution: "answered"`` in the record, beside the real ones, and each letting
the turn proceed to ``run_query`` on the strength of a reply nobody gave. An operator reading
``/audit/turns/{id}/trace`` afterwards would find a clarification marked answered and an
answer that came from the transport.

``malformed`` rather than ``declined`` for the closure: a reader's decision and a transport
fault are different facts, and a record that spells them the same cannot support either
"how often do analysts decline" or "how often does the resume path break".
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
    """Each of these used to return ``answered`` and let the turn continue."""
    text, resolution, fail_closed = parse_resume(payload, why="w")

    assert fail_closed is True, f"{what} did not end the turn"
    assert resolution == RESOLUTION_MALFORMED, f"{what} was recorded as {resolution!r}"
    assert "do not call run_query" in text.lower(), text


def test_malformed_is_not_declined() -> None:
    """The two closures are both closed and must stay distinguishable in the record.

    Collapsing them would be the cheaper fix and would put a decision the analyst never made
    into the one field that reports how often they make it.
    """
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
    """The protocol's own vocabulary, unchanged. Closing the fall-through must not narrow it.

    The bare string matters most: the client does not send one, but the docstring declares it
    and a stricter reading that required a mapping would refuse a shape this function
    promises to accept.
    """
    assert parse_resume(payload, why="w")[1] == expected


def test_a_resume_naming_another_clarification_is_refused() -> None:
    """A stale tab or a replayed request answering whichever question is outstanding now.

    Checked only when the payload names an id: the live client sends
    ``{clarification_id, answer}``, and the documented bare-string and flag shapes do not, so
    requiring one would refuse the protocol above.
    """
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
