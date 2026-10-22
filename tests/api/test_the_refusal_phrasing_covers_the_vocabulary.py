"""The UI copies ``refused_by`` keys by hand: the client cannot import the registry (ADR 0007).

A missing sentence falls back to the raw identifier and does not crash. A sentence for a value
the engine does not emit is dead text.
"""

from __future__ import annotations

import re
from pathlib import Path

from governed_bi.registry.stages import CRASH_REFUSED_BY, REFUSED_BY_TO_STAGE

DELIVERY_TS = Path(__file__).resolve().parents[2] / "ui" / "lib" / "answer-delivery.ts"


def _phrased_keys() -> set[str]:
    """Parsed, not executed: the Python suite has no JS runtime. The block is bounded first."""
    source = DELIVERY_TS.read_text(encoding="utf-8")
    match = re.search(
        r"const REFUSED_BY_SENTENCE: Record<string, string> = \{(.*?)^\};",
        source,
        re.DOTALL | re.MULTILINE,
    )
    assert match, (
        "could not find `REFUSED_BY_SENTENCE` in ui/lib/answer-delivery.ts. If it was renamed or "
        "moved, this test has to move with it — deleting it would leave the hand-copy unchecked, "
        "which is the state that produced the 32-dead-keys defect in the provenance drawer."
    )
    return set(re.findall(r"^\s{2}([a-z_]+):", match.group(1), re.MULTILINE))


def test_every_refusal_the_engine_can_emit_has_a_sentence() -> None:
    """A missing key does not raise: the reader is shown the raw identifier."""
    missing = sorted(set(REFUSED_BY_TO_STAGE) - _phrased_keys())
    assert not missing, (
        f"`refused_by` values the engine emits with no reader-facing sentence: {missing}. Add one "
        f"to `REFUSED_BY_SENTENCE` in {DELIVERY_TS.name}. Until then a reader in business mode is "
        "shown the raw identifier, which is the thing that mode exists to avoid."
    )


def test_no_sentence_describes_a_refusal_the_engine_cannot_emit() -> None:
    unknown = sorted(_phrased_keys() - set(REFUSED_BY_TO_STAGE))
    assert not unknown, (
        f"sentences for `refused_by` values no longer in REFUSED_BY_TO_STAGE: {unknown}. Either "
        "the engine dropped the reason and the sentence is dead, or the key is misspelled — in "
        "which case the real value is falling through to the raw-identifier fallback."
    )


def test_our_own_faults_are_not_phrased_as_the_readers_data_problem() -> None:
    """A crash sentence that blames the corpus sends the reader to curate a fault they cannot see."""
    source = DELIVERY_TS.read_text(encoding="utf-8")
    for reason in sorted(CRASH_REFUSED_BY):
        match = re.search(rf"^\s{{2}}{reason}: \"([^\"]+)\"", source, re.MULTILINE)
        assert match, f"{reason} has no single-line sentence to check"
        sentence = match.group(1)
        assert "on our side" in sentence, (
            f"{reason} is in CRASH_REFUSED_BY — it is our bug, not the reader's data. Its sentence "
            f"is {sentence!r}, which does not say so. A crash described as a limitation of their "
            "corpus sends someone to curate away a fault they cannot see."
        )
