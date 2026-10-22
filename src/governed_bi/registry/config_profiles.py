"""This module records what a config claims changed. It does not set settings.

Inferring the setting under test from ``context_hash`` distinctness measures retrieval nondeterminism. Named
``config_profiles`` because ``configs`` is already a setting.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, fields
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

from .settings import setting_names
from .stored_names import artifact_get

__all__ = [
    "CONFIGS_FILE",
    "ConfigProfile",
    "config_profile",
    "load_config_profiles",
    "reconcile",
    "recorded_corpus_release",
    "recorded_question_subset",
]

# Beside this module so an installed wheel still finds it. Climbing out of the
# package only works from a source checkout.
CONFIGS_FILE = Path(__file__).resolve().parent / "configs.toml"


@dataclass(frozen=True, slots=True)
class ConfigProfile:
    """``under_test``, the digests ``reconcile`` reads, and the hypothesis pair are read by a check.

    ``corpus``, ``dataset``, ``compare_to``, ``description``, and ``notes`` are for a human.
    """

    name: str
    description: str
    under_test: frozenset[str]
    compare_to: str | None = None
    #: Git ref for a human. Nothing in a row carries it, so nothing reconciles against it.
    corpus: str | None = None
    #: Content digest on the row. A different namespace from ``corpus``.
    corpus_content_hash: str | None = None
    #: Git ref for a human. Not reconciled; collapsing it with the question set made that check vacuous.
    dataset: str | None = None
    question_subset: str | None = None
    #: What the config declares. Without it, a corpus setting under test cannot name itself.
    corpus_release: str | None = None
    #: Declared before the run, as a proportion of ``readout`` (3pp is 0.03).
    hypothesised_effect: float | None = None
    #: Required with the effect. MDE is in points of the whole population, and base rates differ.
    readout: str | None = None
    notes: str = ""


# Derived from the dataclass, not hand-listed. A key missing from the parser
# arrives as None, which readers treat as no claim.
_CONFIG_KEYS = frozenset(f.name for f in fields(ConfigProfile)) - {"name"}


def _hypothesis(body: Mapping[str, Any], *, config: str, source: str) -> tuple[float | None, str]:
    """Optional, and refused unless the pair is complete.

    Absence stays legal. Half a pair, or a number that is not a proportion, is refused: a made-up effect makes the power
    check pass.
    """
    effect = body.get("hypothesised_effect")
    readout = body.get("readout")
    if effect is None and readout is None:
        return None, ""
    if effect is None:
        raise ValueError(
            f"{source}: [config.{config}] declares no hypothesised_effect beside readout {readout!r}. "
            "A readout alone reaches `config_power_refusal`, which returns None on the missing "
            "effect and abstains -- so this half-declaration is the one shape that looks like a "
            "pre-registered hypothesis and checks nothing."
        )
    if readout is None:
        raise ValueError(
            f"{source}: [config.{config}] declares no readout beside hypothesised_effect {effect!r}. "
            "MDE is denominated in points of the whole population and two readouts' base rates "
            "differ by two orders of magnitude, so an effect size with no quantity attached "
            "cannot be compared against a detection floor."
        )
    if not isinstance(readout, str) or not readout.strip():
        raise ValueError(
            f"{source}: [config.{config}].readout must be the name of the quantity the effect is in, "
            f"not {readout!r}"
        )
    # A proportion: require_power compares abs(effect) to a floor that is one.
    # 3 for "3pp" clears every floor. Zero is refused: it is not "no hypothesis".
    if isinstance(effect, bool) or not isinstance(effect, (int, float)):
        raise ValueError(
            f"{source}: [config.{config}].hypothesised_effect must be a number in points of the "
            f"readout ({readout!r}), not {effect!r}"
        )
    if not 0 < float(effect) < 1:
        raise ValueError(
            f"{source}: [config.{config}].hypothesised_effect is {effect!r}, which is not in points of "
            f"the readout ({readout!r}) -- those are proportions, so 3pp is 0.03. `require_power` "
            "compares it against a floor that is a proportion, so a percentage-point figure "
            "clears every floor at every n and a zero clears none."
        )
    return float(effect), readout


def _parse_profiles(data: Mapping[str, Any], *, source: str) -> dict[str, ConfigProfile]:
    configs = data.get("config")
    if not isinstance(configs, Mapping):
        raise ValueError(f"{source}: no [config.*] tables")

    keys = setting_names()
    out: dict[str, ConfigProfile] = {}
    for name, body in configs.items():
        if not isinstance(body, Mapping):
            raise ValueError(f"{source}: [config.{name}] is not a table")
        # Refused, not ignored: an unknown key would parse and check nothing.
        unknown_keys = sorted(set(body) - _CONFIG_KEYS)
        if unknown_keys:
            raise ValueError(
                f"{source}: [config.{name}] declares {unknown_keys}, which are not ConfigProfile "
                f"fields. Accepted keys are {sorted(_CONFIG_KEYS)}."
            )
        raw = body.get("under_test", [])
        if not isinstance(raw, list) or not all(isinstance(x, str) for x in raw):
            raise ValueError(f"{source}: [config.{name}].under_test must be a list of setting names")
        under_test = frozenset(raw)
        # A typo that drops the setting looks like a data problem; a different name would certify a confounded pair.
        unknown = sorted(under_test - keys)
        if unknown:
            raise ValueError(
                f"{source}: [config.{name}].under_test names {unknown}, which are not "
                "declared settings in registry/settings.py"
            )
        # Mandatory. Without a digest, reconcile examines nothing and can report agreement.
        if not body.get("corpus_content_hash"):
            raise ValueError(
                f"{source}: [config.{name}] declares no corpus_content_hash. That is the only "
                "field `reconcile` can compare, so a config without one is unreconcilable and "
                "would report agreement from a check that examined nothing. Read the digest "
                "off any row of the config's artifact; `corpus` is the git ref and is not it."
            )
        # Mandatory. Without the question set, a different population can still agree on the digest.
        if not body.get("question_subset"):
            raise ValueError(
                f"{source}: [config.{name}] declares no question_subset. The corpus is pinned "
                "twice here and the question set would be pinned not at all, so a config rerun "
                "on a different dataset would agree with this profile on everything it "
                "compares. Read it off any row's settings_resolved['question_subset'], or hash "
                "the config's question ids the way eval/provenance.py::scope_identity does; "
                "`dataset` is the git ref and is not it."
            )
        # Mandatory only when the release is the setting under test. Otherwise the digest already reconciles the rows.
        if "corpus_release" in under_test and not body.get("corpus_release"):
            raise ValueError(
                f"{source}: [config.{name}] says its treatment is corpus_release and does not name "
                "one. `reconcile` compares a release only when the profile declares it, so this "
                "config's own treatment would be the single field about it that nothing checks. "
                "Name the corpus TAG the config was measured on; `corpus` is the git ref and "
                "`corpus_content_hash` is the digest, and neither is it."
            )
        hypothesised_effect, readout = _hypothesis(body, config=name, source=source)
        # One key per field. ``**body`` would be a second home for a setting's value.
        values: dict[str, Any] = dict(
            name=name,
            description=str(body.get("description", "")),
            under_test=under_test,
            compare_to=body.get("compare_to"),
            corpus=body.get("corpus"),
            corpus_content_hash=body.get("corpus_content_hash"),
            dataset=body.get("dataset"),
            question_subset=body.get("question_subset"),
            corpus_release=body.get("corpus_release"),
            hypothesised_effect=hypothesised_effect,
            readout=readout or None,
            notes=str(body.get("notes", "")),
        )
        # A field missing from the call above arrives as None, which readers treat as no claim.
        unwired = sorted({f.name for f in fields(ConfigProfile)} - set(values))
        if unwired:
            raise RuntimeError(
                f"ConfigProfile declares {unwired}, which _parse_profiles does not pass, so every "
                "profile would carry the field's default no matter what the file says. That is "
                "how `hypothesised_effect` silenced the power check: add the key here."
            )
        out[name] = ConfigProfile(**values)
    return out


@lru_cache(maxsize=1)
def load_config_profiles(path: Path | None = None) -> Mapping[str, ConfigProfile]:
    """Cached: the file is a constant of a run."""
    source = path or CONFIGS_FILE
    with source.open("rb") as fh:
        return _parse_profiles(tomllib.load(fh), source=str(source))


def config_profile(name: str, *, path: Path | None = None) -> ConfigProfile:
    """Raises on an unknown name rather than returning an empty setting under test."""
    profiles = load_config_profiles(path)
    try:
        return profiles[name]
    except KeyError:
        raise KeyError(
            f"no [config.{name}] in {path or CONFIGS_FILE}; declared configs are "
            f"{sorted(profiles)}"
        ) from None


def reconcile(profile: ConfigProfile, row: Mapping[str, Any]) -> tuple[str, ...]:
    """Empty means agreement. Only asserted fields are checked.
    ``under_test`` is about a pair and is not reconciled here.
    ``corpus_content_hash`` comes from the row. ``question_subset`` comes from
    the setting mapping, then the row. No digest is a problem, not a pass.
    """
    problems: list[str] = []
    if profile.corpus_content_hash is None:
        return (
            f"profile {profile.name!r} declares no corpus_content_hash, so nothing about this "
            "row can be reconciled. An unreconcilable profile must not be reported as agreeing "
            "— that is the state the baseline config was in while the check said it was fine.",
        )
    recorded = row.get("corpus_content_hash")
    # None cannot contradict the profile: the corpus was never written onto the turn.
    if recorded is not None and str(recorded) != str(profile.corpus_content_hash):
        problems.append(
            f"profile claims corpus_content_hash {profile.corpus_content_hash!r}, "
            f"row records {recorded!r}"
        )
    if profile.question_subset is None:
        problems.append(
            f"profile {profile.name!r} declares no question_subset, so this row's question set "
            "cannot be reconciled. The corpus is pinned twice in configs.toml and the question set "
            "would be pinned not at all — which is how the same n over a replaced dataset "
            "passed every check."
        )
    else:
        subset = recorded_question_subset(row)
        # Absent means the row did not say. Refusing each such row would strand stored artifacts.
        if subset is not None and str(subset) != str(profile.question_subset):
            problems.append(
                f"profile claims question_subset {profile.question_subset!r}, "
                f"row records {subset!r}"
            )

    # Checked only when the profile declares one. A missing digest is a defect; a missing release is not, because the
    # digest already reconciles the row.
    if profile.corpus_release is not None:
        release = recorded_corpus_release(row)
        if release is not None and str(release) != str(profile.corpus_release):
            problems.append(
                f"profile claims corpus_release {profile.corpus_release!r}, "
                f"row records {release!r}"
            )
    return tuple(problems)


def recorded_corpus_release(row: Mapping[str, Any]) -> Any:
    """Prefer the setting mapping. The top of the row is the fallback when that mapping has no ``corpus_release``."""
    settings = artifact_get(row, "settings_resolved")
    if isinstance(settings, Mapping) and "corpus_release" in settings:
        return settings["corpus_release"]
    return row.get("corpus_release")


def recorded_question_subset(row: Mapping[str, Any]) -> Any:
    """Membership, not ``.get``: a recorded ``None`` must not fall through to the top of the row.

    The fallback fires only when there is no setting mapping.
    """
    settings = artifact_get(row, "settings_resolved")
    if isinstance(settings, Mapping) and "question_subset" in settings:
        return settings["question_subset"]
    return row.get("question_subset")
