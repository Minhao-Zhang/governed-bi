"""Audit D9's positive control: the delivery gate must refuse a seed-only null pair.

``run1`` and ``run2`` in ``runs/eval/`` differ **only by a random seed** — the same treatment, kept on
disk for exactly this purpose. ``comparison_quotable`` is supposed to establish that two arms really
are two treatments before a delta may be quoted, and its cross-arm half is
``context_hashes_distinct``: at least 95% of shared questions must have differing ``context_hash``.

They do — 1350 of 1351 — because **retrieval is nondeterministic**. Facet rewrites, vector tie-breaks
and shortlist ordering all move the context, so `context_hash` differs almost by construction. The
gate believes it is asking "did the treatment change" and is measuring "is there retrieval noise", to
which the answer is always yes. So it always passes, and a net delta over two identical treatments is
published with a `[pass]` beside it.

That is not a missing check. It is the sentence "this comparison may be quoted" turned into a
tautology, in a repository whose thesis is that a number nobody can explain is worthless.

**This file is the control, and it is marked ``xfail(strict=True)`` on purpose.** The assertion below
is what the gate *should* do; it fails today. When D9 lands, it will XPASS, and ``strict`` turns an
unexpected pass into a failure — so the fix cannot ship without deleting the marker, and the marker
cannot be deleted without the fix. A defect nobody can forget.

The fix, from this audit's Phase 2: assert treatment difference from ``knobs_resolved`` ∪
``prompt_set_hash`` ∪ ``corpus_content_hash`` — things a run *declares* — and keep ``context_hash`` as
an existence gate only. Note while doing it that these two artifacts carry ``corpus_content_hash:
None``, so on **this** pair the honest new verdict is ``cannot_evaluate`` rather than ``fail``; a
`fail` needs a pair that records its identity. Both are correct outcomes and neither is a pass.
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


def _population(label: str, path: pathlib.Path):
    from governed_bi.measure.population import Population

    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return Population.of(
        label,
        [
            {
                "question_id": str(r.get("question_id")),
                "context_hash": r.get("context_hash"),
                "corpus_content_hash": r.get("corpus_content_hash"),
                "prompt_set_hash": r.get("prompt_set_hash"),
                "knobs_resolved": r.get("knobs_resolved"),
                "correct": r.get("correct"),
                "outcome": r.get("outcome"),
            }
            for r in rows
        ],
    )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "audit D9: the cross-arm gate measures retrieval nondeterminism and reports it as a "
        "treatment difference, so it certifies a seed-only null pair. strict=True means fixing D9 "
        "turns this into a failure until the marker is removed."
    ),
)
def test_a_seed_only_pair_is_not_a_quotable_comparison() -> None:
    """The two arms differ only by seed, so no delta between them may be quoted."""
    from governed_bi.eval.report import Verdict, context_hashes_distinct

    a, b = (_population(p.stem[:24], p) for p in NULL_PAIR)
    gate = context_hashes_distinct(a, b)
    assert gate.verdict is not Verdict.passed, (
        f"the delivery gate passed on two arms that differ only by a random seed: "
        f"{gate.render()}. Any net delta between them is noise wearing a [pass]."
    )


def test_the_null_pair_is_still_on_disk_and_is_still_a_null() -> None:
    """The control needs its fixture, and the fixture needs to still be a null.

    Without this, deleting the artifacts would silently retire the control above: an xfail on a
    missing file is still an xfail. Asserted as an ordinary test so its absence is a failure.
    """
    for path in NULL_PAIR:
        assert path.exists(), f"the designated null replicate is gone: {path.name}"

    a, b = (_population(p.stem[:24], p) for p in NULL_PAIR)
    assert len(a.units & b.units) > 1000, "the two arms no longer share a question set"
    # Same treatment: every knob the two record agrees. This is what makes them a null.
    knobs = [
        next(iter(pop.by_unit().values())).get("knobs_resolved") for pop in (a, b)
    ]
    assert knobs[0] == knobs[1], (
        "the two arms no longer record the same knobs, so they are no longer a null replicate "
        "and the control above is measuring something else"
    )
