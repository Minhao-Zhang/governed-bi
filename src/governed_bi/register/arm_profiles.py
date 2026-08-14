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

__all__ = ["ARMS_FILE", "ArmProfile", "arm_profile", "load_arm_profiles", "reconcile"]

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
        out[name] = ArmProfile(
            name=name,
            description=str(body.get("description", "")),
            treatment=treatment,
            compare_to=body.get("compare_to"),
            corpus=body.get("corpus"),
            corpus_content_hash=body.get("corpus_content_hash"),
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
    return tuple(problems)
