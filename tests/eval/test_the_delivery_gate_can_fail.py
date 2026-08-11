"""Audit D9's positive control: the cross-arm gate passes on a seed-only null pair.

``run1`` and ``run2`` in ``runs/eval/`` differ **only by a random seed** — the same treatment, kept on
disk for exactly this purpose. ``context_hashes_distinct`` requires at least 95% of shared questions to
have differing ``context_hash`` before an arm-to-arm delta may be quoted, on the reasoning that a
changed treatment changes the context. On that pair it passes at **0.9993**, and it passes on every
other pair on disk too (0.992, 0.992, 0.988). It believes it asks "did the treatment change" and
measures "is there retrieval noise", to which the answer is always yes.

**Three corrections to how this was first written, all from review, all narrowing the claim.**

1. **It does not currently let a delta be published.** ``comparison_quotable(run1, run2)`` returns
   **False** at HEAD — blocked by the ``corpus_content_hash`` gate that arrived when D7 closed, because
   these two artifacts predate that field. The first version of this file said a delta "is published
   with a ``[pass]`` beside it"; that is false today. The tautology is real, its consequence is
   currently masked by an unrelated gate, and a mask is not a fix — the next pair that carries a corpus
   hash is quotable on a seed.
2. **The only caller is ``python -m governed_bi.eval --pair``**, the SQLite driver that produced none of
   ``runs/eval/`` (still-open D10). So no artifact here has ever been through this gate. "Published"
   was present tense about something that has not happened.
3. **``context_hash`` differing is not merely ordering.** Measured across the pair: only 100 of 1351
   questions get the same schema shortlist and only 33 the same ``licensed`` set. Different tables are
   selected on 92.6% of questions. That is the strongest argument *for* the current gate — two runs
   whose realised context differs that much are arguably not one treatment — and it is why the fix is a
   judgement about what "treatment" names, not a bug fix.

**What this control does and does not force.** It fires on the gate keeping its name and its role. If
the fix instead demotes ``context_hash`` to an existence check and asserts treatment difference from
declared fields — which is what this audit's Phase 2 prescribes — then **this test stays XFAIL and
stops meaning anything**. Whoever does that must repoint it at whatever makes the judgement, and the D9
row says so. ``strict=True`` catches the narrower case; it is not clairvoyant, and the first version of
this docstring implied it was.
"""

from __future__ import annotations

import json
import pathlib

import pytest

RUNS = pathlib.Path(__file__).resolve().parents[2] / "runs" / "eval"
NULL_PAIR = (
    RUNS / "proxy_full_opus_high_corpus30872d3.jsonl",
    RUNS / "proxy_full_opus_high_corpus30872d3_run2.jsonl",
)

#: ``runs/`` is gitignored, so CI has no artifacts and neither does a fresh clone. **The first version
#: of this file asserted the fixture exists, which turned `main` red** — and its commit claimed "gates
#: green", which was true only on the machine that wrote it. Skipped rather than xfailed: a missing
#: artifact is not a defect in the code, and an xfail would make the two indistinguishable.
_ARTIFACTS = pytest.mark.skipif(
    not all(p.exists() for p in NULL_PAIR),
    reason=(
        "the designated null replicate is not on this machine; `runs/` is gitignored, so this control "
        "runs where the artifacts are and is silent where they are not"
    ),
)


def _population(label: str, path: pathlib.Path):
    from governed_bi.measure.population import Population

    rows = [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    return Population.of(
        label,
        [
            {
                # No `str()` around a possibly-absent id: `Population.of` refuses a row it cannot
                # identify, and `str(None)` would hand it the string "None" and defeat that.
                "question_id": r["question_id"],
                "context_hash": r.get("context_hash"),
                "corpus_content_hash": r.get("corpus_content_hash"),
                "knobs_resolved": r.get("knobs_resolved"),
                "correct": r.get("correct"),
                "outcome": r.get("outcome"),
            }
            for r in rows
        ],
    )


def _pair():
    # Distinct labels: both stems share their first 24 characters, so truncating them made the
    # control's failure message name one arm twice — in a test whose only product is that message.
    return _population("run1", NULL_PAIR[0]), _population("run2", NULL_PAIR[1])


@_ARTIFACTS
@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason=(
        "audit D9: the cross-arm gate measures retrieval nondeterminism and reports it as a treatment "
        "difference, so it passes on a seed-only null pair. strict=True turns fixing D9 into a failure "
        "until the marker goes. raises=AssertionError so an ImportError — which a rename of the "
        "function would produce — is a real failure rather than the expected one."
    ),
)
def test_the_cross_arm_gate_refuses_a_seed_only_pair() -> None:
    """The gate must not certify two arms that differ only by a random seed."""
    from governed_bi.eval.report import Verdict, context_hashes_distinct

    gate = context_hashes_distinct(*_pair())
    assert gate.verdict is not Verdict.passed, (
        f"the delivery gate passed on two arms that differ only by a random seed: {gate.render()}. "
        "It is measuring retrieval noise and reporting it as a treatment difference."
    )


@_ARTIFACTS
def test_a_seed_only_pair_is_not_quotable_today_and_must_stay_that_way() -> None:
    """The regression guard, which passes now — and for a reason that is not D9.

    ``comparison_quotable`` refuses this pair because these artifacts predate
    ``corpus_content_hash``, not because anything noticed the treatment was identical. Asserted anyway:
    whatever D9's fix does, it must not make a seed-only pair quotable, and this fails if some future
    change relaxes the corpus gate while D9 is still open.
    """
    from governed_bi.eval.report import comparison_quotable

    ok, _results_a, _results_b, _ctx = comparison_quotable(*_pair())
    assert ok is False, "a seed-only null pair became quotable"


@_ARTIFACTS
def test_the_null_pair_is_still_a_null() -> None:
    """The control needs its fixture to still be a null, on **every** row.

    The first version read one row per arm — mutation-verified as insufficient: a knob changed on row 0
    was caught, the same change on row 700 was not. It checks all 1,351 now.

    What cannot be checked here: prompt and corpus identity, because both artifacts predate those
    fields. That rests on ``runs/eval/README.md``'s table, which that file itself says to treat as an
    annotation rather than as evidence — so this is a partial check and says so.
    """
    a, b = _pair()
    shared = a.units & b.units
    assert len(shared) == 1351, f"the arms share {len(shared)} questions, not the full set"

    by_a, by_b = a.by_unit(), b.by_unit()
    differing = [
        qid for qid in sorted(shared) if by_a[qid].get("knobs_resolved") != by_b[qid].get("knobs_resolved")
    ]
    assert not differing, (
        f"{len(differing)} row(s) record different knobs, so this is no longer a null replicate and "
        f"the control above is measuring something else: {differing[:5]}"
    )
