"""What identifies one measurement run, and whether an artifact shares it.

Resume reads that identity off the rows. A filename omits the corpus, and an
explicit output path bypasses the filename. This module resolves facts about
the running process; the register owns setting declarations.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Collection, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from governed_bi.registry.stored_names import artifact_get

from ..registry.config_profiles import reconcile, recorded_question_subset

__all__ = [
    "append_refusal",
    "config_power_refusal",
    "config_startup_refusal",
    "PRIOR_DISCORDANT",
    "PRIOR_OF",
    "derived_question_set",
    "flag_conflict",
    "git_provenance",
    "harness_settings",
    "reconciliation_lines",
    "resume_identity_problem",
    "scope_identity",
    "short_digest",
    "truncation_notice",
]

#: Twelve hex characters. Enough to tell two attempts at one run apart, and short on every row.
_DIGEST_CHARS = 12


def short_digest(parts: Iterable[str]) -> str:
    """A stable digest of a set of strings.

    Sorted before hashing. Iteration order is an accident, and a digest that
    moved with it would report drift on every run.
    """
    joined = "\n".join(sorted(str(p) for p in parts))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:_DIGEST_CHARS]


def _git(repo: Path, *args: str) -> str | None:
    """``git`` in ``repo``, or ``None`` if it cannot be answered.

    A missing answer is unmeasured, not a shared value. Decode as UTF-8:
    ``text=True`` uses the locale codec and dies on a non-ASCII diff on Windows.
    """
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return (proc.stdout or "").strip() or None


def git_provenance(repo: Path) -> dict[str, Any]:
    """Resume-drift keys from the working tree.

    Two commits are normal across runs and corruption inside one artifact.
    ``diff_sha256`` covers an uncommitted edit. Ask ``main``, then ``origin/main``.
    """
    head = _git(repo, "rev-parse", "HEAD")
    main = _git(repo, "rev-parse", "main") or _git(repo, "rev-parse", "origin/main")
    diff = _git(repo, "diff", "HEAD")
    # Empty stdout is also "git could not answer". Status is separate: it sees untracked files.
    status = _git(repo, "status", "--porcelain")
    dirty: bool | None = None
    if head is not None:
        dirty = status is not None
    return {
        "git_sha": head,
        "git_main_sha": main,
        "working_tree_dirty": dirty,
        "diff_sha256": (
            hashlib.sha256(diff.encode("utf-8")).hexdigest() if diff is not None else None
        ),
    }


def scope_identity(
    *,
    schemas: Collection[str],
    question_ids: Collection[str],
    dataset_file: Path,
) -> dict[str, Any]:
    """What was served, from which file, to which questions.

    Digests, not name lists: drift is between attempts, and the corpus hash
    already pins the names. ``split`` is the file stem, not the question ids.
    """
    return {
        "schemas_under_test": f"{len(schemas)}:{short_digest(schemas)}",
        "split": dataset_file.stem,
        "question_subset": f"{len(question_ids)}:{short_digest(question_ids)}",
    }


def harness_settings(
    *,
    repo: Path,
    schemas: Collection[str],
    question_ids: Collection[str],
    dataset_file: Path,
    serve_workers: int,
) -> dict[str, Any]:
    """``build_workers`` is not written: a number for a stage that did not run is a fake measurement."""
    return {
        **git_provenance(repo),
        "serve_workers": int(serve_workers),
        **scope_identity(
            schemas=schemas, question_ids=question_ids, dataset_file=dataset_file
        ),
    }


def flag_conflict(*, resume: bool, truncate: bool) -> str | None:
    """Why ``--resume`` and ``--truncate`` cannot both be honoured, or ``None``.

    One keeps the file and one discards it. A pure function so a test can
    reach the decision without building a model and a database.
    """
    if resume and truncate:
        return (
            "--truncate discards the artifact and --resume continues it; pick one. If the "
            "intention was to restart a run that is part-done, that is --truncate alone."
        )
    return None


def _rows_on_disk(out_path: Path) -> int:
    return sum(1 for line in out_path.read_text(encoding="utf-8").splitlines() if line.strip())


def append_refusal(out_path: Path, *, resume: bool, truncate: bool) -> str | None:
    """Appending without ``--resume`` puts two populations in one file, before the repeated-id check."""
    if resume or not out_path.exists() or not out_path.stat().st_size:
        return None
    if truncate:
        return None
    return (
        f"{out_path} already holds {_rows_on_disk(out_path)} row(s) and --resume was not "
        "passed.\nAppending would put two populations in one artifact and report EX over the "
        "union. Pass --resume to continue that run, --truncate to DISCARD those rows and start "
        "over, or move the file aside."
    )


def truncation_notice(out_path: Path, *, resume: bool, truncate: bool) -> str | None:
    """What ``--truncate`` will discard. ``--force-fresh`` does not touch the file; this flag is the only discard, and
    it names the row count first."""
    if resume or not truncate:
        return None
    if not out_path.exists() or not out_path.stat().st_size:
        return None
    rows = _rows_on_disk(out_path)
    return (
        f"--truncate: discarding {rows} measured row(s) at {out_path} and starting over. "
        "Those rows were paid for; if that was not the intention, stop now — the file is "
        "overwritten on the next line."
    )


def _is_pre_routing_abstention(row: Mapping[str, Any]) -> bool:
    """Ended before the identity was written. Counted, not warned. A later ``clarification`` that reached ``finish``
    needs both identity fields absent."""
    return (
        str(row.get("outcome")) == "clarification"
        and not (artifact_get(row, "retrieved_tables") or ())
        and row.get("corpus_content_hash") is None
        and row.get("prompt_set_hash") is None
    )


def _identity_problem(
    rows: Sequence[Mapping[str, Any]], field: str, want: Any
) -> tuple[str | None, str | None, int]:
    foreign: dict[Any, int] = {}
    unwritten = 0
    pre_routing = 0
    for row in rows:
        value = row.get(field)
        if value is None:
            if _is_pre_routing_abstention(row):
                pre_routing += 1
            else:
                unwritten += 1
            continue
        if value != want:
            foreign[value] = foreign.get(value, 0) + 1

    refusal = None
    if foreign:
        lines = [f"  the artifact carries a different {field}:", f"    this run: {want}"]
        lines += [
            f"    on disk : {value}  ({n} rows)"
            for value, n in sorted(foreign.items(), key=lambda kv: -kv[1])
        ]
        refusal = "\n".join(lines)

    warning = None
    if unwritten:
        warning = (
            f"{unwritten} resumed row(s) carry no {field} and did not abstain before routing; "
            "they predate the field and cannot prove they are the same treatment"
        )
    return refusal, warning, pre_routing


def _setting_problem(
    rows: Sequence[Mapping[str, Any]],
    *,
    settings_resolved: Mapping[str, Any],
    affects_results: Collection[str],
) -> tuple[list[str], list[str]]:
    """Where the recorded configuration disagrees with this run.

    Content hashes miss top-n, embed, and the model. Compare ``repr`` as JSON
    would store it. Missing on both sides is skipped; recorded ``None`` is a value.
    """
    refusals: list[str] = []
    warnings: list[str] = []
    for key in sorted(affects_results):
        seen: set[str] = set()
        absent = 0
        for row in rows:
            recorded = artifact_get(row, "settings_resolved")
            if not isinstance(recorded, Mapping) or key not in recorded:
                absent += 1
                continue
            seen.add(_as_recorded(recorded[key]))
        declared = key in settings_resolved
        if not seen and not declared:
            continue
        if not seen or not declared:
            warnings.append(
                f"comparability setting {key!r} is recorded on "
                f"{'the artifact' if seen else 'this run'} and not on "
                f"{'this run' if seen else 'the artifact'}, so the two cannot be shown to "
                "share it"
            )
            continue
        want = settings_resolved[key]
        if absent:
            warnings.append(
                f"{absent} resumed row(s) carry no {key!r}, so they cannot be shown to have "
                "run under this run's value"
            )
        if seen != {_as_recorded(want)}:
            refusals.append(
                f"  the artifact ran under a different {key}:\n"
                f"    this run: {_as_recorded(want)}\n"
                f"    on disk : {', '.join(sorted(seen))}"
            )
    return refusals, warnings


def _as_recorded(value: Any) -> str:
    """``repr`` as JSON would store it, so a tuple and the list it comes back as are one configuration."""
    try:
        return repr(json.loads(json.dumps(value, default=str)))
    except (TypeError, ValueError):
        # Not encodable, so no artifact holds it. The in-memory repr is what two of them can share.
        return repr(value)


def resume_identity_problem(
    rows: Sequence[Mapping[str, Any]],
    *,
    corpus_content_hash: str | None,
    prompt_set_hash: str | None,
    settings_resolved: Mapping[str, Any],
    affects_results: Collection[str],
    question_ids: Collection[str],
    replay_routing: bool,
) -> tuple[str, list[str]]:
    """Same configuration as this run. Misses gold edited under the same ids. ``routing_pinned`` is an outcome, so a
    pinned resume of pre-routing rows is invisible."""
    problems: list[str] = []
    warnings: list[str] = []

    for field, want in (
        ("corpus_content_hash", corpus_content_hash),
        ("prompt_set_hash", prompt_set_hash),
    ):
        refusal, warning, pre_routing = _identity_problem(rows, field, want)
        if refusal:
            problems.append(refusal)
        if warning:
            warnings.append(warning)
        if pre_routing:
            warnings.append(
                f"{pre_routing} resumed row(s) carry no {field} because the turn abstained "
                "before routing; that is a declared path, not a treatment gap (open-work "
                "3.6a)"
            )

    setting_refusals, setting_warnings = _setting_problem(
        rows, settings_resolved=settings_resolved, affects_results=affects_results
    )
    problems.extend(setting_refusals)
    warnings.extend(setting_warnings)

    # Ids the artifact has and this run does not. Dataset or scope changed; name both.
    resumed = {str(row.get("question_id")) for row in rows}
    stale = sorted(resumed - set(map(str, question_ids)))
    if stale:
        problems.append(
            f"  {len(stale)} row(s) name questions this run does not cover, so the artifact and "
            f"this run are not one population. Either --dataset changed, or --limit/--per-schema "
            f"narrowed the scope. Example question id: {stale[0]}"
        )

    # Only a replayed run can record `True`. The other direction is invisible:
    # a pinned run whose kept rows all ended before routing also has none.
    if not replay_routing:
        pinned_rows = sum(1 for row in rows if row.get("routing_pinned") is True)
        if pinned_rows:
            problems.append(
                f"  {pinned_rows} row(s) had their routing replayed from another artifact and "
                "this run routes for itself. --replay-routing is part of the treatment. The "
                "driver's default filename now carries a _pinned segment, so the two configs "
                "normally land in different files -- but --out names the file directly and "
                "bypasses the tag, which is the case this check still has to catch."
            )

    if problems:
        problems.append(
            "  Two treatments in one artifact is not a config. Rename the artifact and start a "
            "new one, or restore the treatment it was measured under."
        )
    return "\n".join(problems), warnings


#: Discordance assumed when a hypothesis has no prior pair. A fraction, not a
#: decimal, so it is not a rate measured here. Integer arithmetic: a float
#: count understates the detection floor.
PRIOR_DISCORDANT, PRIOR_OF = 20, 131


def config_power_refusal(profile: Any, n: int) -> str | None:
    """Why this config cannot detect what it claims, or ``None``.

    No declared hypothesis means no check. Inventing an effect size would
    quote this function's number as the config's. The readout is named, not used.
    """
    effect = getattr(profile, "hypothesised_effect", None)
    if effect is None:
        return None
    readout = getattr(profile, "readout", None)
    if not readout:
        return (
            f"--config {profile.name} declares hypothesised_effect={effect} and no readout. An "
            "effect size with no quantity attached cannot be compared against a detection floor: "
            "MDE is in points of the whole population, and two readouts' base rates differ by two "
            "orders of magnitude. Name the readout in configs.toml."
        )

    from governed_bi.eval.power import UnderpoweredConfiguration, require_power

    # A count, never a float. At least one: zero discordance is unmeasured and must not pass.
    estimated = max(1, int(n) * PRIOR_DISCORDANT // PRIOR_OF)
    try:
        require_power(n=int(n), discordant=estimated, hypothesised_effect=float(effect))
    except UnderpoweredConfiguration as err:
        return (
            f"--config {profile.name} cannot detect its own hypothesis on {readout}:\n"
            f"  {err}\n"
            f"  The discordance is an ESTIMATE: {estimated} of {int(n)}, scaled from the only "
            f"prior pair on disk ({PRIOR_DISCORDANT} of {PRIOR_OF} questions, another "
            "repository's beer_factory run). It is not a measurement of this config.\n"
            "  Raise n at a comparable discordance rate, or restrict the population to the "
            "stratum the treatment actually reaches -- and say so in configs.toml."
        )
    return None


def config_startup_refusal(profile: Any, session_identity: Mapping[str, Any]) -> str | None:
    """Why this config must not run against this session, or ``None``.

    Asked before the first paid question. Both reconcilable fields are
    required. A pure function so a test can reach it without building models.
    """
    mislabelled = reconcile(profile, session_identity)
    if not mislabelled:
        return None
    # Name the field that mismatched. One word for two identifiers sent a question-set mismatch to the corpus flag.
    lines = [f"--config {profile.name} does not match this run's identity:"]
    lines += [f"  {problem}" for problem in mislabelled]
    lines.append(
        "  Fix configs.toml, or point --corpus-dir / --dataset at what the profile names. A run "
        "labelled with a config it did not measure is worse than an unlabelled one."
    )
    return "\n".join(lines)


def derived_question_set(rows: Sequence[Mapping[str, Any]]) -> str | None:
    """The question set the rows contain. A part-done run is not the set the run set out to cover, so this is only the
    fallback when nothing recorded the setting."""
    ids = [row.get("question_id") for row in rows]
    if not ids or any(qid is None for qid in ids):
        return None
    unique = {str(qid) for qid in ids}
    return f"{len(unique)}:{short_digest(unique)}"


def _question_set_from_the_ids(
    rows: Sequence[Mapping[str, Any]], profile: Any
) -> tuple[str | None, str | None]:
    """``(problem, note)`` when no row recorded ``question_subset``.

    An agreement is a note, not silence. The same sentence for a recorded set
    and a set hashed afterwards would read the weaker check as the stronger.
    """
    declared = getattr(profile, "question_subset", None)
    if declared is None:
        return None, None  # `reconcile` already refuses the profile outright.
    if any(recorded_question_subset(row) is not None for row in rows):
        return None, None  # The per-row check did the work.
    derived = derived_question_set(rows)
    if derived is None:
        return (
            "  no row records a question_subset and no row names a question, so this artifact "
            "cannot be shown to be the config's question set",
            None,
        )
    if derived != str(declared):
        return (
            f"  the artifact holds a different question set:\n"
            f"    configs.toml: {declared}\n"
            f"    on disk  : {derived}  (derived from the question ids, since no row records "
            f"the setting)",
            None,
        )
    return None, (
        f"  no row records question_subset (the writer postdates this artifact); the "
        f"{derived.split(':')[0]} question ids it holds hash to the declared {declared}"
    )


def reconciliation_lines(rows: Sequence[Mapping[str, Any]], profile: Any) -> list[str]:
    """One line per disagreement. Agreement is printed only when there was something to agree about."""
    counts: dict[str, int] = {}
    for row in rows:
        for problem in reconcile(profile, row):
            counts[problem] = counts.get(problem, 0) + 1
    derived_problem, derived_note = _question_set_from_the_ids(rows, profile)
    notes = [note for note in (derived_note, _pinned_routing_note(rows)) if note]
    if not counts and derived_problem is None:
        return [f"config {profile.name}: every row agrees with the profile in configs.toml", *notes]
    total = sum(counts.values())
    # The derived finding is about the artifact. The header must not say zero rows contradict it.
    if counts:
        lines = [f"config {profile.name}: {total} row(s) contradict configs.toml"]
    else:
        lines = [f"config {profile.name}: contradicts configs.toml"]
    lines += [
        f"  {problem}   ({n} rows)"
        for problem, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    ]
    if derived_problem is not None:
        lines.append(derived_problem)
    return lines + notes


def _pinned_routing_note(rows: Sequence[Mapping[str, Any]]) -> str | None:
    """Turns that replayed another shortlist. The profile does not claim the config routed itself."""
    pinned = sum(1 for row in rows if row.get("routing_pinned"))
    if not pinned:
        return None
    return (
        f"  {pinned} of {len(rows)} turns replayed a pinned shortlist (`routing_pinned`), so "
        "routing on those turns is the config's it was pinned from, not this one's"
    )
