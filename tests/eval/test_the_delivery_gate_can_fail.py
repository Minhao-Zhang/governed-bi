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
def test_the_cross_arm_judgement_refuses_a_seed_only_pair() -> None:
    """The control, repointed at the gate that now makes the judgement.

    **This was an ``xfail(strict=True)`` on ``context_hashes_distinct`` until 2026-08-11.** The
    docstring above anticipated exactly what happened: the fix demoted ``context_hash`` to an
    existence check and moved the treatment judgement onto declared fields, so the old assertion
    would have stayed XFAIL and stopped meaning anything. Per the D9 row's instruction, it is
    repointed rather than deleted.

    ``knobs_comparable`` reads the treatment from ``knobs_resolved`` instead of inferring it
    from a hash. ``run1`` and ``run2`` differ only by a random seed, so no caller can name a
    treatment for them and none of the 45 comparability knobs separates them — whichever of
    those two facts these artifacts show, the verdict must not be ``passed``.
    """
    from governed_bi.eval.report import Verdict, knobs_comparable

    gate = knobs_comparable(*_pair())
    assert gate.verdict is not Verdict.passed, (
        f"the cross-arm judgement passed on two arms that differ only by a random seed: "
        f"{gate.render()}"
    )


@_ARTIFACTS
def test_context_hash_no_longer_claims_to_detect_a_treatment() -> None:
    """The other half of D9, asserted so the demotion cannot be quietly reverted.

    The gate is now an existence check and *does* pass on the null pair — that is correct and
    is the point. What must not come back is the reading of that pass as "the treatment
    changed", so the judgement is required to live elsewhere: this asserts the two gates
    disagree on this pair, which is only possible because they now ask different questions.
    """
    from governed_bi.eval.report import Verdict, context_hashes_distinct, knobs_comparable

    a, b = _pair()
    assert context_hashes_distinct(a, b).verdict is Verdict.passed
    assert knobs_comparable(a, b).verdict is not Verdict.passed


@_ARTIFACTS
def test_a_seed_only_pair_is_not_quotable_today_and_must_stay_that_way() -> None:
    """The regression guard, which passes now — and for a reason that is not D9.

    ``comparison_quotable`` refuses this pair because these artifacts predate
    ``corpus_content_hash``, not because anything noticed the treatment was identical. Asserted anyway:
    whatever D9's fix does, it must not make a seed-only pair quotable, and this fails if some future
    change relaxes the corpus gate while D9 is still open.
    """
    from governed_bi.eval.report import comparison_quotable

    ok, _results_a, _results_b, _ctx, _knobs = comparison_quotable(*_pair())
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


# ── the treatment judgement itself, on input that reaches it ──────────────────
#
# **The four controls above are green against a tree with the entire treatment half of
# `knobs_comparable` deleted.** Independent review proved it by mutation: on the real null pair every
# comparability knob check short-circuits at the missing-key branch — those artifacts do not record
# `cost_budget`, `negative_tau`, `semantic_scale_ceiling` or `sqlglot_version` — so `cannot_evaluate`
# is returned for *absence* and the replicate check is never reached. That is the
# `corpus_content_hash` masking defect of audit D7 repeated one gate over: a verdict that looks like
# the right answer, arrived at without asking the question.
#
# Synthetic rows, therefore, and no apology for it: the property is about the *judgement*, and the
# artifacts on disk cannot express it. The artifact-backed controls above stay, because "no pair on
# disk can reach this gate" is itself worth pinning.

_ALL_KNOBS_AGREE = None  # populated per test from the register, so a new knob cannot be forgotten


def _synthetic(label: str, knobs: dict):
    from governed_bi.measure.population import Population

    return Population.of(
        label,
        [
            {"question_id": f"q{i}", "outcome": "answered", "correct": True, "knobs_resolved": knobs}
            for i in range(3)
        ],
    )


def _every_comparability_knob() -> dict:
    """A complete, well-formed `knobs_resolved`, so nothing short-circuits on absence."""
    from governed_bi.eval.report import comparability_keys
    from governed_bi.register.knobs import Unset, knob_default

    out = {}
    for key in comparability_keys():
        value = knob_default(key)
        out[key] = None if isinstance(value, Unset) else value
    return out


def test_two_arms_with_every_knob_identical_are_a_replicate_not_a_comparison() -> None:
    """The judgement D9 exists to make, reached rather than short-circuited.

    Deleting the replicate check leaves every artifact-backed control above green; it does not leave
    this one green. That is the whole difference between a gate and a gate with a control.
    """
    from governed_bi.eval.report import Verdict, knobs_comparable

    knobs = _every_comparability_knob()
    gate = knobs_comparable(_synthetic("a", knobs), _synthetic("b", dict(knobs)))
    assert gate.verdict is not Verdict.passed, (
        f"two arms with every comparability knob identical were certified as a comparison: "
        f"{gate.render()}"
    )
    assert "absent" not in gate.render(), (
        "the verdict came from a missing knob rather than from the judgement, which is the defect "
        f"this test exists to exclude: {gate.render()}"
    )

    # **Both halves, because the first assertion alone was green with the replicate check deleted.**
    # With no treatment declared the gate exits at the "nothing was named" branch and never reaches
    # the replicate check, so `d9-replicate-check-deleted` survived until this was added — caught by
    # `tools/mutate.py` within a minute of the control being written, which is the argument for
    # declaring the mutation at the same time as the test.
    key = "route_top_n" if "route_top_n" in knobs else sorted(knobs)[0]
    declared = knobs_comparable(
        _synthetic("a", knobs), _synthetic("b", dict(knobs)), treatment=frozenset({key})
    )
    assert declared.verdict is Verdict.failed, (
        f"a declared treatment identical on both arms is a replicate, not a comparison: "
        f"{declared.render()}"
    )
    assert "replicate" in declared.render()


def test_one_moved_knob_outside_the_declared_treatment_is_a_confounder() -> None:
    """The paired positive: the gate must still be able to *pass*, and to refuse a confounder.

    Without this, a `knobs_comparable` that refuses everything satisfies the test above.
    """
    from governed_bi.eval.report import Verdict, knobs_comparable

    knobs = _every_comparability_knob()
    moved = dict(knobs)
    key = "route_top_n" if "route_top_n" in knobs else sorted(knobs)[0]
    moved[key] = (knobs[key] or 0) + 7 if isinstance(knobs[key], (int, float)) else "moved"

    undeclared = knobs_comparable(_synthetic("a", knobs), _synthetic("b", moved))
    assert undeclared.verdict is Verdict.failed, (
        f"a knob moved outside any declared treatment is a confounder, not a comparison: "
        f"{undeclared.render()}"
    )
    declared = knobs_comparable(
        _synthetic("a", knobs), _synthetic("b", moved), treatment=frozenset({key})
    )
    assert declared.verdict is Verdict.passed, (
        f"the gate cannot pass at all, so refusing a replicate proves nothing: {declared.render()}"
    )
