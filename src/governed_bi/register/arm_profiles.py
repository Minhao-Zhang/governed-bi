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
    corpus: str | None = None
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
        out[name] = ArmProfile(
            name=name,
            description=str(body.get("description", "")),
            treatment=treatment,
            compare_to=body.get("compare_to"),
            corpus=body.get("corpus"),
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


def reconcile(profile: ArmProfile, knobs_resolved: Mapping[str, Any]) -> tuple[str, ...]:
    """Where a recorded run disagrees with what its arm profile claims. Empty means agreement.

    The point of writing the claim down is that it can be wrong. A profile saying ``corpus =
    "30872d3"`` beside a row recording a different ``corpus_content_hash`` is a mislabelled
    artifact, and mislabelled artifacts are how a number ends up quoted against the wrong
    treatment.

    Only what the profile actually asserts is checked. ``treatment`` is not checkable from one
    arm — it is a statement about a *pair* — so it is ``knobs_comparable``'s business and not
    reconciled here.
    """
    problems: list[str] = []
    if profile.corpus is not None:
        recorded = knobs_resolved.get("corpus_content_hash")
        if recorded is not None and not str(recorded).startswith(str(profile.corpus)):
            problems.append(
                f"profile claims corpus {profile.corpus!r}, row records {recorded!r}"
            )
    return tuple(problems)
