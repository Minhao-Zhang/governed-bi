"""Arm profiles: the committed declaration of what each measured arm changed.

**This module does not set knobs and must not learn to.** ``register/knobs.py`` is the one home
for a knob's value; a second place deciding that is the defect AGENTS.md names and this
repository has already paid for. What lives here is the *claim* — "arm v4's treatment was the
prompt" — which had no committed home at all. An arm's identity lived in a gitignored ``.env``
on one machine while ``runs/eval/`` named the arm, so nothing a reader could fetch said what the
name meant.

The claim is load-bearing because of audit D9. ``eval/report.py::knobs_comparable`` will not
certify a pair that cannot name its treatment, having learned that inferring the treatment from
``context_hash`` distinctness measures retrieval nondeterminism instead. Somebody has to say what
changed; this is where they say it, in a file that diffs.

Named ``arm_profiles`` and not ``arms`` because ``arms`` is already a ``Role.scope`` knob, and
two things in the register answering to one word is how the second reader gets in.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

from .knobs import comparability_keys

__all__ = [
    "ARMS_FILE",
    "ArmProfile",
    "arm_profile",
    "load_arm_profiles",
    "reconcile",
    "recorded_corpus_release",
    "recorded_question_subset",
]

#: Beside this module, because the register owns arm profiles. It lived at the repo root until
#: 2026-08-11, reached by climbing four parents out of the package — which resolves only from a
#: source checkout: from an installed wheel the same climb lands above ``site-packages`` and the
#: file is not there. As package data it travels with the distribution instead.
ARMS_FILE = Path(__file__).resolve().parent / "arms.toml"


@dataclass(frozen=True, slots=True)
class ArmProfile:
    """One arm's declared identity. ``treatment`` is the only field a gate reads."""

    name: str
    description: str
    treatment: frozenset[str]
    compare_to: str | None = None
    #: The corpus repository's **git ref**, for a human who wants to check it out. Nothing in a
    #: measurement row carries this, so nothing reconciles against it — see
    #: :attr:`corpus_content_hash`, which is a different namespace and was being compared
    #: against this one.
    corpus: str | None = None
    #: The corpus **content digest** the rows must carry. This is what :func:`reconcile` reads.
    corpus_content_hash: str | None = None
    #: The question dataset's **git ref** in ``../BIRD-Data-Obfuscation``, for a human who wants
    #: to check it out. Nothing in a measurement row carries it, so nothing reconciles against
    #: it — the same split as :attr:`corpus` against :attr:`corpus_content_hash`, kept because
    #: collapsing the two is what made the corpus check vacuous for a year.
    dataset: str | None = None
    #: The question set the rows must carry, in the ``question_subset`` scope knob's own format
    #: and under its own name. This is what :func:`reconcile` reads.
    question_subset: str | None = None
    #: The corpus **release tag** this arm was measured on, matching the ``corpus_release``
    #: comparability knob. Distinct from :attr:`corpus` (a ref a human checks out) and from
    #: :attr:`corpus_content_hash` (what a row carries): a release is what an arm *declares*, and
    #: without it an arm whose treatment is the corpus cannot name its own treatment.
    corpus_release: str | None = None
    #: The effect this arm exists to detect, in points of the readout below (0.03 = 3pp).
    #:
    #: **Declared before the run, which is the whole point.** ``eval/power.py::require_power``
    #: refuses an arm that cannot detect its own hypothesis, and until this field existed it had
    #: no caller -- so the gate that stops an underpowered arm before it spends anything was
    #: reachable only by a caller passing a number it had made up on the spot.
    hypothesised_effect: float | None = None
    #: Which quantity :attr:`hypothesised_effect` is denominated in. Required alongside it,
    #: because **MDE is denominated in points of the whole population** and two readouts' base
    #: rates can differ by two orders of magnitude. A mechanism indicator with a 2.15pp ceiling
    #: has 1.9 resolvable steps against EX's 28.5, and a draft of this design read the smaller
    #: MDE as the better instrument. Naming the readout is what makes that error visible.
    readout: str | None = None
    notes: str = ""


def _parse_profiles(data: Mapping[str, Any], *, source: str) -> dict[str, ArmProfile]:
    arms = data.get("arm")
    if not isinstance(arms, Mapping):
        raise ValueError(f"{source}: no [arm.*] tables")

    keys = comparability_keys()
    out: dict[str, ArmProfile] = {}
    for name, body in arms.items():
        if not isinstance(body, Mapping):
            raise ValueError(f"{source}: [arm.{name}] is not a table")
        raw = body.get("treatment", [])
        if not isinstance(raw, list) or not all(isinstance(x, str) for x in raw):
            raise ValueError(f"{source}: [arm.{name}].treatment must be a list of knob names")
        treatment = frozenset(raw)
        # Refused rather than ignored: a typo that reads as "no treatment declared" would turn
        # a real comparison into `cannot_evaluate` and look like a data problem, and one that
        # reads as a *different* knob would certify a confounded pair.
        unknown = sorted(treatment - keys)
        if unknown:
            raise ValueError(
                f"{source}: [arm.{name}].treatment names {unknown}, which are not "
                "Role.comparability knobs in register/knobs.py"
            )
        # **Mandatory, and this is the half that outlived the first fix.** `reconcile` was
        # repaired on 2026-08-11 and `v3_fold` — the baseline v4 is measured against — still
        # declared no digest, so the guard was never entered, the pre-flight check passed, and
        # the driver printed "every row agrees with the profile in arms.toml" about an arm it
        # had not compared. Refusing here is what stops the *next* arm reintroducing it: an
        # optional field that silences a check is a check with an off switch nobody labelled.
        if not body.get("corpus_content_hash"):
            raise ValueError(
                f"{source}: [arm.{name}] declares no corpus_content_hash. That is the only "
                "field `reconcile` can compare, so an arm without one is unreconcilable and "
                "would report agreement from a check that examined nothing. Read the digest "
                "off any row of the arm's artifact; `corpus` is the git ref and is not it."
            )
        # **Mandatory for the same reason, one field over.** This file pinned the corpus twice
        # and the question set not at all, so a rerun on a replaced dataset produced the same
        # n = 1 351, a different population, and passed every gate — the corpus digest and the
        # knobs both matched. Recovering which questions the shipped arms ran took a
        # schema-filtered count across four versions of another repository (see arms.toml's
        # header); the field is mandatory so the next arm cannot cost that again.
        if not body.get("question_subset"):
            raise ValueError(
                f"{source}: [arm.{name}] declares no question_subset. The corpus is pinned "
                "twice here and the question set would be pinned not at all, so an arm rerun "
                "on a different dataset would agree with this profile on everything it "
                "compares. Read it off any row's knobs_resolved['question_subset'], or hash "
                "the arm's question ids the way eval/provenance.py::scope_identity does; "
                "`dataset` is the git ref and is not it."
            )
        out[name] = ArmProfile(
            name=name,
            description=str(body.get("description", "")),
            treatment=treatment,
            compare_to=body.get("compare_to"),
            corpus=body.get("corpus"),
            corpus_content_hash=body.get("corpus_content_hash"),
            dataset=body.get("dataset"),
            question_subset=body.get("question_subset"),
            notes=str(body.get("notes", "")),
        )
    return out


@lru_cache(maxsize=1)
def load_arm_profiles(path: Path | None = None) -> Mapping[str, ArmProfile]:
    """Every declared arm, by name. Cached: the file is a constant of a run."""
    source = path or ARMS_FILE
    with source.open("rb") as fh:
        return _parse_profiles(tomllib.load(fh), source=str(source))


def arm_profile(name: str, *, path: Path | None = None) -> ArmProfile:
    """One arm's profile.

    Raises on an unknown name rather than returning a profile with an empty treatment, which
    would silently degrade a real comparison into "nobody said what changed".
    """
    profiles = load_arm_profiles(path)
    try:
        return profiles[name]
    except KeyError:
        raise KeyError(
            f"no [arm.{name}] in {path or ARMS_FILE}; declared arms are "
            f"{sorted(profiles)}"
        ) from None


def reconcile(profile: ArmProfile, row: Mapping[str, Any]) -> tuple[str, ...]:
    """Where a recorded run disagrees with what its arm profile claims. Empty means agreement.

    The point of writing the claim down is that it can be wrong. A profile naming one corpus
    beside a row recording another is a mislabelled artifact, and mislabelled artifacts are how
    a number ends up quoted against the wrong treatment.

    ``row`` is a **measurement row** (or anything shaped like one — the session's own identity
    reads the same way, which is what lets the driver run this before the first paid question).

    **Two things about this were wrong until 2026-08-11.** It took ``knobs_resolved`` and read
    ``corpus_content_hash`` out of it; that field is a ``RecordField``, not a knob, and is
    never in the knob mapping, so the lookup returned ``None`` and the function returned
    agreement unconditionally. And the value it compared against was ``corpus`` — the corpus
    repository's git ref, ``30872d3`` — while every row records the content digest,
    ``86ed1dbf…``. Two identifiers in two namespaces, compared with ``startswith``, which
    could never match. Both halves failed silently and in the safe-looking direction, which is
    why the profile field is now split in two and only the digest is reconciled.

    Only what the profile actually asserts is checked. ``treatment`` is not checkable from one
    arm — it is a statement about a *pair* — so it is ``knobs_comparable``'s business and not
    reconciled here.

    **A profile with no digest is a problem, not a pass.** That was the third way this function
    returned agreement without comparing anything, and it survived the other two: ``v3_fold``
    declared no ``corpus_content_hash``, the ``is not None`` guard was never entered, and a run
    launched ``--arm v3_fold`` against any corpus at all cleared the pre-flight check *and* was
    told every row agreed. ``load_arm_profiles`` now refuses such a file, and this is the second
    lock, because :class:`ArmProfile` can be constructed directly and a test fixture is exactly
    where an unreconcilable profile gets invented.

    **``question_subset`` is read out of ``knobs_resolved``, which is the opposite of the rule
    above, and the difference is the whole point.** The 2026-08-11 defect was not "the knob
    mapping is the wrong place to look" — it was looking somewhere the field never is.
    ``corpus_content_hash`` is a ``RecordField`` and lives at the top of the row;
    ``question_subset`` is a ``Role.scope`` knob resolved by
    ``eval/provenance.py::scope_identity`` and lives in the row's knob mapping, verified on
    ``runs/eval/live_full_gpt-5.6-luna_xhigh_topdefault_lexical.jsonl``, where it reads
    ``1351:423a3f4b65fb``. So each field is read from its own home. A reader who "fixes" this
    to match the corpus branch reintroduces the vacuous lookup in the other direction, and the
    test that catches that is
    ``test_reconcile_reads_the_question_set_out_of_the_knob_mapping_where_it_lives``.

    A top-level ``question_subset`` is accepted as a fallback so that a caller holding a bare
    identity mapping — the driver's pre-flight, which has no knob mapping — can be asked the
    same question. No real row is in that shape.
    """
    problems: list[str] = []
    if profile.corpus_content_hash is None:
        return (
            f"profile {profile.name!r} declares no corpus_content_hash, so nothing about this "
            "row can be reconciled. An unreconcilable profile must not be reported as agreeing "
            "— that is the state the baseline arm was in while the check said it was fine.",
        )
    recorded = row.get("corpus_content_hash")
    # ``None`` is a turn that abstained before anything stamped the corpus onto it
    # (open-work 3.6a). It cannot contradict the profile, and treating it as a
    # contradiction would put one line per clarification into every report.
    if recorded is not None and str(recorded) != str(profile.corpus_content_hash):
        problems.append(
            f"profile claims corpus_content_hash {profile.corpus_content_hash!r}, "
            f"row records {recorded!r}"
        )
    if profile.question_subset is None:
        problems.append(
            f"profile {profile.name!r} declares no question_subset, so this row's question set "
            "cannot be reconciled. The corpus is pinned twice in arms.toml and the question set "
            "would be pinned not at all — which is how the same n over a replaced dataset "
            "passed every gate."
        )
    else:
        subset = recorded_question_subset(row)
        # Absent reads as "did not say", not as "disagrees", for the reason the corpus branch
        # gives one field up. Here the absent case has a *different* cause: the seven
        # ``proxy_*`` artifacts in ``runs/eval/`` predate ``scope_identity``'s writer
        # (2026-08-12) and carry ``None`` on all 1 351 rows. Refusing per row would strand
        # every artifact on disk, which is the reason ``provenance._knob_problem`` already
        # gives for warning rather than refusing in the same situation. The artifact-level
        # answer is ``provenance.reconciliation_lines``, which can see all the rows at once
        # and derives the set from the ids they carry.
        if subset is not None and str(subset) != str(profile.question_subset):
            problems.append(
                f"profile claims question_subset {profile.question_subset!r}, "
                f"row records {subset!r}"
            )

    # `corpus_release` is checked only when the profile declares one, which is the opposite
    # convention from `corpus_content_hash` above -- and deliberately. The digest is what makes a
    # row reconcilable at all, so its absence is a defect. A release is a *name a human pinned*,
    # and the arms measured before the knob existed have none: refusing them would strand every
    # artifact on disk to gain nothing, since the digest already reconciles the rows.
    if profile.corpus_release is not None:
        release = recorded_corpus_release(row)
        if release is not None and str(release) != str(profile.corpus_release):
            problems.append(
                f"profile claims corpus_release {profile.corpus_release!r}, "
                f"row records {release!r}"
            )
    return tuple(problems)


def recorded_corpus_release(row: Mapping[str, Any]) -> Any:
    """``corpus_release`` as this row records it, or ``None``.

    Read out of the knob mapping, where a ``Role.comparability`` knob lives, and **not** off the
    top of the row: ``corpus_content_hash`` is a ``RecordField`` and lives at the top, and the
    2026-08-11 defect was reading one from the other's home. Each field is read from its own.

    Absent reads as "did not say" rather than as disagreement, for the reason ``reconcile`` gives
    at the call site: every artifact on disk predates this knob.
    """
    knobs = row.get("knobs_resolved")
    if isinstance(knobs, Mapping) and "corpus_release" in knobs:
        return knobs["corpus_release"]
    return row.get("corpus_release")


def recorded_question_subset(row: Mapping[str, Any]) -> Any:
    """``question_subset`` as this row records it, or ``None``.

    Membership rather than ``.get()``, and it is load-bearing on the artifacts on disk:
    ``_resolved_knobs`` writes **every** declared knob, flattening ``UNSET`` to ``None``, so all
    1 351 rows of ``runs/eval/proxy_v4_corpus30872d3.jsonl`` do carry the key — with a ``None``
    value, measured 2026-08-20. A ``.get()`` chain would read that as "the knob mapping has
    nothing" and fall through to the top level, which is a different question. Both states still
    reach the caller as ``None``, because "recorded nothing" and "declined to say" are equally
    unable to contradict a profile; what the membership test protects is that the fallback fires
    only when there is genuinely no knob mapping to read.
    """
    knobs = row.get("knobs_resolved")
    if isinstance(knobs, Mapping) and "question_subset" in knobs:
        return knobs["question_subset"]
    return row.get("question_subset")
