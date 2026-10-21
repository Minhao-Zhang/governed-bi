"""Config profiles: the committed declaration of what each measured config changed.

**This module does not set settings and must not learn to.** ``registry/settings.py`` is the one home
for a setting's value; a second place deciding that is a defect this
repository has already paid for. What lives here is the *claim* — "config v4's setting under test was the
prompt" — which had no committed home at all. An config's identity lived in a gitignored ``.env``
on one machine while ``runs/eval/`` named the config, so nothing a reader could fetch said what the
name meant.

The claim is because of audit D9. ``eval/report.py::settings_comparable`` will not
certify a pair that cannot name its setting under test, having learned that inferring the setting under test from
``context_hash`` distinctness measures retrieval nondeterminism instead. Somebody has to say what
changed; this is where they say it, in a file that diffs.

Named ``config_profiles`` and not ``configs`` because ``configs`` is already a ``Role.scope`` setting, and
two things in the register answering to one word is how the second reader gets in.
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

#: Beside this module, because the register owns config profiles. It lived at the repo root until
#: 2026-08-11, reached by climbing four parents out of the package — which resolves only from a
#: source checkout: from an installed wheel the same climb lands above ``site-packages`` and the
#: file is not there. As package data it travels with the distribution instead.
CONFIGS_FILE = Path(__file__).resolve().parent / "configs.toml"


@dataclass(frozen=True, slots=True)
class ConfigProfile:
    """One config's declared identity.

    **Five fields are read by a check**, and the docstring here said ``setting under test`` was the only
    one until 2026-08-24 — which is how three of the five came to be added without a loader.
    ``setting under test`` is ``eval/report.py::settings_comparable``'s; ``corpus_content_hash``,
    ``question_subset`` and ``corpus_release`` are :func:`reconcile`'s; ``hypothesised_effect``
    and ``readout`` are ``eval/provenance.py::config_power_refusal``'s. ``corpus``, ``dataset``,
    ``compare_to``, ``description`` and ``notes`` are for a human, and the driver prints the last
    three.
    """

    name: str
    description: str
    under_test: frozenset[str]
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
    #: The question set the rows must carry, in the ``question_subset`` scope setting's own format
    #: and under its own name. This is what :func:`reconcile` reads.
    question_subset: str | None = None
    #: The corpus **release tag** this config was measured on, matching the ``corpus_release``
    #: setting that affects results. Distinct from :attr:`corpus` (a ref a human checks out) and from
    #: :attr:`corpus_content_hash` (what a row carries): a release is what a config *declares*, and
    #: without it a config whose setting under test is the corpus cannot name its own setting under test.
    corpus_release: str | None = None
    #: The effect this config exists to detect, in points of the readout below (0.03 = 3pp).
    #:
    #: **Declared before the run, which is the whole point.** ``eval/power.py::require_power``
    #: refuses a config that cannot detect its own hypothesis, and until this field existed it had
    #: no caller -- so the check that stops an underpowered config before it spends anything was
    #: reachable only by a caller passing a number it had made up on the spot.
    hypothesised_effect: float | None = None
    #: Which quantity :attr:`hypothesised_effect` is denominated in. Required alongside it,
    #: because **MDE is denominated in points of the whole population** and two readouts' base
    #: rates can differ by two orders of magnitude. A mechanism indicator with a 2.15pp ceiling
    #: has 1.9 resolvable steps against EX's 28.5, and a draft of this design read the smaller
    #: MDE as the better instrument. Naming the readout is what makes that error visible.
    readout: str | None = None
    notes: str = ""


#: Every key an ``[config.*]`` table may carry: :class:`ConfigProfile`'s own fields, less ``name``,
#: which is the table's name rather than a key inside it.
#:
#: **Derived and not hand-listed**, because a hand-list is what this was. ``_parse_profiles``
#: passed nine keys while the dataclass had twelve fields, so ``hypothesised_effect``, ``readout``
#: and ``corpus_release`` could be declared in the file, parse without complaint, and arrive as
#: ``None`` — which is the same value as "this config claims nothing". ``config_power_refusal`` reads
#: the first of those and abstains on ``None``, so the check that stops an underpowered paid config
#: was silent for every config that could ever be declared.
_CONFIG_KEYS = frozenset(f.name for f in fields(ConfigProfile)) - {"name"}


def _hypothesis(body: Mapping[str, Any], *, config: str, source: str) -> tuple[float | None, str]:
    """The config's pre-registered effect and the quantity it is in, or ``(None, "")``.

    **Optional, and refused unless it is complete.** Optional because no config on disk declares one:
    all four in ``configs.toml`` were measured before the field existed, and an effect size written
    down after the measurement is not a hypothesis — it is the result, and a made-up one makes
    ``config_power_refusal`` *pass* rather than abstain, which is strictly worse than the silence.
    So absence stays legal here, and what is refused is every way of declaring it that a reader
    could mistake for a declaration: an unknown key, half a pair, a non-number, or a number in
    the wrong unit.
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
    # `0 < e < 1`, because `require_power` compares `abs(effect)` against a floor that is a
    # proportion. `hypothesised_effect = 3` for "3pp" clears every floor at every n, so the unit
    # error would make the check pass -- the same shape as `require_power(discordant=0.29)`, which
    # is already on record as approving the exact config it exists to refuse. Zero is refused too: it
    # is not "no hypothesis" and must not be spelled the same way as one.
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
        # **Refused, not ignored, and this is the lock on the defect the whole file is about.**
        # Ignoring an unrecognised key is how `hypothesised_effect = 0.03` in `configs.toml` came to
        # parse clean and do nothing for a fortnight. The field is spelled the British way, so
        # `hypothesized_effect` is one keystroke from a declaration that checks nothing.
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
        # Refused rather than ignored: a typo that reads as "no setting under test declared" would turn
        # a real comparison into `cannot_evaluate` and look like a data problem, and one that
        # reads as a *different* setting would certify a confounded pair.
        unknown = sorted(under_test - keys)
        if unknown:
            raise ValueError(
                f"{source}: [config.{name}].under_test names {unknown}, which are not "
                "declared settings in registry/settings.py"
            )
        # **Mandatory, and this is the half that outlived the first fix.** `reconcile` was
        # repaired on 2026-08-11 and `v3_fold` — the baseline v4 is measured against — still
        # declared no digest, so the guard was never entered, the pre-flight check passed, and
        # the driver printed "every row agrees with the profile in configs.toml" about a config it
        # had not compared. Refusing here is what stops the *next* config reintroducing it: an
        # optional field that silences a check is a check with an off switch nobody labelled.
        if not body.get("corpus_content_hash"):
            raise ValueError(
                f"{source}: [config.{name}] declares no corpus_content_hash. That is the only "
                "field `reconcile` can compare, so a config without one is unreconcilable and "
                "would report agreement from a check that examined nothing. Read the digest "
                "off any row of the config's artifact; `corpus` is the git ref and is not it."
            )
        # **Mandatory for the same reason, one field over.** This file pinned the corpus twice
        # and the question set not at all, so a rerun on a replaced dataset produced the same
        # n = 1 351, a different population, and passed every check — the corpus digest and the
        # settings both matched. Recovering which questions the shipped configs ran took a
        # schema-filtered count across four versions of another repository (see configs.toml's
        # header); the field is mandatory so the next config cannot cost that again.
        if not body.get("question_subset"):
            raise ValueError(
                f"{source}: [config.{name}] declares no question_subset. The corpus is pinned "
                "twice here and the question set would be pinned not at all, so a config rerun "
                "on a different dataset would agree with this profile on everything it "
                "compares. Read it off any row's settings_resolved['question_subset'], or hash "
                "the config's question ids the way eval/provenance.py::scope_identity does; "
                "`dataset` is the git ref and is not it."
            )
        # **Mandatory only for the config that says the release IS its setting under test**, which is the
        # opposite convention from the two fields above and matches `reconcile`'s. An config
        # declaring `setting under test = ["corpus_release"]` and no release leaves `reconcile`'s release
        # branch unentered, so the one thing the config exists to vary is the one thing nothing
        # checks. Configs treating something else keep it optional: every artifact in `runs/eval/`
        # predates the setting, and refusing all seven buys nothing the digest does not already
        # reconcile.
        if "corpus_release" in under_test and not body.get("corpus_release"):
            raise ValueError(
                f"{source}: [config.{name}] says its treatment is corpus_release and does not name "
                "one. `reconcile` compares a release only when the profile declares it, so this "
                "config's own treatment would be the single field about it that nothing checks. "
                "Name the corpus TAG the config was measured on; `corpus` is the git ref and "
                "`corpus_content_hash` is the digest, and neither is it."
            )
        hypothesised_effect, readout = _hypothesis(body, config=name, source=source)
        # Explicit, one key per field, because `**body` would take a setting's value from a file --
        # a second home for a setting, which `registry/settings.py` refuses -- and would silently accept whatever a
        # future TOML key is called. What makes the explicit list safe is the coverage check
        # below: this call passed nine keys against twelve fields for a fortnight, and the three
        # it dropped were the three a check reads.
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
        # The structural half, and it is what keeps this defect from recurring rather than
        # merely fixed. A field added to `ConfigProfile` and not to the call above arrives as its
        # dataclass default -- `None`, which every consumer reads as "no claim was made" -- so
        # the omission is invisible at the declaration site and at the reading site both. Loud
        # on the first load instead, which in this tree means CI.
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
    """Every declared config, by name. Cached: the file is a constant of a run."""
    source = path or CONFIGS_FILE
    with source.open("rb") as fh:
        return _parse_profiles(tomllib.load(fh), source=str(source))


def config_profile(name: str, *, path: Path | None = None) -> ConfigProfile:
    """One config's profile.

    Raises on an unknown name rather than returning a profile with an empty setting under test, which
    would silently degrade a real comparison into "nobody said what changed".
    """
    profiles = load_config_profiles(path)
    try:
        return profiles[name]
    except KeyError:
        raise KeyError(
            f"no [config.{name}] in {path or CONFIGS_FILE}; declared configs are "
            f"{sorted(profiles)}"
        ) from None


def reconcile(profile: ConfigProfile, row: Mapping[str, Any]) -> tuple[str, ...]:
    """Where a recorded run disagrees with what its config profile claims. Empty means agreement.

    The point of writing the claim down is that it can be wrong. A profile naming one corpus
    beside a row recording another is a mislabelled artifact, and mislabelled artifacts are how
    a number ends up quoted against the wrong setting under test.

    ``row`` is a **measurement row** (or anything shaped like one — the session's own identity
    reads the same way, which is what lets the driver run this before the first paid question).

    **Two things about this were wrong until 2026-08-11.** It took ``settings_resolved`` and read
    ``corpus_content_hash`` out of it; that field is a ``RecordField``, not a setting, and is
    never in the setting mapping, so the lookup returned ``None`` and the function returned
    agreement unconditionally. And the value it compared against was ``corpus`` — the corpus
    repository's git ref, ``30872d3`` — while every row records the content digest,
    ``86ed1dbf…``. Two identifiers in two namespaces, compared with ``startswith``, which
    could never match. Both halves failed silently and in the safe-looking direction, which is
    why the profile field is now split in two and only the digest is reconciled.

    Only what the profile asserts is checked. ``setting under test`` is not checkable from one
    config — it is a statement about a *pair* — so it is ``settings_comparable``'s business and not
    reconciled here.

    **A profile with no digest is a problem, not a pass.** That was the third way this function
    returned agreement without comparing anything, and it survived the other two: ``v3_fold``
    declared no ``corpus_content_hash``, the ``is not None`` guard was never entered, and a run
    launched ``--config v3_fold`` against any corpus at all cleared the pre-flight check *and* was
    told every row agreed. ``load_config_profiles`` now refuses such a file, and this is the second
    lock, because :class:`ConfigProfile` can be constructed directly and a test fixture is exactly
    where an unreconcilable profile gets invented.

    **``question_subset`` is read out of ``settings_resolved``, which is the opposite of the rule
    above, and the difference is the whole point.** The 2026-08-11 defect was not "the setting
    mapping is the wrong place to look" — it was looking somewhere the field never is.
    ``corpus_content_hash`` is a ``RecordField`` and lives at the top of the row;
    ``question_subset`` is a ``Role.scope`` setting resolved by
    ``eval/provenance.py::scope_identity`` and lives in the row's setting mapping, verified on
    ``runs/eval/live_full_gpt-5.6-luna_xhigh_topdefault_lexical.jsonl``, where it reads
    ``1351:423a3f4b65fb``. So each field is read from its own home. A reader who "fixes" this
    to match the corpus branch reintroduces the vacuous lookup in the other direction, and the
    test that catches that is
    ``test_reconcile_reads_the_question_set_out_of_the_setting_mapping_where_it_lives``.

    A top-level ``question_subset`` is accepted as a fallback so that a caller holding a bare
    identity mapping — the driver's pre-flight, which has no setting mapping — can be asked the
    same question. No real row is in that shape.
    """
    problems: list[str] = []
    if profile.corpus_content_hash is None:
        return (
            f"profile {profile.name!r} declares no corpus_content_hash, so nothing about this "
            "row can be reconciled. An unreconcilable profile must not be reported as agreeing "
            "— that is the state the baseline config was in while the check said it was fine.",
        )
    recorded = row.get("corpus_content_hash")
    # ``None`` is a turn that abstained before anything wrote the corpus onto it
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
            "cannot be reconciled. The corpus is pinned twice in configs.toml and the question set "
            "would be pinned not at all — which is how the same n over a replaced dataset "
            "passed every check."
        )
    else:
        subset = recorded_question_subset(row)
        # Absent reads as "did not say", not as "disagrees", for the reason the corpus branch
        # gives one field up. Here the absent case has a *different* cause: the seven
        # ``proxy_*`` artifacts in ``runs/eval/`` predate ``scope_identity``'s writer
        # (2026-08-12) and carry ``None`` on all 1 351 rows. Refusing per row would strand
        # every artifact on disk, which is the reason ``provenance._setting_problem`` already
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
    # and the configs measured before the setting existed have none: refusing them would strand every
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

    Read out of the setting mapping, where a ``a recorded setting`` setting lives, and **not** off the
    top of the row: ``corpus_content_hash`` is a ``RecordField`` and lives at the top, and the
    2026-08-11 defect was reading one from the other's home. Each field is read from its own.

    Absent reads as "did not say" rather than as disagreement, for the reason ``reconcile`` gives
    at the call site: every artifact on disk predates this setting.
    """
    settings = artifact_get(row, "settings_resolved")
    if isinstance(settings, Mapping) and "corpus_release" in settings:
        return settings["corpus_release"]
    return row.get("corpus_release")


def recorded_question_subset(row: Mapping[str, Any]) -> Any:
    """``question_subset`` as this row records it, or ``None``.

    Membership rather than ``.get()``, and it is on the artifacts on disk:
    ``_resolved_settings`` writes **every** declared setting, flattening ``UNSET`` to ``None``, so all
    1 351 rows of ``runs/eval/proxy_v4_corpus30872d3.jsonl`` do carry the key — with a ``None``
    value, measured 2026-08-20. A ``.get()`` chain would read that as "the setting mapping has
    nothing" and fall through to the top level, which is a different question. Both states still
    reach the caller as ``None``, because "recorded nothing" and "declined to say" are equally
    unable to contradict a profile; what the membership test protects is that the fallback fires
    only when there is no setting mapping to read.
    """
    settings = artifact_get(row, "settings_resolved")
    if isinstance(settings, Mapping) and "question_subset" in settings:
        return settings["question_subset"]
    return row.get("question_subset")
