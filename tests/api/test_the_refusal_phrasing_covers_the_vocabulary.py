"""The UI's reader-facing refusal sentences are a hand-copy of the engine's vocabulary.

``ui/lib/answer-delivery.ts``'s ``REFUSED_BY_SENTENCE`` turns each ``refused_by`` value into one
plain sentence, for the display mode built for a reader who cannot act on
``refused_by: no_schema_matched``. It cannot import
:data:`~governed_bi.register.stages.REFUSED_BY_TO_STAGE` — the client shares this repository and
nothing else (ADR 0007) — so the keys are copied by hand.

**This is the arrangement ``tests/api/test_provenance_groups_match_the_register.py`` already
guards, and it exists because that one caught a real, silent degradation**: the drawer's key lists
had been written against v1's deleted ``analyst/run_log.py``, so 32 copied keys named fields the v2
record never emits and 35 register fields appeared on no list. Nothing failed, because nothing was
checking. A refusal vocabulary is the same shape of hazard with a worse consequence: the reader is
already being told the engine could not answer them, and the sentence explaining why is the only
part they can use.

**Both directions, because only one of them is the interesting failure.** A sentence for a value the
engine no longer emits is dead text. A value the engine *does* emit with no sentence falls to the
fallback, which prints the raw identifier — and that is a real reader seeing
``This turn produced no answer (over_connect_bounds).`` It does not crash and it does not look like
a bug, which is exactly why it needs a test rather than a review.
"""

from __future__ import annotations

import re
from pathlib import Path

from governed_bi.register.stages import CRASH_REFUSED_BY, REFUSED_BY_TO_STAGE

DELIVERY_TS = Path(__file__).resolve().parents[2] / "ui" / "lib" / "answer-delivery.ts"


def _phrased_keys() -> set[str]:
    """The keys of ``REFUSED_BY_SENTENCE``, read out of the TypeScript.

    Parsed rather than executed, for the reason the provenance test gives: running the client's
    module would need a JS runtime in the Python suite. The block is bounded first so a key-shaped
    string elsewhere in the file cannot be counted.
    """
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
    """The direction that degrades silently.

    A missing key does not raise: ``refusalSentence`` falls back to
    ``This turn produced no answer (<raw>).`` So the failure is a reader being handed an engine
    identifier, and this assertion is the only thing between the vocabulary growing and that
    happening.
    """
    missing = sorted(set(REFUSED_BY_TO_STAGE) - _phrased_keys())
    assert not missing, (
        f"`refused_by` values the engine emits with no reader-facing sentence: {missing}. Add one "
        f"to `REFUSED_BY_SENTENCE` in {DELIVERY_TS.name}. Until then a reader in business mode is "
        "shown the raw identifier, which is the thing that mode exists to avoid."
    )


def test_no_sentence_describes_a_refusal_the_engine_cannot_emit() -> None:
    """The other direction: dead text, and a claim about a state that cannot occur."""
    unknown = sorted(_phrased_keys() - set(REFUSED_BY_TO_STAGE))
    assert not unknown, (
        f"sentences for `refused_by` values no longer in REFUSED_BY_TO_STAGE: {unknown}. Either "
        "the engine dropped the reason and the sentence is dead, or the key is misspelled — in "
        "which case the real value is falling through to the raw-identifier fallback."
    )


def test_our_own_faults_are_not_phrased_as_the_readers_data_problem() -> None:
    """``CRASH_REFUSED_BY`` is us, not them, and the sentence has to say so.

    ``register/stages.py`` keeps this separation deliberately — *"Declining on purpose is the
    product working; ``Outcome`` requires it stay apart from our own bugs"* — and it survives all
    the way to the record. It would be undone here by a sentence that sent someone to look at their
    corpus for a crash in ours, so the two crash reasons must own the failure in words.
    """
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
