"""What identifies one measurement run, and whether an artifact on disk shares that identity.

Two halves of one question, kept together because they have to agree: the driver *stamps*
the harness-side identity onto every row here, and *reads it back* here when ``--resume``
opens an artifact somebody else's run may have written.

**The incident.** ``--resume`` decided whether to keep an artifact from the artifact's
*filename*. The tag carries ``--model``, ``--effort``, ``--top-n``, ``--embed``, the provider
and ``--prompt-variant``; it carries no corpus, no dataset and no worker count, and an
explicit ``--out`` bypasses it entirely. So ``git pull`` in ``../BIRD-corpus``, resume, and one
artifact holds two corpora — with every quotability gate passing and the driver printing that
the numbers are quotable as a single arm. The corpus is the treatment identity (AGENTS.md),
which makes that the worst sentence the driver can print.

Both treatment hashes were already on every row. The half that was missing is a reader, and
the half that is added here is the rest of the identity: the harness commit, the worker count,
and which questions and schemas were served.

**Not a knob home.** ``register/knobs.py`` owns every knob's *declaration* and default; this
module only resolves the ones whose value is a fact about the running process — a git sha
cannot have a register default. Every name written here is declared there, and
``tools/check_declared_is_consumed.py`` rule K2 fails the build if one is not.
"""

from __future__ import annotations

import hashlib
import subprocess
from collections.abc import Collection, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from ..register.arm_profiles import reconcile

__all__ = [
    "append_refusal",
    "arm_startup_refusal",
    "flag_conflict",
    "git_provenance",
    "harness_knobs",
    "reconciliation_lines",
    "resume_identity_problem",
    "scope_identity",
    "short_digest",
    "truncation_notice",
]

#: How many hex characters of a SHA-256 a scope digest keeps. Twelve is 48 bits, which is far
#: more than the collision resistance a *drift* check needs (the populations being compared are
#: two attempts at one run, not an adversarial pair) and short enough that the value costs a
#: few dozen bytes on each of 1 351 rows.
_DIGEST_CHARS = 12


def short_digest(parts: Iterable[str]) -> str:
    """A stable, order-independent digest of a set of strings.

    Sorted before hashing: the caller's iteration order is an accident of a directory scan or
    a dict, and a digest that moved with it would report drift on every run.
    """
    joined = "\n".join(sorted(str(p) for p in parts))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:_DIGEST_CHARS]


def _git(repo: Path, *args: str) -> str | None:
    """``git <args>`` in ``repo``, or ``None`` if it cannot be answered.

    Never raises: a run must not die because the harness is a tarball rather than a checkout,
    and a missing answer is recorded as ``None`` — which reads as "unmeasured" rather than as
    a value two rows can agree on.

    **``encoding="utf-8", errors="replace"`` rather than ``text=True``, and it is not a style
    preference.** ``text=True`` decodes with the *locale* codec, which on Windows is cp1252, and
    cp1252 has no mapping for five bytes UTF-8 uses routinely. ``git diff HEAD`` over a working
    tree containing one edited non-ASCII file — this repository has Chinese documents in
    ``docs/analysis/`` — therefore killed the reader thread, left ``proc.stdout`` as ``None``, and
    made this function raise ``AttributeError`` on the next line. Found 2026-08-12 by editing one
    of those documents: four tests went red and, on a real run, ``harness_knobs()`` would have
    died before the first paid question. ``errors="replace"`` because the caller digests this
    string rather than reading it, so a mangled character is a fact about the diff and a crash is
    the loss of a run. With ``errors="replace"`` the reader thread cannot die, so the ``or ""``
    guard below is redundant now and still worth not needing.
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
    """The four resume-drift keys, resolved from the working tree.

    All four are ``Role.operational``: two runs at different commits are the *normal*
    comparison, and the same difference inside one artifact is corrupting.
    ``measure/gates.py::_knobs_resolved_gate`` looks for disagreement across an arm's rows —
    and until this existed all four were ``None`` on every row of every arm, so the gate that
    exists to stop a resume blending two harness versions into one score was comparing four
    constants against themselves. That is exactly what ``diff_sha256``'s own register note says
    the absence would cost.

    ``git_main_sha`` is asked of ``main`` and then ``origin/main``: on the experiment server the
    code sits on a branch never equal to main, so the branch tip alone cannot say which main
    commit a paid run was based on.

    ``diff_sha256`` digests ``git diff HEAD``. Checking ``git_sha`` alone lets a resume across an
    uncommitted edit blend two harness versions with no gate firing, which is the incident the
    knob was declared for.
    """
    head = _git(repo, "rev-parse", "HEAD")
    main = _git(repo, "rev-parse", "main") or _git(repo, "rev-parse", "origin/main")
    diff = _git(repo, "diff", "HEAD")
    # `_git` returns None for an empty stdout, so "clean tree" and "git could not answer" arrive
    # as the same value and must not be reported as the same fact. `git status --porcelain` is
    # asked separately rather than inferred from `diff`, because it also sees untracked files.
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
    """The three ``Role.scope`` knobs: what was served, from which file, to which questions.

    Scope keys are *fatal on resume* by declaration and had no writer at all, so an artifact
    could not say which schemas were in the router or which questions were asked.

    **Counts and digests, not lists.** ``schemas_under_test``'s register note describes the
    incident it exists to prevent — "a schema dropped from one attempt leaves its YAML behind
    and competes as a router candidate for every other schema's questions" — which is drift
    *between attempts at one run*, and a digest detects that exactly. Carrying the 57 names
    verbatim would add roughly 740 bytes to each of a 1 351-row artifact's 6.4 KB rows, about
    12%, to make recoverable something ``corpus_content_hash`` already pins. ``question_subset``
    is the same shape, and its note is explicit that "a probe set's identity is not its count",
    so the count alone would not do.

    ``split`` is the dataset **file stem**, not the word "train" or "test". The ids in
    ``test_final.jsonl`` are BIRD ``train_*`` ids re-split by that repository's
    ``build_eval_dataset.py``, so answering "train or test" from the ids would be a claim about
    another repository's intent; the file name is a fact.
    """
    return {
        "schemas_under_test": f"{len(schemas)}:{short_digest(schemas)}",
        "split": dataset_file.stem,
        "question_subset": f"{len(question_ids)}:{short_digest(question_ids)}",
    }


def harness_knobs(
    *,
    repo: Path,
    schemas: Collection[str],
    question_ids: Collection[str],
    dataset_file: Path,
    serve_workers: int,
) -> dict[str, Any]:
    """Every knob whose value is a fact about *this process* rather than about the corpus.

    ``serve_workers`` is here and ``build_workers`` deliberately is not: this driver serves, it
    does not build a corpus, and writing a number for a stage that did not run would be the
    ``embedding_provider`` defect (a null reads as unmeasured, a value reads as a measurement).
    Its note is about a worker that "holds a connection AND a long-lived agent conversation",
    which is the curator, and the curator is not in this repository.
    """
    return {
        **git_provenance(repo),
        "serve_workers": int(serve_workers),
        **scope_identity(
            schemas=schemas, question_ids=question_ids, dataset_file=dataset_file
        ),
    }


# --------------------------------------------------------------------------- #
# Reading the identity back
# --------------------------------------------------------------------------- #


def flag_conflict(*, resume: bool, truncate: bool) -> str | None:
    """Why this pair of flags cannot both be honoured, or ``None``.

    ``--resume`` keeps what was measured; ``--truncate`` throws it away. The file is the same
    one, so the two are opposite instructions and resolving them in either direction guesses at
    hours of paid model calls.

    A pure function and not three lines of ``argparse`` glue, for a reason the hung test run
    that produced it makes concrete: driving ``main`` to check this guard means driving it past
    credential resolution, model construction and a database connection, so a mutation that
    *removes* the guard does not fail the test — it hangs it, or worse, gets far enough to spend
    something. The decision is the thing worth testing, so the decision is what a test can
    reach.
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
    """Why this artifact must not be opened for appending, or ``None``.

    **Appending to an existing artifact without ``--resume`` is not a resume, it is two
    populations in one file.** The driver opened the output in ``"a"`` mode unconditionally, so
    a re-run duplicated every question, ``_report`` printed EX over the doubled population, and
    only *afterwards* did ``Population.of`` raise on the repeated ids — the number was printed
    before the check that would have withdrawn it.

    ``truncate`` is the caller having said, in its own flag, that discarding the rows on disk is
    what it meant. It is **not** ``--force-fresh``: see :func:`truncation_notice`.

    A pure function rather than three lines inside ``main`` because ``main`` needs a corpus, a
    dataset, a database and a model before it reaches them, so a branch left there is a branch
    no test can run.
    """
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
    """What is about to be destroyed, or ``None`` if nothing is. Non-empty means *do it*.

    **The destructive branch, extracted so it has a name, a test and a sentence on stdout.**
    ``--force-fresh`` used to be non-destructive: it relaxed the *sibling-artifact* abort, a
    path on which the output file does not exist. It then quietly acquired
    ``out_path.write_text("")`` as well, so a flag whose documented job was "yes, I know there
    are other artifacts, start anyway" began deleting a completed run. On this dataset an arm is
    hours of paid model calls, and the irony is recorded three functions up:
    :func:`append_refusal` exists because "a branch left there is a branch no test can run", and
    the destructive half stayed in ``main`` where no test could reach it.

    So the two meanings are two flags. ``--force-fresh`` relaxes the sibling abort and touches
    nothing on disk; ``--truncate`` is the only thing in this repository that discards a
    measured artifact, and it says how many rows it is discarding before it does.

    Refuse-by-default lives in :func:`append_refusal`: without ``truncate`` an existing artifact
    stops the run. This function only answers "and now that the caller has asked for it, what
    exactly goes".
    """
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
    """A turn that ended before anything stamped the treatment onto it.

    Every row in the 2026-08-09 artifacts whose ``corpus_content_hash`` is ``None`` is a
    zero-licensed turn that ended in a clarifying question — 6 of 6 in v3-fold, 8 of 8 in
    v3-pinned, 4 of 4 in v4, 5 of 5 in v5, 13 of 13 in v4-reflect. ``None`` there does not mean
    "written before the field existed"; it means the field has a path it is not written on.

    That matters here because a guard that warns on **every** legitimate resume is the shape
    that teaches a reader to ignore a warning. These rows are counted and reported as a number,
    not as a caution.
    """
    return str(row.get("outcome")) == "clarification" and not (row.get("licensed") or ())


def _identity_problem(
    rows: Sequence[Mapping[str, Any]], field: str, want: Any
) -> tuple[str | None, str | None, int]:
    """``(refusal, warning, n_pre_routing)`` for one treatment-identity field."""
    foreign: dict[Any, int] = {}
    unstamped = 0
    pre_routing = 0
    for row in rows:
        value = row.get(field)
        if value is None:
            if _is_pre_routing_abstention(row):
                pre_routing += 1
            else:
                unstamped += 1
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
    if unstamped:
        warning = (
            f"{unstamped} resumed row(s) carry no {field} and did not abstain before routing; "
            "they predate the field and cannot prove they are the same treatment"
        )
    return refusal, warning, pre_routing


def _knob_problem(
    rows: Sequence[Mapping[str, Any]],
    *,
    knobs_resolved: Mapping[str, Any],
    comparability: Collection[str],
) -> tuple[list[str], list[str]]:
    """Where the artifact's recorded configuration disagrees with this run's.

    **Why the two treatment hashes are not enough.** The filename tag is the only thing that
    separated a ``--top-n 3`` artifact from a ``--top-n 8`` one, and ``--out`` bypasses the tag.
    Neither ``corpus_content_hash`` nor ``prompt_set_hash`` moves with ``--top-n``, ``--embed``,
    ``--reflect`` or the model id, so a resume under a renamed output file could merge two
    treatments with both hashes agreeing. These are the same keys
    ``eval/report.py::knobs_comparable`` compares across two arms; here they are compared
    across one artifact and the run about to extend it.

    ``repr`` rather than ``==``, for the reason the within-arm gate gives: ``3`` and ``"3"`` are
    two configurations, and a comparison that coerced them would report drift as agreement.

    A key absent from every row **and** from this run is skipped — both sides declined to say.
    No live run is in that state: ``session._resolved_knobs`` omits no key, so this run always
    records all 47. Present on one side only is a **warning**: the likeliest cause is an
    artifact older than the knob, and refusing on it would strand every artifact on disk. That
    is the branch every artifact on disk hits — six comparability knobs are absent from all
    seven ``proxy_*`` artifacts in ``runs/eval/`` (``abstention_policy_enabled``,
    ``access_grant``, ``cost_budget``, ``negative_tau``, ``semantic_scale_ceiling``,
    ``sqlglot_version``) and recorded by any run that would resume one.

    "Absent" and "recorded ``None``" stay apart throughout, which is why membership is tested
    rather than ``.get()``: ``_resolved_knobs`` flattens ``UNSET`` to ``None`` on purpose, so a
    recorded ``None`` is a value two runs may agree on while a missing key is a run declining
    to say.
    """
    refusals: list[str] = []
    warnings: list[str] = []
    for key in sorted(comparability):
        seen: set[str] = set()
        absent = 0
        for row in rows:
            recorded = row.get("knobs_resolved")
            if not isinstance(recorded, Mapping) or key not in recorded:
                absent += 1
                continue
            seen.add(repr(recorded[key]))
        declared = key in knobs_resolved
        if not seen and not declared:
            continue
        if not seen or not declared:
            warnings.append(
                f"comparability knob {key!r} is recorded on "
                f"{'the artifact' if seen else 'this run'} and not on "
                f"{'this run' if seen else 'the artifact'}, so the two cannot be shown to "
                "share it"
            )
            continue
        want = knobs_resolved[key]
        if absent:
            warnings.append(
                f"{absent} resumed row(s) carry no {key!r}, so they cannot be shown to have "
                "run under this run's value"
            )
        if seen != {repr(want)}:
            refusals.append(
                f"  the artifact ran under a different {key}:\n"
                f"    this run: {want!r}\n"
                f"    on disk : {', '.join(sorted(seen))}"
            )
    return refusals, warnings


def resume_identity_problem(
    rows: Sequence[Mapping[str, Any]],
    *,
    corpus_content_hash: str | None,
    prompt_set_hash: str | None,
    knobs_resolved: Mapping[str, Any],
    comparability: Collection[str],
    question_ids: Collection[str],
    replay_routing: bool,
) -> tuple[str, list[str]]:
    """``(refusal, warnings)`` — is the artifact on disk the same treatment as this run?

    Pure, so it can be driven from a list of dicts; ``main`` owns only the printing and the
    exit code.

    ``rows`` are the rows the resume intends to **keep** (crashed rows are dropped by the
    caller before this is asked, because a crashed row is not a measurement).

    **What this does not catch**, stated rather than implied:

    * a dataset whose gold statements were edited while the question ids stayed the same. The
      row carries no dataset identity beyond ``split`` and ``question_subset``, and
      ``gold_fingerprint`` is attached after the resume decision. A dataset with a *different
      question set* is caught, by ``question_subset`` and by the ids themselves.
    * an *unpinned* run resumed into a *pinned* artifact when every kept row happens to be a
      turn that never routed. The reliable direction is asserted below; the other one would
      need the driver's intent on the row, and ``routing_pinned`` is deliberately an outcome
      (open-work §3.7) rather than an intent.
    """
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

    knob_refusals, knob_warnings = _knob_problem(
        rows, knobs_resolved=knobs_resolved, comparability=comparability
    )
    problems.extend(knob_refusals)
    warnings.extend(knob_warnings)

    # Ids the artifact has and this run does not. Either --dataset changed or the scope narrowed
    # (--limit / --per-schema). Both mean the two are not one population; the driver cannot tell
    # them apart from here, so it names both rather than guessing.
    resumed = {str(row.get("question_id")) for row in rows}
    stale = sorted(resumed - set(map(str, question_ids)))
    if stale:
        problems.append(
            f"  {len(stale)} row(s) name questions this run does not cover, so the artifact and "
            f"this run are not one population. Either --dataset changed, or --limit/--per-schema "
            f"narrowed the scope. Example question id: {stale[0]}"
        )

    # One direction only, and it is the sound one: under the corrected semantics of
    # `routing_pinned` (the turn's shortlist *is* the pinned one), only a run that passed
    # `--replay-routing` can produce a `True`. The converse — a pinned run resuming an unpinned
    # artifact — cannot be read off the rows, because a pinned run whose kept rows all abstained
    # before routing also carries no `True`.
    if not replay_routing:
        pinned_rows = sum(1 for row in rows if row.get("routing_pinned") is True)
        if pinned_rows:
            problems.append(
                f"  {pinned_rows} row(s) had their routing replayed from another artifact and "
                "this run routes for itself. --replay-routing is part of the treatment. The "
                "driver's default filename now carries a _pinned segment, so the two arms "
                "normally land in different files -- but --out names the file directly and "
                "bypasses the tag, which is the case this check still has to catch."
            )

    if problems:
        problems.append(
            "  Two treatments in one artifact is not an arm. Rename the artifact and start a "
            "new one, or restore the treatment it was measured under."
        )
    return "\n".join(problems), warnings


# --------------------------------------------------------------------------- #
# Reading the *arm profile* back
# --------------------------------------------------------------------------- #


def arm_startup_refusal(profile: Any, session_identity: Mapping[str, Any]) -> str | None:
    """Why ``--arm NAME`` must not run against this session, or ``None``.

    Asked **before the first paid question**, which is the only place the check is worth
    anything: a run labelled ``v4`` against a corpus that is not v4's is a mislabelled artifact,
    and mislabelled artifacts are how a number is quoted against the wrong treatment.

    ``session_identity`` is anything shaped like a measurement row — the driver passes
    ``{"corpus_content_hash": session.corpus_content_hash}``, which is why ``reconcile`` was
    written to read a row rather than a session.

    Extracted from ``main`` for the reason :func:`append_refusal` was: the driver reaches this
    point only after a corpus, a dataset, a database and four models are built, so the branch
    was unreachable from a test. It was also the wire that made ``reconcile`` non-vacuous, and
    an untested wire is exactly what let a vacuous ``reconcile`` through in the first place.
    """
    mislabelled = reconcile(profile, session_identity)
    if not mislabelled:
        return None
    lines = [f"--arm {profile.name} does not match this corpus:"]
    lines += [f"  {problem}" for problem in mislabelled]
    lines.append(
        "  Fix arms.toml or point --corpus-dir at the corpus the profile names. A run labelled "
        "with an arm it did not measure is worse than an unlabelled one."
    )
    return "\n".join(lines)


def reconciliation_lines(rows: Sequence[Mapping[str, Any]], profile: Any) -> list[str]:
    """Every row against the arm profile's committed claim, as report lines.

    The startup check reads the *session*; this reads the artifact, which is what a resume can
    make differ from it. One line per distinct disagreement with a count — 1 351 copies of the
    same sentence is not a finding, it is a wall.

    Returns the agreement line only when there was something to agree about. ``reconcile``
    refuses an unreconcilable profile outright, so "every row agrees" can no longer be printed
    by a check that compared nothing.
    """
    counts: dict[str, int] = {}
    for row in rows:
        for problem in reconcile(profile, row):
            counts[problem] = counts.get(problem, 0) + 1
    if not counts:
        return [f"arm {profile.name}: every row agrees with the profile in arms.toml"]
    total = sum(counts.values())
    lines = [f"arm {profile.name}: {total} row(s) contradict arms.toml"]
    lines += [
        f"  {problem}   ({n} rows)"
        for problem, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    ]
    return lines
