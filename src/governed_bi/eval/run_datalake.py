"""Pooled **data-lake** eval driver (D15 scale run).

The only eval driver (`run_datalake`). The retired single-schema driver
(``run_experiment``, removed 2026-07-31, M3 N9) was subsumed: single-schema eval
is this driver at ``n=1`` via ``--dbs <db>``. This driver serves a whole BIRD split
with **every** schema living in one database at once, so the schema router
(``analyst.agent`` + ``retrieval.schema_router``) must pick the right schema per
question when more than one schema is in the pool. It is the "one database, many
schemas" experiment (docs/design-decisions.md D15).

``--split test`` (default) is the held-out score. ``--split train`` is larger but
is what the curator was built from, so it is a diagnostic only. Rows stream to
disk as they are scored, so ``--resume-from <run dir>`` continues an interrupted
run; ``governed_bi.eval.analysis`` reports table-selection attribution, paired
McNemar and gradeable EX over the result with no further model calls.

Shape (mirrors the eval ladder — three fair rungs, same serve path):

1. **Build** ``baseline`` / ``curated`` / ``curated_sme`` for N ``db_id``s into
   three *shared* corpus roots (each db writes its own ``<root>/<db_id>/``
   subtree). Per-db curator sidecars are relocated so a shared root does not
   clobber them. Resumable: a db whose subtree has the durable
   ``BUILD_COMPLETE.json`` marker is skipped; partial YAML without that marker
   is discarded and rebuilt.
2. **Pool** the split's questions (tagged with their ``db_id``), the gold hashes
   (keyed by globally-unique ``question_id``), and a **per-db** suspect-column
   set (the decoy metric is bare-column-name, so pooling suspect sets would
   cross-contaminate — each db's questions are scored against that db's set).
3. **Serve** every arm through ONE unpinned connector (``schema=None`` → the
   engine emits fully schema-qualified ``schema.table`` and the router routes),
   with an embedder for BM25+embedding RRF and (default on here) a single-schema
   LLM pick. Score EX against the pooled gold, and — separately — the routing
   recall (did the router keep the true schema?) so mis-routing is visible.

Run (subset first — this is the heaviest run in the project)::

    uv run python -m governed_bi.eval.run_datalake \\
      --bird-dir ../BIRD-Data-Obfuscation \\
      --pg-dsn "$GOVERNED_BI_PG_DSN" \\
      --limit-dbs 5 --out runs/datalake/

The gold self-check runs against a schema-*pinned* gateway per sampled db (gold
``sql_rename`` is schema-unqualified, so it needs ``search_path``); serve uses
the unpinned gateway. Cross-check EX (which re-executes gold SQL) is therefore
skipped in this mode.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import threading
import time
import traceback
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, NamedTuple

from ..config import (
    DataSourceConfig,
    Environment,
    NoteGovernance,
    Settings,
    load_dotenv,
    load_settings,
)
from ..corpus import Corpus, load_corpus
from ..corpus.schemas import NoteAsset
from ..gateway import Gateway, Identity
from ..gateway.connectors.postgres import PostgresConnector
from ..prompts import (
    parse_cli_overrides,
)
from ..prompts import (
    resolve as resolve_prompts,
)
from ..prompts import (
    text as prompt_text,
)
from ..stages import (
    INFRA_ERROR_PREFIX,
    Outcome,
    Stage,
    classify_outcome,
    classify_row,
)
from . import metrics
from .analysis import (
    analyse_run,
    census_delta,
    corpus_census,
    write_questions_sidecar,
)
from .arms import (
    ARM_ORDER,
    _touches_suspect,
    agent_solver,
    ladder_steps,
)
from .bird_loader import (
    available_dbs,
    description_dir,
    load_bird_items,
    load_rename_map,
)
from .harness import (
    _collect_curator_errors,
    _RefuseAllSolver,
    _sme_fold_signal,
    _suspect_from_corpus,
    _utc_ts,
    _validate_corpora,
    _warn_if_not_green,
    _warn_if_sme_noop,
    _write_jsonl,
)
from .hash_grade import (
    load_gold_hashes,
    load_trap_columns,
    score_sql_hashes,
    validate_gold_hashes_live,
)
from .index import RESUME_DRIFT_KEYS, index_run
from .leakage import twin_report, ungradeable_question_ids
from .oracle import GoldIndex, OracleRung, oracle_solver
from .parallel import ServeWorker, resolve_workers, run_ordered_pool
from .sql_diff import is_frozen_constant

# The five the driver still calls, plus eleven it does not: those carry
# ``noqa: F401`` because they are re-exports, kept so tests that import them from
# here keep working. Both groups have the same deadline as the alias block below.
from .statistics import (
    PRICE_VERDICT_TAGS,  # noqa: F401
    _bool_rate,  # noqa: F401
    _ex_by_stamp,  # noqa: F401
    _guardrail_ceiling,  # noqa: F401
    _mean,  # noqa: F401
    _positive,  # noqa: F401
    _rate_over,  # noqa: F401
    _split,  # noqa: F401
    _sum_counters,  # noqa: F401
    _twin_stamps_complete,  # noqa: F401
    compare_arms,
    fmt_rate,
    ladder_deltas,
    price_verdict,  # noqa: F401
    routing_escaped,
    summarise_rows,
)
from .treatment import divergence_table

# ---------------------------------------------------------------------------
# Migration aliases (M4b N19) — DELETE AFTER ONE RELEASE.
#
# The statistics cluster moved to ``eval/statistics.py``. It was never actually
# private: 19 test modules reached into this driver by underscore name, 181
# references in all. Rewriting every one of them in the same commit that moves
# the code would bury the move in an unreviewable diff, so the old names forward
# from here and the callers migrate in batches.
#
# **Delete these the release after the move lands.** The signal that it is safe:
#
#     grep -rn '_summarise_rows\|_compare_arms\|_routing_escaped\|_fmt_rate' src tests
#
# returns only this block. Delete the re-export list above at the same time and
# for the same reason.
#
# This is not the guardrail decision 12 forbids. That decision is about gates
# that catch an operator slip at run time; this is a time-boxed internal rename
# shim, the same distinction item 4.1 already settled.
_summarise_rows = summarise_rows
_compare_arms = compare_arms
_routing_escaped = routing_escaped
_fmt_rate = fmt_rate
# ---------------------------------------------------------------------------

# Derived from the enum, not spelled again: two independent spellings of the same
# taxonomy drift, and this driver both validates ``--arms`` against it and uses it
# as the default. Those were two names until 2026-07-28, when removing the opt-in
# ``curated_sme_blind`` rung made the default equal to the whole ladder.
_ARMS = ARM_ORDER
_SPLITS = ("test", "train")
#: Share of schemas whose gold must fail to execute before the run aborts rather than
#: warns. Set where it is, not tuned: misconfiguration (wrong DSN, unloaded schemas,
#: gold read from the un-obfuscated ``sql_sqlite``) takes out essentially every schema,
#: while a query crossing the gateway timeout or a gold row BIRD never flagged as broken
#: takes out one. A quarter of the split is far above the latter and far below the
#: former. Below it the failures are reported and recorded, never swallowed.
#: Share of requested schemas that must build before the run is allowed to serve.
#: Half is deliberately lenient — a handful of awkward schemas should not throw away a
#: scale run — but a pool that has lost most of its members is a different experiment
#: from the one requested, and it is unquotable regardless, so serving it only spends
#: money. See the check after the build phase.
_BUILD_COVERAGE_ABORT_FRACTION = 0.5
_GOLD_EXEC_FAILURE_ABORT_FRACTION = 0.25
#: ...and at least this many schemas, so the share cannot fire on a single failure in a
#: small pool. Without it, one slow gold row aborted every ``--limit-dbs 3`` run.
_GOLD_EXEC_FAILURE_ABORT_MIN_DBS = 2
#: Share of the requested pool that may be withheld for a recorded curator error before
#: the run aborts rather than serving what is left.
#:
#: The incident: a paid 55-schema run hit the curator's recursion limit on 13 schemas.
#: ``_invoke_agent`` records that in the per-db ``run_manifest.json`` and lets the build
#: finish, so 13 partially-authored corpora were served, scored, and ranked by the
#: pooled router as if they were complete — and the only consequence was that
#: ``quotable()`` disqualified the whole run, which cannot say "the other 42 are fine".
#:
#: A quarter, and measured against ``wanted`` rather than against what built, so it
#: shares the gold guard's denominator: two shares of the same requested pool can be
#: read side by side, and quarantining after a lossy build does not get a flattering
#: smaller denominator. 13/55 is 24%, just under — which is the intent. That run should
#: withhold 13 schemas and still report the 42, not throw away a paid build; a run where
#: a third of the pool came back partial is a broken curator configuration, and serving
#: it only spends the serve budget on a benchmark far smaller than the one it names.
_CURATOR_ERROR_QUARANTINE_ABORT_FRACTION = 0.25
#: ...and at least this many, for the reason its gold twin has one: on the runbook's
#: ``--limit-dbs 3`` smoke a single quarantine is 33% and no evidence of anything
#: systematic. The "nothing survived" case is caught unconditionally instead.
_CURATOR_ERROR_QUARANTINE_ABORT_MIN_DBS = 2
# A gold answer that is a literal ``VALUES (...)`` constant hands back a precomputed
# row instead of querying anything, so no generated SQL can match it. These are
# counted out of ``ex_gradeable`` and reported, rather than silently deflating EX
# and diluting every arm-to-arm delta measured against it. Detection lives in
# ``sql_diff.is_frozen_constant`` (shared with analysis / leakage).
# Curator sidecar files written to the corpus *root* (not the per-schema subtree):
# on a shared root each db would overwrite the last. Relocated per-db after build.
#: Written into ``<db>/_build/`` when a curator diagnostic could not be promoted.
#: Its presence means this db's curator verdict is unknown, which the run ledger
#: must treat as a reason not to quote the run.
_UNPROMOTED_MARKER = "UNPROMOTED_SIDECARS.json"
#: Written into ``<db>/_build/`` only after a successful per-(arm, db) build.
#: Resume, staging seed, skip, and promote treat this — not "any ``*.yaml``" — as
#: the durable completeness contract. A kill mid-build leaves YAML without this
#: marker; that tree is debris, not a finished corpus.
_BUILD_COMPLETE_MARKER = "BUILD_COMPLETE.json"

#: Per-(arm, db) build artifacts promoted out of the staging root into
#: ``<db>/_build/``. Anything NOT listed here is deleted with the staging tree, so a
#: new curator artifact has to be added here or it is written and then thrown away.
_SIDECARS = (
    "run_manifest.json",
    "validate_findings.jsonl",
    "adversary_findings.jsonl",
    "sme_clarifications.jsonl",
    "clarifications.jsonl",
    # The per-tool-call traces. The manifest keeps the derived counters, but only
    # these carry the ordered calls and their argument digests — the sole record of
    # *what* an agent that exhausted its budget was doing. Dropping them would
    # reproduce, one layer down, the 2026-07-29 gap they were added to close.
    "curator_trace.jsonl",
    "curator_sme_trace.jsonl",
)


class _ServeProgress:
    """Driver-side serve progress + ETA (N11). Lives here, not in ``eval.parallel``.

    ``on_result`` already fires per completed question; this only prints. Call
    :meth:`tick` from the driver's persist callback / serial loop.
    """

    def __init__(self, *, arm: str, total: int, every: int | None = None) -> None:
        self.arm = arm
        self.total = max(0, total)
        self.done = 0
        self._t0 = time.perf_counter()
        # Small runs: every question. Large runs: about 20 lines max.
        self.every = every if every is not None else (
            1 if self.total <= 20 else max(1, self.total // 20)
        )

    def tick(self) -> None:
        self.done += 1
        if self.done != self.total and self.done % self.every != 0:
            return
        elapsed = time.perf_counter() - self._t0
        if self.done > 0 and elapsed > 0 and self.done < self.total:
            eta_s = (self.total - self.done) * (elapsed / self.done)
            eta = f", eta {eta_s:.0f}s"
        else:
            eta = ""
        print(
            f"  serve [{self.arm}]: {self.done}/{self.total} "
            f"({100.0 * self.done / self.total if self.total else 100.0:.0f}%)"
            f" in {elapsed:.0f}s{eta}"
        )


def _has_yaml(root: Path, db_id: str) -> bool:
    d = root / db_id
    return d.is_dir() and any(d.rglob("*.yaml"))


def _build_complete_path(root: Path, db_id: str) -> Path:
    return root / db_id / "_build" / _BUILD_COMPLETE_MARKER


def _corpus_complete(root: Path, db_id: str) -> bool:
    """True only when a successful build left the durable completeness marker.

    ``*.yaml`` alone is not enough: a kill mid-build leaves partial YAML that used
    to be adopted as finished on ``--resume``.
    """
    return _build_complete_path(root, db_id).is_file() and _has_yaml(root, db_id)


def _mark_build_complete(root: Path, db_id: str) -> None:
    """Record that ``root/db_id`` finished successfully. Call only after a full build."""
    dest = root / db_id / "_build"
    dest.mkdir(parents=True, exist_ok=True)
    payload = {
        "db_id": db_id,
        "complete": True,
        "marked_at_utc": _utc_ts(),
    }
    _build_complete_path(root, db_id).write_text(
        json.dumps(payload, indent=2) + "\n", encoding="utf-8"
    )


def _discard_incomplete_corpus(root: Path, db_id: str) -> bool:
    """Remove a shared-root ``(arm, db)`` tree that has YAML but no completeness marker.

    Returns whether anything was discarded. Resume must not seed, skip, or promote
    such debris; rebuilding over it would also leave orphan YAML from the killed
    attempt mixed into the new tree.
    """
    d = root / db_id
    if not d.is_dir():
        return False
    if _corpus_complete(root, db_id):
        return False
    if not _has_yaml(root, db_id) and not _build_complete_path(root, db_id).exists():
        # Empty or sidecar-only debris without YAML — still unsafe to keep if the
        # directory exists from a killed attempt; clear when anything is present.
        try:
            next(d.iterdir())
        except StopIteration:
            return False
    shutil.rmtree(d)
    return True


def _relocate_sidecars(
    root: Path, db_id: str, *, dest_root: Path | None = None
) -> list[Path]:
    """Move root-level curator sidecars into ``<dest_root>/<db_id>/_build/``.

    Returns the sources it could NOT place, so a caller that is about to delete
    ``root`` can tell the difference between "everything is promoted" and "a
    diagnostic is still sitting in a directory I am about to remove".

    ``dest_root`` defaults to ``root`` (the in-place serial case). When a build runs
    in a private staging root, the sidecars are lifted straight into the shared arm
    root's per-db folder instead, so the staging directory can be discarded whole.
    """
    dest = (dest_root or root) / db_id / "_build"
    # The named sidecars. The curator used to also drop an
    # ``agent_checkpoints_<schema>.sqlite`` here, which needed its own exemption
    # because losing it was harmless; the curator no longer creates one (deepagents
    # wants a checkpointer only for ``interrupt_on``, which the curator does not use),
    # so every file handled here is now a diagnostic whose loss matters.
    movable = [root / name for name in _SIDECARS]
    stuck: list[Path] = []
    for src in movable:
        if not src.exists():
            continue
        dest.mkdir(parents=True, exist_ok=True)
        try:
            src.replace(dest / src.name)
            continue
        except OSError:
            pass
        # A move can fail where a copy succeeds: on Windows an open handle blocks
        # renaming the file but not reading it. Copying keeps the diagnostic even
        # when the original cannot be released.
        try:
            shutil.copy2(src, dest / src.name)
        except OSError as err:
            stuck.append(src)
            print(
                f"*** WARNING: could not place {src.name} for {db_id!r}: {err} ***"
            )
            continue
        # The copy landed, so the original is now redundant AND dangerous: left at
        # the arm root it is the next db's build that overwrites it, and in serial
        # mode nobody deletes the arm root. Best effort — the copy is what matters.
        try:
            src.unlink()
        except OSError:
            pass
    lost = sorted(p.name for p in stuck)
    if lost:
        # A diagnostic that could not be placed means this db's curator verdict is
        # UNKNOWN, and that has to survive as a fact about the run rather than as a
        # file somewhere. Two earlier attempts tried to preserve the file itself and
        # both failed: the staging root that was "kept for inspection" is cleared at
        # the start of the next build (so a `--resume` erased it and then re-promoted
        # cleanly, scoring the db as healthy), and in serial mode there is no staging
        # root at all — the file simply sits at the arm root until the next db
        # overwrites it.
        #
        # So the marker goes where the *promoted* artifacts go, in both modes. It is
        # read by `_collect_curator_errors`, lands in `summary.json`'s
        # `curator_errors`, and makes the run un-quotable through the gate that
        # already exists — because a missing `run_manifest.json` otherwise reads
        # downstream as "no curator error", which is the swallowed-crash failure this
        # whole harness is built to stop.
        dest.mkdir(parents=True, exist_ok=True)
        (dest / _UNPROMOTED_MARKER).write_text(
            json.dumps(
                {
                    "db_id": db_id,
                    "unpromoted": lost,
                    "why": (
                        "these curator diagnostics could not be moved or copied out of "
                        "the build root, so this db's curator verdict is unknown and "
                        "the run must not be quoted on it"
                    ),
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        print(
            f"*** WARNING: could not promote {', '.join(lost)} for {db_id!r} — "
            f"recorded in {dest / _UNPROMOTED_MARKER}; this db's curator verdict is "
            "unknown and the run will not be quotable ***"
        )
    return stuck


def _stage_roots(
    staging_root: Path,
    roots: dict[str, Path],
    db_id: str,
    *,
    resume: bool,
) -> dict[str, Path]:
    """Private per-``(arm, db)`` staging roots for a concurrent build.

    Extracted from ``run_datalake``'s build worker so the two things that decide
    whether a parallel build means the same as a serial one can be tested without
    driving the whole harness: what a staging root starts out holding, and whether a
    resume can still see what is already on disk.

    Roots are **cleared**, not just created. A build killed by the process dying
    (OOM, Ctrl-C) leaves partial YAML behind; staging is never trustworthy, so it
    is wiped at the start of every attempt.

    Under ``resume`` each staging root is then seeded only with a *complete*
    shared-root corpus for this db (``BUILD_COMPLETE.json`` present). Partial
    shared trees are discarded rather than copied: seeding them would let the
    build's skip check (and a later promote) adopt kill debris as finished.
    """
    staged = {arm: staging_root / f"{arm}__{db_id}" for arm in roots}
    for path in staged.values():
        shutil.rmtree(path, ignore_errors=True)
        path.mkdir(parents=True, exist_ok=True)
    if resume:
        for arm, path in staged.items():
            if _corpus_complete(roots[arm], db_id):
                shutil.copytree(roots[arm] / db_id, path / db_id, dirs_exist_ok=True)
            elif _discard_incomplete_corpus(roots[arm], db_id):
                print(
                    f"  [{arm}] discarded incomplete {db_id!r} corpus under "
                    f"{roots[arm].name} (YAML without {_BUILD_COMPLETE_MARKER}); "
                    "will rebuild"
                )
    return staged


def run_build_phase(
    wanted: "Sequence[str]",
    *,
    roots: dict[str, Path],
    staging_root: Path,
    build_workers: int,
    resume: bool,
    build_errors: dict[str, str],
    build_lock: "threading.Lock",
    build_one_db: "Callable[[str, dict[str, Path]], Any]",
) -> list[str]:
    """Build every db in ``wanted``, serially or across workers, into ``roots``.

    Returns the db_ids that succeeded, in the order of ``wanted`` — not completion
    order: ``Executor.map`` yields in *submission* order, so a slow first schema holds
    back results already finished behind it. Failures land in ``build_errors`` and are
    dropped rather than aborting the run, because one bad schema must not cost a scale
    run its other 68.

    Module-level, and taking the actual build as ``build_one_db``, for one reason:
    this dispatch decides whether ``--build-workers N`` produces the same corpora as
    ``--build-workers 1``, and while it was a closure inside :func:`run_datalake` that
    could only be tested by driving the whole harness — Postgres, gold, serve loop and
    all. So it never was. The isolation, promotion and staging mechanics underneath it
    each had tests; the composition of them did not, and the composition is where a
    parallel build would silently diverge from a serial one.

    The width is deliberately invisible to the result. At ``build_workers == 1`` the
    builds write straight into the shared roots; above 1 each gets a private staging
    root (:func:`_stage_roots`) and is promoted under a lock (:func:`_promote_build`).
    Promotion is the only step touching shared state, and it is a directory move per
    arm — serialised so two dbs cannot race on creating the same parent, and cheap
    enough not to meaningfully reduce the parallel fraction.
    """
    built: list[str] = []

    def _build_one(db: str) -> str | None:
        # Pre-assigned so the ``except`` branch can always read it. ``_stage_roots``
        # can itself raise (a full disk, a permission error), and this used to be
        # assigned only inside the ``try`` — so that failure raised
        # ``UnboundLocalError`` *from the exception handler*, which is not caught and
        # took down the whole build phase rather than one schema.
        build_roots = roots
        # ...but "did staging happen" is then a separate question from "is
        # ``build_workers > 1``", and conflating them made the failure message name the
        # shared arm roots — which by then hold other schemas' promoted corpora — as
        # this build's disposable debris.
        staged = False
        try:
            if build_workers > 1:
                build_roots = _stage_roots(staging_root, roots, db, resume=resume)
                staged = True
            build_one_db(db, build_roots)
            # Completeness markers: a successful return from the build means each
            # arm that holds YAML for this db is finished. Fake/test builds and
            # paths that write YAML without calling ``_mark_build_complete`` still
            # get the durable contract before promote/skip can see them.
            for _arm, path in build_roots.items():
                if _has_yaml(path, db) and not _corpus_complete(path, db):
                    _mark_build_complete(path, db)
            if staged:
                with build_lock:
                    for arm, path in build_roots.items():
                        _promote_build(path, roots[arm], db)
            return db
        except Exception as err:  # one bad db must not lose the whole run
            detail = f"{type(err).__name__}: {err}"
            with build_lock:
                build_errors[db] = detail
            print(f"*** build FAILED for [{db}] — dropped from pool: {detail}")
            if staged:
                # The staging roots are KEPT, not deleted. They hold whatever the
                # failed build managed to write — findings, a run manifest, a partial
                # corpus — and that is the only evidence of why it failed. Deleting
                # them here also undid ``_promote_build``'s refusal to discard a
                # diagnostic it could not place, which was the entire point of that
                # refusal.
                #
                # Safe to leave: staging is cleared at the START of every build, so a
                # later ``--resume`` cannot mistake this debris for a finished corpus,
                # and it all lives under the run directory rather than somewhere the
                # operator has to be told about.
                kept = [str(p) for p in build_roots.values() if p.exists()]
                if kept:
                    print(
                        f"    [{db}] staging kept for inspection: "
                        f"{', '.join(sorted(kept))}"
                    )
            return None

    def _consume(results: "Iterable[str | None]") -> None:
        for done in results:
            if done is not None:
                built.append(done)
                # Two things are load-bearing in this one line and neither is
                # cosmetic. The ``[db]`` tag is N11's build-log prefix, so 20
                # interleaved worker lines stay attributable. The literal
                # ``built corpora:`` is what
                # ``test_progress_is_reported_as_each_build_finishes_not_all_at_the_end``
                # counts on stdout to prove this loop streams rather than
                # batches — N11 reworded it to ``corpora ready`` and that test
                # went to zero observed writes while the streaming itself was
                # still fine. Keep both phrases if you reword this again.
                print(
                    f"  build [{done}] done — "
                    f"built corpora: {len(built)}/{len(wanted)}"
                )

    # Consumed INSIDE the ``with``, not after it. ``Executor.map`` submits eagerly but
    # yields lazily, and ``pool.__exit__`` calls ``shutdown(wait=True)`` — so draining
    # the iterator after the block means nothing prints until every schema has
    # finished, then all of it at once. On the 69-schema run this exists for that is
    # hours of silence, and if the process dies partway there is no log of what
    # completed. Verified: at 2 workers over 6 half-second builds, consuming inside
    # streams at 0.5/0.5/1.0/1.0/1.5/1.5s; consuming after prints all six at 1.5s.
    if build_workers > 1 and len(wanted) > 1:
        print(
            f"  building {len(wanted)} db(s) across {build_workers} worker(s) "
            f"(each in a private staging root)"
        )
        with ThreadPoolExecutor(max_workers=build_workers) as pool:
            _consume(pool.map(_build_one, wanted))
    else:
        _consume(_build_one(db) for db in wanted)
    return built


def _promote_build(staged: Path, shared: Path, db_id: str) -> None:
    """Move one finished db's build out of its private staging root into the shared
    arm root, then discard the staging root.

    This is what makes the build loop parallelisable without touching the curator.
    Everything a curator build writes — the agent's ``FilesystemBackend`` root and all
    five sidecars — is rooted at the arm root it is handed, and the sidecars are
    *root-level* filenames. Two concurrent builds sharing that
    root would interleave writes to the same ``clarifications.jsonl`` /
    ``validate_findings.jsonl`` / ``adversary_findings.jsonl``, which for the SME arm
    means one schema's clarification text leaking into another's corpus. Giving each
    build a private root keeps every path relationship *inside* a build byte-identical
    to the serial case — deliberately, because the one time those paths were re-pointed
    the SME arm silently read its ledger from a directory a build step had moved, and
    every SME number for weeks was a no-op.

    The schema tree is installed by a same-filesystem swap: stage → ``.incoming``,
    then rename live → ``.previous`` and ``.incoming`` → live, then delete
    ``.previous``. A failure between installing the new tree and cleanup leaves
    either the old or the new valid destination, never an empty hole. Delete-then-
    move used to destroy the last good corpus when the process died between the
    two steps.

    Leftovers from a prior crashed promote are healed before anything is deleted:
    if the live dest is missing, ``.previous`` (preferred) or ``.incoming`` is
    restored first. Only then are unused leftover names cleared. Clearing first
    was how a second attempt could erase the last recoverable corpus.
    """
    src_schema = staged / db_id
    if src_schema.is_dir():
        if not _corpus_complete(staged, db_id):
            raise RuntimeError(
                f"refusing to promote incomplete build for {db_id!r} from {staged}: "
                f"missing {_BUILD_COMPLETE_MARKER} (partial YAML is not a corpus)"
            )
        dest_schema = shared / db_id
        dest_schema.parent.mkdir(parents=True, exist_ok=True)
        incoming = shared / f".{db_id}.incoming"
        previous = shared / f".{db_id}.previous"
        _heal_promote_leftovers(dest_schema, previous=previous, incoming=incoming)
        # Move staged tree onto the shared filesystem under a temp name first.
        # Heal may have left nothing named ``incoming``; if a fresh leftover name
        # somehow still exists it is empty debris and safe to replace.
        if incoming.exists():
            shutil.rmtree(incoming)
        shutil.move(str(src_schema), str(incoming))
        moved_aside = False
        try:
            if dest_schema.exists():
                # ``previous`` must be free: heal already restored or cleared it.
                if previous.exists():
                    shutil.rmtree(previous)
                dest_schema.rename(previous)
                moved_aside = True
            incoming.rename(dest_schema)
        except BaseException:
            # Restore the prior corpus if we had moved it aside and the new tree
            # did not land. Prefer leaving *some* valid destination over none.
            if moved_aside and previous.exists() and not dest_schema.exists():
                previous.rename(dest_schema)
            elif incoming.exists() and not dest_schema.exists():
                incoming.rename(dest_schema)
            raise
        if previous.exists():
            shutil.rmtree(previous, ignore_errors=True)
    # Sidecars sit at the staging root; lift them into the promoted per-db folder
    # before the staging root is discarded.
    # Anything that could not be placed is already recorded, durably, in the shared
    # root by ``_relocate_sidecars`` — see the marker it writes there. Raising here
    # instead was the previous attempt and it did not hold: the staging root it
    # preserved is cleared at the start of the next build, so a ``--resume`` erased
    # the evidence and then promoted cleanly, scoring the db as healthy.
    _relocate_sidecars(staged, db_id, dest_root=shared)
    shutil.rmtree(staged, ignore_errors=True)


def _heal_promote_leftovers(
    dest_schema: Path, *, previous: Path, incoming: Path
) -> None:
    """Restore a missing live dest from prior-promote leftovers before deleting any.

    Order matters. A process death after ``dest → .previous`` (and possibly after
    ``.incoming → dest`` failed) leaves the recoverable corpus under ``.previous``
    and/or ``.incoming`` with no live dest. Deleting those names first destroys the
    only recoverable tree. Prefer ``.previous`` (the last known-good live corpus)
    over ``.incoming`` (the candidate that may never have been fully installed).
    """
    if dest_schema.exists():
        # Live dest is valid; leftover temp names from an older crash are debris.
        if incoming.exists():
            shutil.rmtree(incoming)
        if previous.exists():
            shutil.rmtree(previous)
        return
    if previous.exists():
        previous.rename(dest_schema)
        if incoming.exists():
            shutil.rmtree(incoming)
        return
    if incoming.exists():
        incoming.rename(dest_schema)


def _assert_build_coverage(
    built: "Sequence[str]",
    wanted: "Sequence[str]",
    build_errors: dict[str, str],
) -> None:
    """Refuse to serve a pool far from the one requested.

    Build attrition is its own failure mode and needs its own gate. A run missing much
    of its requested pool is not the experiment anyone asked for: the pooled router
    ranks against corpora that were never built, the census disagrees with the arm
    summaries, and :func:`governed_bi.eval.index.quotable` already refuses the run on
    ``build_errors`` alone. Serving it anyway spends the serve budget on a number nobody
    could quote.

    It also takes a load off the gold denominator. The gold-failure share is measured
    against every requested schema, which is right for its own question — is this a
    systematic misconfiguration across what we asked for — but it means a gold problem
    confined to a small surviving pool reads as a small fraction. Catching the attrition
    here stops that pool from being served at all, rather than teaching the gold check
    to detect build failures.

    Module-level so it can be exercised. As an inline block its only test asserted
    arithmetic about the threshold constant and never called it, which would have passed
    with the gate deleted.
    """
    if not built:
        raise RuntimeError(f"every db failed to build: {build_errors}")
    coverage = len(built) / len(wanted)
    if coverage < _BUILD_COVERAGE_ABORT_FRACTION:
        # Named, and the count first: an operator reading this needs to know how much of
        # the pool went missing before they need to know which schemas.
        detail = "; ".join(f"{db}: {err}" for db, err in sorted(build_errors.items())[:3])
        more = "" if len(build_errors) <= 3 else f" (+{len(build_errors) - 3} more)"
        raise RuntimeError(
            f"only {len(built)} of {len(wanted)} schema(s) built "
            f"({coverage:.0%}, below the {_BUILD_COVERAGE_ABORT_FRACTION:.0%} floor) — "
            f"refusing to serve a pool this far from the one it set out to build, "
            f"because its numbers could not be quoted anyway. Note this counts only "
            f"schemas that were present on Postgres and failed to build; schemas absent "
            f"from Postgres never reach this check and are reported separately as "
            f"`dbs_absent_from_postgres`. Fix the builds, or narrow the request with "
            f"--limit-dbs so the pool you score is the pool you asked for. "
            f"Failures: {detail}{more}"
        )


def _quarantine_curator_failures(
    built: "Sequence[str]",
    curator_errors: dict[str, dict],
    *,
    n_requested: int,
) -> tuple[list[str], dict[str, str]]:
    """Withhold schemas whose curator recorded an error, before serve spends on them.

    Returns ``(servable, reason_by_db)``.

    The incident is in :data:`_CURATOR_ERROR_QUARANTINE_ABORT_FRACTION`: 13 of 55
    schemas hit the curator's recursion limit, ``_invoke_agent`` filed the crash in
    ``run_manifest.json`` and let the build finish, and 13 partial corpora were then
    served, scored, and ranked against the complete ones. The driver already *knew* —
    it collected these errors and printed a warning per schema — and the knowledge was
    spent on nothing but that warning plus an end-of-run gate that can only disqualify
    the run whole.

    A partial corpus is not a weaker treatment, it is an unknown one: nobody can say
    which tables got descriptions before the agent ran out of steps, so its arm-to-arm
    delta measures the recursion limit. It also pollutes the schemas that ARE intact,
    because the pooled router ranks every schema in the pool against every question.

    ``reason_by_db`` carries the per-schema reason into ``summary.json`` and the ledger.
    A schema that simply disappeared from the pool is the failure
    ``dbs_absent_from_postgres`` exists to prevent, one layer in: the run would report
    full coverage of a pool it had silently shrunk.

    Module-level and returning its decision rather than mutating, for the reason
    :func:`_assert_build_coverage` is: the inline version of that gate had a test that
    asserted arithmetic about its threshold and never called it, so it would have
    passed with the gate deleted.
    """
    reason_by_db: dict[str, str] = {}
    for db in built:
        errs = curator_errors.get(db)
        if not errs:
            continue
        # First line per arm. ``_collect_curator_errors`` already truncates, but
        # ``_invoke_agent`` deliberately keeps the FULL traceback in the file it reads —
        # so anything that stops going through that truncation would paste a stack into a
        # ``summary.json`` field and a ledger record, where it is unreadable in both.
        reason_by_db[db] = "; ".join(
            f"{arm}: "
            + str(block.get("error") or block.get("fix_pass_error")).splitlines()[0]
            for arm, block in sorted(errs.items())
        )
    servable = [db for db in built if db not in reason_by_db]
    if not reason_by_db:
        return servable, reason_by_db

    # Unconditional, and ahead of the proportional test: with nothing left to serve the
    # pool is empty and every downstream aggregate divides by zero, so the share (and
    # its small-pool floor) must not get a chance to wave it through.
    if not servable:
        raise RuntimeError(
            f"every one of the {len(built)} built schema(s) recorded a curator error, so "
            "there is no intact corpus left to serve: "
            + "; ".join(f"{db} ({why})" for db, why in sorted(reason_by_db.items())[:3])
            + ". Fix the curator, then rebuild. On a GraphRecursionError: the tool-call "
            "budget is derived per schema when --max-agent-steps is unset, so check "
            "whether an explicit --max-agent-steps is capping it below what the schema "
            "needs (each schema's run_manifest.json records the tool_call_budget it "
            "ran with, and the effective recursion limit is 3 * budget + 4)."
        )
    share = len(reason_by_db) / n_requested if n_requested else 0.0
    systematic = (
        len(reason_by_db) >= _CURATOR_ERROR_QUARANTINE_ABORT_MIN_DBS
        and share > _CURATOR_ERROR_QUARANTINE_ABORT_FRACTION
    )
    if systematic:
        detail = "; ".join(f"{db}: {why}" for db, why in sorted(reason_by_db.items())[:3])
        more = "" if len(reason_by_db) <= 3 else f" (+{len(reason_by_db) - 3} more)"
        raise RuntimeError(
            f"{len(reason_by_db)} of {n_requested} requested schema(s) recorded a "
            f"curator error and would be withheld ({share:.0%}, above the "
            f"{_CURATOR_ERROR_QUARANTINE_ABORT_FRACTION:.0%} ceiling) — refusing to "
            f"serve the remaining {len(servable)}, because at this share the curator is "
            f"misconfigured rather than unlucky and the surviving pool is a much smaller "
            f"benchmark than the one this run names. On a GraphRecursionError: the "
            f"tool-call budget derives from each schema's size when --max-agent-steps "
            f"is unset, so drop an explicit --max-agent-steps (or raise it) rather "
            f"than assume the default is the cap — each schema's run_manifest.json "
            f"records the tool_call_budget it ran with, and the effective recursion "
            f"limit is 3 * budget + 4. Then rebuild, or narrow the request with "
            f"--limit-dbs so the pool you score is the pool you asked for. "
            f"Errors: {detail}{more}"
        )
    print(
        f"*** WARNING: withholding {len(reason_by_db)} of {n_requested} requested "
        f"schema(s) from the serve loop — the curator recorded an error, so their "
        f"corpora are partial and neither their questions nor their tables should reach "
        f"the router: {sorted(reason_by_db)[:10]}"
        + (f" (+{len(reason_by_db) - 10} more)" if len(reason_by_db) > 10 else "")
        + f". Serving the remaining {len(servable)}; recorded as "
        "`dbs_quarantined_curator_error` and this blocks quotability ***"
    )
    return servable, reason_by_db


def _quarantine_zero_question_schemas(
    built: "Sequence[str]",
    pairs: "Sequence[tuple[Any, str]]",
) -> tuple[list[str], list[str]]:
    """Withhold schemas that contribute zero questions from the serve / census pool.

    Returns ``(servable, zero_question_dbs)``.

    After the dataset rescreen (eval-rebuild §4), a schema can still *build* while
    its split file has no rows left for it. Leaving it in ``built`` made it look
    built-but-unscored, inflated ``corpus_census`` / router candidates with a
    schema that never enters the graded denominator, and corrupted the pool
    census against ``n_questions``. Quarantine is explicit: the schema leaves
    ``built_dbs``, is named in ``dbs_zero_questions``, and ``quotable()`` refuses.

    Order of ``built`` is preserved for the survivors (same contract as
    :func:`_quarantine_curator_failures`).
    """
    scored = {db for _item, db in pairs}
    empty = [db for db in built if db not in scored]
    if not empty:
        return list(built), []
    servable = [db for db in built if db in scored]
    if not servable:
        raise RuntimeError(
            f"every one of the {len(built)} built schema(s) has zero questions in "
            "the scored split, so there is nothing to serve: "
            + ", ".join(empty[:10])
            + (" (+more)" if len(empty) > 10 else "")
            + ". Narrow --dbs / --split, or fix the dataset filter that emptied "
            "these schemas."
        )
    print(
        f"*** WARNING: withholding {len(empty)} built schema(s) with zero questions "
        f"in this split from the serve loop and corpus census: "
        f"{empty[:10]}"
        + (f" (+{len(empty) - 10} more)" if len(empty) > 10 else "")
        + f". Serving the remaining {len(servable)}; recorded as "
        "`dbs_zero_questions` and this blocks quotability ***"
    )
    return servable, empty


def _pooled_items(
    dataset_dir: Path, db_ids: list[str], *, limit: int | None, split: str = "test"
) -> list[tuple[Any, str]]:
    """Load one split's items for each db, tagged with their ``db_id`` (``EvalItem``
    carries no db_id). ``limit`` caps *per db* to keep a subset run balanced.

    ``split="train"`` scores the questions the curator itself was built from, so it
    is a **diagnostic**, not a held-out measurement — see :func:`run_datalake`.

    Schemas with no rows for the split contribute nothing here; callers must run
    :func:`_quarantine_zero_question_schemas` before treating ``db_ids`` as the
    scored pool, or a zero-question schema stays in ``built_dbs`` and corrupts
    census / routing while looking built-but-unscored.
    """
    pairs: list[tuple[Any, str]] = []
    for db in db_ids:
        items = load_bird_items(
            dataset_dir, db, split=split, gold_sql_field="sql_rename"
        )
        if limit is not None:
            items = items[:limit]
        pairs.extend((it, db) for it in items)
    return pairs


def _assert_train_test_disjoint(dataset_dir: Path, db_ids: list[str]) -> dict[str, Any]:
    """Fail before serving if any db's train and test question ids overlap (C4).

    The curator reads train; the score is test. An overlap means a scored question
    was in the curator's own input, and no downstream metric can see that — the run
    just looks good. This driver serves dozens of dbs at once, which is exactly
    where a bad split regeneration would hide.
    """
    overlaps: dict[str, list[str]] = {}
    text_overlaps: dict[str, list[str]] = {}
    n_train = 0
    n_test = 0
    n_text_overlap = 0
    for db in db_ids:
        train_items = load_bird_items(
            dataset_dir, db, split="train", gold_sql_field="sql_rename"
        )
        test_items = load_bird_items(
            dataset_dir, db, split="test", gold_sql_field="sql_rename"
        )
        train_ids = {it.question_id for it in train_items if it.question_id}
        test_ids = {it.question_id for it in test_items if it.question_id}
        n_train += len(train_ids)
        n_test += len(test_ids)
        both = train_ids & test_ids
        if both:
            overlaps[db] = sorted(both)[:5]
        # Byte-identical question TEXT across splits. Id-disjointness is the coarse
        # form and it passes here; the text form is documented in `oracle.py` ("five
        # questions appear in both splits with byte-identical text") and was checked
        # by nothing (AUDIT E5). Recorded, not fatal: on this dataset the known cases
        # share gold SQL, so they are harmless — but a scored question whose exact
        # words the curator read is not something a run should be able to hide.
        train_text = {(it.question or "").strip() for it in train_items}
        shared_text = sorted(
            {(it.question or "").strip() for it in test_items} & train_text - {""}
        )
        if shared_text:
            n_text_overlap += len(shared_text)
            text_overlaps[db] = shared_text[:5]
    if overlaps:
        raise AssertionError(f"train/test question_id overlap: {overlaps}")
    if text_overlaps:
        print(
            f"train/test question TEXT overlap: {n_text_overlap} question(s) across "
            f"{len(text_overlaps)} schema(s) — recorded in the manifest, not fatal"
        )
    return {
        "train_test_disjoint": True,
        "n_train_ids": n_train,
        "n_test_ids": n_test,
        "n_train_test_text_overlap": n_text_overlap,
        "train_test_text_overlap_examples": text_overlaps,
    }


def _load_built_corpus(root: Path, built: list[str]) -> Corpus:
    """Load exactly the schemas in ``built`` from a shared arm root.

    Scoping by the list rather than by the directory is the fix: the root is shared
    and cumulative, so a ``schema=None`` load serves whatever *any* attempt ever
    wrote there. A db dropped from ``built`` (a transient Postgres blip on an
    already-built db was enough) leaves its YAML behind, and that YAML then competes
    as a router candidate for every other db's questions — silently changing the
    routing problem's difficulty between two runs of the same db set, and
    desynchronising ``corpus_census`` / ``corpus_validation`` from ``built_dbs``.
    The set being scored is knowable; the directory's contents are not.
    """
    corpus = Corpus()
    for db in built:
        corpus.assets.extend(load_corpus(root, schema=db).assets)
    return corpus


def _stage_event_rows(
    meta: dict[str, Any], *, question_id: str, arm: str, db_id: str
) -> list[dict[str, Any]]:
    """Flatten one question's serve-side ``stage_events`` into per-stage records.

    Tolerant of the key being absent — the serve-path producer can be older than
    this reader, and a turn with no timings has none to report — but loud about a
    malformed payload: dropping a misshapen one silently is how an instrumentation
    regression becomes an empty file nobody questions.
    """
    events = meta.get("stage_events")
    if events is None:
        return []
    if not isinstance(events, list):
        print(
            f"*** WARNING: stage_events for {question_id} is a "
            f"{type(events).__name__}, not a list — dropped"
        )
        return []
    out: list[dict[str, Any]] = []
    n_malformed = 0
    for event in events:
        if not isinstance(event, dict):
            n_malformed += 1
            continue
        out.append(
            {
                "question_id": question_id,
                "arm": arm,
                "db_id": db_id,
                # Same ids Langfuse / run.log use — join key for N12a three-sink accept.
                "run_id": meta.get("run_id"),
                "turn_id": meta.get("turn_id"),
                "stage": event.get("stage"),
                "status": event.get("status"),
                "ms": event.get("ms"),
                "detail": event.get("detail"),
            }
        )
    if n_malformed:
        print(
            f"*** WARNING: dropped {n_malformed} malformed stage event(s) for "
            f"{question_id}"
        )
    return out


class _RowSink:
    """Append-only JSONL writer that flushes every row.

    A pooled run is hours long. Buffering rows in memory means a crash loses all of
    them and leaves ``--resume`` nothing to resume from, so each row leaves this
    process before the next question starts. ``flush()`` without ``fsync()`` is
    deliberate: it survives the threat this defends against (the Python process
    being killed — the bytes are already in the OS page cache) but **not** an OS
    crash or power loss, and an fsync per row would cost throughput for a threat
    an eval harness does not face. Rows arrive in submission order (the serial
    loop, or :func:`run_ordered_pool`'s ordered ``on_result``), so no lock is needed.
    """

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        # A run killed mid-write leaves a partial final line with no newline.
        # Appending straight onto it would splice the next row into the wreckage
        # and lose BOTH, so terminate the truncated line first — ``_read_rows``
        # then drops the fragment alone.
        if path.exists() and path.stat().st_size:
            with path.open("rb") as probe:
                probe.seek(-1, 2)
                needs_newline = probe.read(1) != b"\n"
            if needs_newline:
                with path.open("a", encoding="utf-8") as fh:
                    fh.write("\n")
        self._fh = path.open("a", encoding="utf-8")

    def write(self, row: dict[str, Any]) -> None:
        self._fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        self._fh.flush()

    def close(self) -> None:
        self._fh.close()


def _read_rows(path: Path) -> list[dict[str, Any]]:
    """Rows scored by a previous (possibly interrupted) run of this arm."""
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except ValueError:
                # A truncated line is the normal signature of a killed run. Drop it
                # and re-score that question rather than abort the resume. Named,
                # because a malformed line anywhere but the end is a real defect.
                print(
                    f"*** WARNING: dropping malformed row {path.name}:{lineno} "
                    "(will be re-scored)"
                )
    return rows


# Manifest keys that change what a scored row MEANS; anything outside this set
# (timestamps, paths) may differ freely between a run and its resume.
# Derived from the ledger's own list rather than spelled again. These two were
# separate hand-maintained tuples that a comment claimed were in sync, and they had
# drifted: this one named ``skip_agent`` and ``git_sha`` while the ledger's
# ``_resume_drift`` iterated the *comparability* keys and saw neither, so a resume
# after a code edit warned once on the console and then recorded no drift at all.
# (``skip_agent`` itself was retired at ``MANIFEST_SCHEMA_VERSION`` 2, M3 N10.)
#
# Notable members, whose reasons live at the definition site:
# ``prompt_set_hash`` is the hash and not the variant map, so editing a variant's
# text cannot blend two prompts into one arm under an unchanged-looking id;
# ``git_sha`` is fatal within a directory and unremarkable between directories,
# which is exactly why ``RESUME_DRIFT_KEYS`` is a superset of ``COMPARABILITY_KEYS``
# instead of the same tuple.
_RESUME_KNOBS = tuple(key for key, _label in RESUME_DRIFT_KEYS)


def _question_scope_hash(pairs: "Sequence[tuple[Any, str]]") -> str:
    """Stable hash of the effective ``(question_id, db_id)`` pool after caps.

    Caps (``limit``, ``limit_dbs``, ``--dbs``) change which questions are scored;
    recording only the knobs is not enough when the underlying split files move.
    """
    import hashlib

    lines = sorted(
        f"{db}\t{item.question_id or item.question}" for item, db in pairs
    )
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()[:16]


def _build_manifest(
    *,
    bird_dir: Path,
    split: str,
    # ``None`` when this run needs no model at all (an empty fair-arm set and no
    # oracle rung beyond ``oracle_sql`` — the ``--oracle-only`` inference, M3 N10),
    # the configured name otherwise. Decided by the caller, once, from ``arms`` /
    # ``oracles`` — not a second flag this builder has to keep in sync with the real
    # one, which is exactly how ``skip_agent`` (retired at schema version 2) drifted.
    model_name: str | None,
    prompt_variants: dict[str, str],
    route_top_k: int,
    route_llm_pick: bool,
    schema_pick_max_columns: int,
    use_embedder: bool,
    serve_workers: int,
    # The graded pool's identity, from ``metrics.question_pool_hash``. Required, unlike
    # the scope arguments below, because it is a comparability GATE key: an omitted
    # scope field is compared only within this directory on resume, while an omitted
    # gate key reads as "both runs agree" in the ledger for every future pair. The
    # dataset is filtered upstream, so this is the only field that moves when the
    # question pool does.
    question_pool_hash: str | None,
    # ADR 0003 note governance, as ``Settings`` has it for this run. Gate keys, so
    # required here for the same reason ``question_pool_hash`` is: ``pin_triggers_enabled``
    # decides whether the corpus's authored triggers fire at all, and it moves the
    # router's shortlist as well as the prompt.
    always_note_global_max: int,
    always_note_char_max: int,
    pin_triggers_enabled: bool,
    pin_require_certified: bool,
    pin_max: int,
    # Graded delivery. Required here even though the shared builder defaults it, because
    # this driver *overrides* the shipped ``False`` and is the reason the knob is a gate
    # key at all: a run that handed the grader an unverified answer where serve would have
    # refused is not comparable to one that refused. Read off ``Settings``, never
    # restated, so the manifest cannot claim a policy the serve path did not use.
    grade_semantic_failures: bool,
    build_workers: int = 1,
    # The run's SCOPE. Not knobs — they decide which arms exist and which questions
    # are in the pool, so a resume that disagrees is not the same experiment at all.
    # Recorded because none of them is derivable from the directory's contents, so
    # they were silently re-read from argv on every invocation: the runbook's resume
    # line omits ``--arms``/``--dbs``/``--oracle``/``--replicate``, so resuming a
    # Step 3 rung directory dropped ``--oracle`` and picked up the four default arms.
    arms: tuple[str, ...] = (),
    oracles: tuple[str, ...] = (),
    replicate_of: str | None = None,
    db_ids: list[str] | None = None,
    limit: int | None = None,
    limit_dbs: int | None = None,
    question_scope_hash: str | None = None,
    llm_temperature: float | None = None,
) -> dict[str, Any]:
    """The pooled driver's manifest, built through the shared register.

    Kept as a named function with this signature because the invariant "a resume
    knob absent from the manifest can never fire" has to be testable without a
    Postgres instance — that guard is the only thing standing between a
    re-invocation and one arm's rows silently spanning two configurations, and it
    reads this dict by key name. :func:`governed_bi.eval.metrics.validate_manifest`
    now enforces the same thing from the other side.
    """
    return metrics.build_manifest(
        mode="datalake",
        bird_dir=bird_dir,
        split=split,
        model_name=model_name,
        prompt_variants=prompt_variants,
        created_at_utc=_utc_ts(),
        route_top_k=route_top_k,
        route_llm_pick=route_llm_pick,
        schema_pick_max_columns=schema_pick_max_columns,
        use_embedder=use_embedder,
        question_pool_hash=question_pool_hash,
        always_note_global_max=always_note_global_max,
        always_note_char_max=always_note_char_max,
        pin_triggers_enabled=pin_triggers_enabled,
        pin_require_certified=pin_require_certified,
        pin_max=pin_max,
        grade_semantic_failures=grade_semantic_failures,
        arms=arms,
        oracles=oracles,
        replicate_of=replicate_of,
        # ``None`` means "the whole split", which is different from an empty list.
        db_ids=None if db_ids is None else sorted(db_ids),
        limit=limit,
        limit_dbs=limit_dbs,
        question_scope_hash=question_scope_hash,
        serve_workers=serve_workers,
        build_workers=build_workers,
        llm_temperature=llm_temperature,
    )


def _read_manifest(out_dir: Path) -> dict[str, Any]:
    """The run directory's manifest, or ``{}`` when absent.

    Raises when the file is there but unreadable. An *absent* manifest legitimately
    means "this directory predates the check"; an *unparseable* one means the resume
    guard has nothing to compare and cannot know it. Returning ``{}`` for both is
    how a manifest torn by a kill mid-``write_text`` silently disables the fatal
    split check and the knob-drift warning on the very next resume.
    """
    path = out_dir / "manifest.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as err:
        raise RuntimeError(
            f"{path} exists but is unreadable ({type(err).__name__}: {err}); a run "
            "killed mid-write leaves this. Resuming would skip the split / knob-drift "
            "guard with no signal. Inspect it, then delete it to resume without that "
            "guard, accepting that this arm's rows may mix two configurations."
        ) from err
    if not isinstance(data, dict):
        raise RuntimeError(
            f"{path} is valid JSON but not an object ({type(data).__name__}); the "
            "resume guard cannot read the prior run's knobs from it."
        )
    return data


def _merge_resume_manifest(
    prior: dict[str, Any], current: dict[str, Any]
) -> dict[str, Any]:
    """Keep the ORIGINAL run's knobs and append this attempt under ``resumes``.

    Overwriting would make drift detection one-shot: across a chain of resumes
    that each change a knob, only the last hop stays visible and nothing records
    the configuration the earliest rows were scored under. It would also silently
    redefine ``created_at_utc`` as "start of the most recent resume".
    """
    if not prior:
        return current
    return {**prior, "resumes": [*prior.get("resumes", []), current]}


def _check_resume_manifest(
    out_dir: Path,
    expected: dict[str, Any],
) -> None:
    """Refuse to resume a run whose recorded knobs differ from this one's.

    Appending rows scored under a different split, model, shortlist width, picker
    vocabulary or retrieval backend into one ``generations.<arm>.jsonl`` yields a
    single score silently averaged over two configurations. ``split`` is fatal
    because the two question pools are disjoint; the rest warn loudly, since an
    operator may be knowingly restarting a stalled run with a different knob.

    A missing manifest means the directory predates this check (runs now write one
    before serving anything), so there is nothing to compare — the per-row split
    guard in :func:`_run_pool_arm` is the remaining backstop. An *unreadable* one
    raises from :func:`_read_manifest` instead.
    """
    prior = _read_manifest(out_dir)
    if not prior:
        return
    # A manifest with no recorded split is NOT a wildcard. A pre-``split``-field run
    # directory is test-only by construction, so treating its silence as "compatible
    # with whatever you asked for" is exactly how a `--split train` resume of an old
    # test run mixes two disjoint question pools into one file with no error.
    prior_split = prior.get("split")
    if prior_split != expected.get("split"):
        raise RuntimeError(
            f"{out_dir} holds a --split {prior_split} run; refusing to resume it as "
            f"--split {expected.get('split')} (disjoint question pools would be "
            "scored as one). Use a fresh --out directory."
        )
    # SCOPE is fatal, for the same reason ``split`` is: it decides which arms exist
    # and which questions are in the pool, so a resume that disagrees is a different
    # experiment sharing one directory. Fatal in BOTH directions — widening spends a
    # budget nobody asked for, narrowing overwrites ``summary.json`` with a subset and
    # blanks the arms it did not serve.
    #
    # Compared only when the prior manifest recorded the field: a directory written
    # before this existed cannot be checked, and refusing every such resume would
    # strand work that is otherwise fine.
    for key, flag in (
        ("arms", "--arms"),
        ("oracles", "--oracle"),
        ("replicate_of", "--replicate"),
        ("db_ids", "--dbs"),
        ("limit", "--limit"),
        ("limit_dbs", "--limit-dbs"),
        ("question_scope_hash", "question pool"),
    ):
        if key not in prior:
            continue
        was, now = prior.get(key), expected.get(key)
        if was != now:
            raise RuntimeError(
                f"{out_dir} was run with {flag} {was!r} and this resume asks for "
                f"{now!r}. Scope is not a resume knob: widening it serves arms and "
                f"schemas nobody asked for (on a paid run, a full curator pass and "
                f"extra serve passes), and narrowing it rewrites summary.json with a "
                f"subset. Repeat the original {flag} on the resume, or use a fresh "
                "--out directory."
            )
    drift = {
        k: (prior.get(k), expected.get(k))
        for k in _RESUME_KNOBS
        if k != "split" and prior.get(k) is not None and prior.get(k) != expected.get(k)
    }
    # A changed prompt set is fatal, like a changed split. The other knobs warn
    # because a reader can at least see them in the manifest and judge; a prompt
    # set cannot be judged after the fact, because ``_merge_resume_manifest`` keeps
    # the ORIGINAL manifest's top-level values and the ledger's ``comparable()``
    # reads only those. A half-v1/half-v2 directory would therefore present itself
    # as a clean v1 run and get quoted beside one.
    if "prompt_set_hash" in drift:
        was, now = drift["prompt_set_hash"]
        raise RuntimeError(
            f"{out_dir} was scored under prompt set {was} and this run resolves to "
            f"{now}. Rows already on disk keep the old prompts, and nothing "
            "downstream can separate them. Use a fresh --out directory."
        )
    # Code/git SHA drift is ALWAYS fatal on resume. It used to keep a second track —
    # smoke (``--skip-agent``) warned and continued, and a paid resume could opt in
    # with ``--allow-git-sha-drift`` — and that dual-track judgment call is exactly
    # what M3 N10 (Option A, ``--oracle-only``) retired: both the flag and the
    # `skip_agent` manifest knob it read are gone, so there is no longer a smoke
    # track to warn-and-continue on.
    #
    # THIS DOES NOT AUTHORIZE DELETING THE CHECK ITSELF. What this guard prevents —
    # two harness versions' rows silently averaged into one arm's score — is not the
    # hazard decision 12 was about (a global "no model was called" bypass that could
    # combine with any configuration). A resume after a code edit stays fatal
    # unconditionally; a fresh ``--out`` directory is always the answer. If a future
    # change wants a controlled opt-in back, it needs its own decision, not a revival
    # of this one.
    if "git_sha" in drift:
        was, now = drift.pop("git_sha")
        raise RuntimeError(
            f"{out_dir} was scored under git_sha {was!r} and this run is "
            f"{now!r}. Resuming would mix two harness versions into one arm's "
            "rows. Use a fresh --out directory."
        )
    if drift:
        detail = ", ".join(f"{k}: {was!r} -> {now!r}" for k, (was, now) in drift.items())
        print(
            f"\n*** WARNING: resuming {out_dir.name} with changed knobs ({detail}). "
            "Rows already scored keep the OLD configuration, so this arm's score "
            "will mix both. ***\n"
        )


def _schema_of_assets(
    corpus: Any, asset_ids: "list[str] | None"
) -> tuple[set[str], list[str]]:
    """Schemas of the given table-asset ids, plus ids that did not resolve.

    Ids look like ``tbl_<schema>_<name>`` but schema names contain underscores
    (``beer_factory``), so splitting the string guesses wrong. Unresolved ids are
    returned explicitly rather than dropped: a non-empty ``tables_used`` that resolves
    to nothing must not look like "no tables observed" for routing escape.
    """
    out: set[str] = set()
    unresolved: list[str] = []
    for aid in asset_ids or ():
        key = str(aid)
        asset = corpus.by_id(key) if corpus is not None else None
        schema = getattr(asset, "schema", None)
        if asset is not None and getattr(asset, "asset_type", None) == "table" and schema:
            out.add(str(schema))
        else:
            unresolved.append(key)
    return out, unresolved


def _build_db_corpora(
    *,
    db_id: str,
    pg_dsn: str,
    bird_dir: Path,
    roots: dict[str, Path],
    arms: tuple[str, ...],
    chat_client: Any,
    # ``None`` when this run needs no model (see ``run_datalake``'s ``needs_model``):
    # the arms below then build the deterministic halves only.
    lc_model: Any,
    # Per-schema curator budget in TOOL CALLS. ``None`` = let the curator derive it
    # from the schema's size (``curator.pipeline.derive_step_budget``), which is what
    # an unset ``--max-agent-steps`` means. Passed straight through, per schema, so a
    # 3-table and a 73-table schema in the same pool do not share one constant.
    max_agent_steps: int | None,
    resume: bool,
    # ``None`` = every stage at v1. Defaulted only so an offline caller that builds
    # no agent need not know about prompts; the driver always passes the run's map.
    prompt_variants: dict[str, str] | None = None,
    # The run's Settings, carrying that same resolved map. The curator stamps its
    # own run records from this; without it the curator re-reads the TOML and
    # records the corpus under a prompt set the agent never ran on.
    settings: Any | None = None,
) -> None:
    """Build the requested arms for one ``db_id`` into the shared roots. Baseline is
    always built (it's deterministic and anchors the per-db suspect set); curated is
    built when curated or curated_sme is requested; the SME arm only when requested.
    Raises on any build failure (the caller records it and drops the db).

    ``prompt_variants`` is the run's resolved map. The curator and SME prompts are
    threaded from it rather than re-read inside the curator, because the manifest
    stamps this map for the *whole* run: a corpus built under a prompt the manifest
    does not name would make the curated arms' numbers unattributable."""
    need_seeded = "seeded" in arms
    need_curated = "curated" in arms or "curated_sme" in arms
    need_sme = "curated_sme" in arms

    # Every requested arm is already on disk: return before opening Postgres or
    # re-reading the BIRD split. Those steps can fail transiently, and a failure on
    # a db that is already fully built drops it from ``built`` (so it is not scored)
    # while its YAML stays in the shared corpus root — which used to leave it
    # competing as a router candidate for every OTHER db's questions and
    # desynchronised the census from ``built_dbs``. The SME brief's leakage assertion
    # is not re-run here because it necessarily passed in the attempt that wrote that
    # corpus: it is asserted before the SME build, never after.
    already = ["baseline"]
    if need_seeded:
        already.append("seeded")
    if need_curated:
        already.append("curated")
    if need_sme:
        already.append("curated_sme")
    if resume and all(_corpus_complete(roots[a], db_id) for a in already):
        return

    from ..curator.pipeline import (
        build_baseline_corpus,
        build_curated_corpus,
    )
    from ..curator.sme import assert_brief_no_leakage, build_sme_brief

    connector = PostgresConnector(pg_dsn, schema=db_id)  # build profiles ONE schema
    # Sidecar relocation is DEFERRED to the end of the whole db build, not done after
    # each arm. `build_curated_corpus_with_sme` resolves its clarification ledger from
    # the curated arm root *or* the relocated `<db>/_build/` path (cross-resume), and
    # relocating before the SME arm runs within one process still left the live-root
    # read empty until that resolution existed. Deferring costs nothing: every arm's
    # sidecars are per-db within one build, and nothing outside SME reads them until
    # the run aggregates.
    pending_relocations: list[Path] = []

    def _arm_done(arm: str) -> bool:
        """Skip only a *complete* prior build; discard partial YAML before rebuild."""
        root = roots[arm]
        if resume and _corpus_complete(root, db_id):
            return True
        if _discard_incomplete_corpus(root, db_id):
            print(
                f"  [{arm}] discarded incomplete {db_id!r} corpus "
                f"(YAML without {_BUILD_COMPLETE_MARKER}); rebuilding"
            )
        return False

    try:
        if db_id not in connector.list_schemas():
            raise RuntimeError(f"schema {db_id!r} not present on the Postgres instance")
        gateway = Gateway(connector, max_rows=200_000, timeout_s=60.0)
        train = load_bird_items(
            bird_dir / "eval_dataset", db_id, split="train", gold_sql_field="sql_rename"
        )
        test = load_bird_items(
            bird_dir / "eval_dataset", db_id, split="test", gold_sql_field="sql_rename"
        )

        # --- baseline (deterministic, no LLM) ---
        if not _arm_done("baseline"):
            build_baseline_corpus(connector, db_id, roots["baseline"])
            _mark_build_complete(roots["baseline"], db_id)
            pending_relocations.append(roots["baseline"])

        # --- seeded (deterministic: the mechanical half of `curated`, no LLM) ---
        # Same code path as `curated` with the agent switched off, which is exactly
        # what makes the pair a single-variable comparison: `seeded -> curated` adds
        # the LLM agent and nothing else, and `baseline -> seeded` adds the
        # train-SQL seed and nothing else.
        if need_seeded and not _arm_done("seeded"):
            build_curated_corpus(
                connector,
                gateway,
                db_id,
                train,
                roots["seeded"],
                model=None,
                dialect="postgres",
                max_agent_steps=max_agent_steps,
                run_agent=False,
                system_prompt=prompt_text("curator_phase_a", prompt_variants),
                settings=settings,
            )
            _mark_build_complete(roots["seeded"], db_id)
            pending_relocations.append(roots["seeded"])

        # --- curated ---
        if need_curated and not _arm_done("curated"):
            build_curated_corpus(
                connector,
                gateway,
                db_id,
                train,
                roots["curated"],
                model=lc_model,
                dialect="postgres",
                max_agent_steps=max_agent_steps,
                run_agent=lc_model is not None,
                system_prompt=prompt_text("curator_phase_a", prompt_variants),
                settings=settings,
            )
            _mark_build_complete(roots["curated"], db_id)
            pending_relocations.append(roots["curated"])

        if not need_sme:
            return

        # --- SME brief + leakage invariant (asserted whenever an SME arm builds) ---
        # The description CSVs are keyed to BIRD's original identifiers, so the
        # rename map is what re-addresses them to the schema the agent actually
        # queries. Without it the SME is briefed about a schema that is not there.
        desc_dir = description_dir(bird_dir, db_id)
        rename_map = load_rename_map(bird_dir, db_id)
        if desc_dir is None:
            print(
                f"\n*** WARNING: no database_description/ for {db_id!r} under either "
                "BIRD tree — curated_sme is being built BLIND and is not comparable "
                "to the other SME schemas ***"
            )

        def _brief() -> str:
            # ``build_sme_brief`` degrades to "(no description CSVs found)" for a
            # directory that does not exist, which is what a run without the BIRD
            # description CSVs gets — the arm still builds, with a thinner brief.
            built = build_sme_brief(
                desc_dir or Path("/nonexistent-sme-docs"),
                train,
                system_rules=prompt_text("sme_rules", prompt_variants),
                rename_map=rename_map,
            )
            assert_brief_no_leakage(
                built,
                gold_sqls=[it.sql for it in train],
                test_questions=[it.question for it in test],
            )
            return built

        brief = _brief()

        # --- curated_sme ---
        if not _arm_done("curated_sme"):
            _build_sme_arm(
                connector=connector,
                gateway=gateway,
                db_id=db_id,
                train=train,
                out_root=roots["curated_sme"],
                curated_root=roots["curated"],
                brief=brief,
                chat_client=chat_client,
                lc_model=lc_model,
                prompt_variants=prompt_variants,
                settings=settings,
            )
            _mark_build_complete(roots["curated_sme"], db_id)
            pending_relocations.append(roots["curated_sme"])
    finally:
        # Flush after every arm of this db is built, so no arm's relocation can hide
        # an input another arm still needs.
        for arm_root in pending_relocations:
            _relocate_sidecars(arm_root, db_id)
        connector.close()


def _build_sme_arm(
    *,
    connector: Any,
    gateway: Any,
    db_id: str,
    train: list,
    out_root: Path,
    curated_root: Path,
    brief: str,
    chat_client: Any,
    lc_model: Any,
    prompt_variants: dict[str, str],
    settings: Any,
) -> None:
    """One SME arm's build, taking the brief as a parameter.

    Kept as its own function rather than inlined: the brief is the whole treatment,
    so a caller that wants a different one changes an argument instead of a code
    path. It carried two callers when a blind rung existed; one now.

    ``run_agent`` is inferred from ``lc_model`` — the same pattern ``curated`` uses
    (``run_agent=lc_model is not None``) — rather than a separate ``skip_agent``
    flag this function had to keep in sync with the real one (retired at
    ``MANIFEST_SCHEMA_VERSION`` 2, M3 N10).
    """
    from ..curator.clarifications import StaticResponder
    from ..curator.pipeline import build_curated_corpus_with_sme
    from ..curator.sme import SimulatedSme

    run_agent = lc_model is not None
    if not run_agent:
        build_curated_corpus_with_sme(
            connector,
            gateway,
            db_id,
            train,
            out_root,
            responder=StaticResponder(
                default="Domain column used in analytics; treat as reliable unless samples conflict."
            ),
            curated_root=curated_root,
            model=None,
            run_agent_repass=False,
            seed_ledger_if_empty=True,
            system_prompt=prompt_text("curator_phase_b", prompt_variants),
            settings=settings,
        )
        return
    build_curated_corpus_with_sme(
        connector,
        gateway,
        db_id,
        train,
        out_root,
        responder=SimulatedSme(chat_client, brief, gateway=gateway, settings=settings),
        curated_root=curated_root,
        model=lc_model,
        run_agent_repass=True,
        seed_ledger_if_empty=False,
        system_prompt=prompt_text("curator_phase_b", prompt_variants),
        settings=settings,
    )


def _assert_gold_is_trustworthy(
    gold_check: dict[str, Any],
    *,
    n_schemas: int | None = None,
    on_abort=None,
) -> None:
    """Raise unless the sampled gold executed and agreed with its recorded hashes.

    ``on_abort`` runs before raising, for a caller holding a Postgres connection.

    Order matters. Gold that will not *execute* is judged before ``agree_rate``,
    because an exec error lowers ``n_checked`` without touching the rate: one agreeing
    row across sixty-nine schemas reported 1.0, so the run proceeded to grade against
    gold it had never confirmed. A wrong DSN, an unloaded schema, a bad ``search_path``
    and the wrong ``gold_sql_field`` all present exactly that way — the last easy to
    hit, because the un-obfuscated ``sql_sqlite`` parses fine and simply names tables
    this Postgres does not have.

    The exec-failure test is **proportional**, not absolute.
    :func:`validate_gold_hashes_live` catches ``Exception`` broadly and cannot tell a
    misconfiguration from a query that crossed the 60 s gateway timeout, or a gold row
    BIRD never flagged as broken. Misconfiguration takes out essentially every schema;
    an unlucky query takes out one. Aborting on one would let a single slow query make
    the whole split unrunnable, deterministically, with no way past it — worse than the
    fail-open this replaced. Below the threshold the schemas are named on stdout and
    recorded in the summary, where ``eval.index`` turns them into a quotability
    blocker: the run may proceed, but a score for a schema whose gold nothing confirmed
    is not a number to quote.
    """

    def _abort(message: str) -> None:
        if on_abort is not None:
            on_abort()
        raise RuntimeError(message)

    if not gold_check["n_checked"]:
        _abort(f"gold self-check verified 0 rows: {gold_check}")

    failed = gold_check.get("exec_error_dbs") or {}
    # The denominator is the schemas the RUN asked for, passed in by the caller — not
    # ``gold_check["n_dbs"]``, which is however many happened to be sampled that time.
    # The two call sites sample different sets: the pre-flight covers every requested
    # schema, the post-build one only those that built. Deriving the fraction from each
    # meant the same fixed set of gold failures became a larger share after the build,
    # so a configuration the pre-flight had correctly called "a few awkward queries"
    # could cross the threshold and abort — purely because *unrelated* schemas failed to
    # build — which is precisely the abort-after-paying this pre-flight exists to avoid.
    n_dbs = n_schemas if n_schemas is not None else (gold_check.get("n_dbs") or 0)
    # Both a share AND a count. The share alone made a single awkward gold row abort
    # any run of three or fewer schemas — including the runbook's own
    # ``--limit-dbs 3`` smoke — and abort it while claiming "this is a configuration
    # fault", which one failure out of three is no evidence of. One schema failing is
    # never systematic; the total-failure case is already caught above by
    # ``n_checked == 0``.
    systematic = (
        len(failed) >= _GOLD_EXEC_FAILURE_ABORT_MIN_DBS
        and n_dbs
        and (len(failed) / n_dbs) > _GOLD_EXEC_FAILURE_ABORT_FRACTION
    )
    if systematic:
        detail = "; ".join(f"{db}: {err}" for db, err in list(failed.items())[:3])
        _abort(
            f"gold SQL failed to execute on {len(failed)} of {n_dbs} schema(s) — more "
            f"than {_GOLD_EXEC_FAILURE_ABORT_FRACTION:.0%}, so this is a configuration "
            f"fault rather than a few awkward queries, and the grader cannot be trusted "
            f"against gold it cannot run. Check the DSN, that every schema is loaded, "
            f"and that gold is read from `sql_rename` rather than the un-obfuscated "
            f"`sql_sqlite`. First failures: {detail}"
        )
    if gold_check["agree_rate"] < 1.0:
        _abort(f"gold self-check disagreed with live gold: {gold_check}")

    if failed:
        print(
            f"*** WARNING: gold would not execute on {len(failed)} of {n_dbs} "
            f"schema(s), so the grader is unverified there: {sorted(failed)[:10]} — "
            "raise --gold-per-db to sample more rows per schema, or accept that those "
            "schemas' scores rest on gold nothing confirmed (this blocks quotability) "
            "***"
        )
    if gold_check.get("partial_exec_error_dbs"):
        print(
            f"    ({len(gold_check['partial_exec_error_dbs'])} further schema(s) had a "
            "sampled gold query fail but verified on another — not counted against "
            "them)"
        )
    if gold_check.get("dbs_without_usable_gold"):
        print(
            f"*** WARNING: {len(gold_check['dbs_without_usable_gold'])} schema(s) had "
            "no usable gold in the sampled rows, so their grader agreement is "
            f"unverified: {gold_check['dbs_without_usable_gold'][:10]} ***"
        )


def _datalake_gold_selfcheck(
    pairs: list[tuple[Any, str]],
    gold_hashes: dict[str, Any],
    pg_dsn: str,
    identity: Identity,
    *,
    per_db: int = 1,
) -> dict[str, Any]:
    """Prove the vendored normalizer agrees with the precomputed gold hashes, using
    a schema-*pinned* gateway per db (gold ``sql_rename`` is unqualified, so it
    needs ``search_path``). Samples ``per_db`` items from each db in the pool.
    """
    by_db: dict[str, list] = {}
    for item, db in pairs:
        if len(by_db.setdefault(db, [])) < per_db:
            by_db[db].append(item)

    n_checked = 0
    n_agree = 0
    n_exec_errors = 0
    per_db_fail: list[str] = []
    exec_error_dbs: dict[str, str] = {}
    partial_exec_error_dbs: dict[str, str] = {}
    unverified_dbs: list[str] = []
    for db, items in sorted(by_db.items()):
        conn = PostgresConnector(pg_dsn, schema=db)
        try:
            gw = Gateway(conn, max_rows=200_000, timeout_s=60.0)
            res = validate_gold_hashes_live(
                items, gold_hashes, gw, identity, sample=len(items)
            )
        finally:
            conn.close()
        n_checked += res["n_checked"]
        n_agree += round(res["agree_rate"] * res["n_checked"]) if res["n_checked"] else 0
        if res["n_checked"] and res["agree_rate"] < 1.0:
            per_db_fail.append(db)
        # A db whose gold could not be executed at all is the case this pre-flight
        # exists for, and it used to be invisible: an exec error lowered ``n_checked``
        # and never touched ``agree_rate``, so the caller's ``agree_rate < 1.0`` gate
        # passed on a single agreeing row while every other schema failed to run. That
        # is what a wrong DSN, an unloaded schema, a bad ``search_path`` or the wrong
        # ``gold_sql_field`` all look like — the last of which is easy to get wrong,
        # because the un-obfuscated ``sql_sqlite`` field parses fine and simply names
        # tables this Postgres does not have.
        if res.get("n_exec_errors") and not res["n_checked"]:
            # Only when the schema was left with *nothing* verified. A schema where one
            # sampled query timed out and another executed and agreed is verified: the
            # grader demonstrably works there, and raising ``--gold-per-db`` has to buy
            # redundancy rather than more ways to abort.
            n_exec_errors += res["n_exec_errors"]
            exec_error_dbs[db] = (res.get("errors") or ["?"])[0]
        elif res.get("n_exec_errors"):
            partial_exec_error_dbs[db] = (res.get("errors") or ["?"])[0]
        elif not res["n_checked"]:
            # Nothing to execute rather than a failure to execute: every sampled item
            # had missing or unusable gold. Reported, not fatal — it is a property of
            # the dataset, and those questions are ungradeable for every arm equally.
            unverified_dbs.append(db)
    return {
        "n_checked": n_checked,
        "agree_rate": (n_agree / n_checked) if n_checked else 0.0,
        "n_dbs": len(by_db),
        "failed_dbs": per_db_fail,
        # Schemas left with nothing verified because gold would not run. Fatal in
        # aggregate — see :func:`_assert_gold_is_trustworthy`.
        "n_exec_errors": n_exec_errors,
        "exec_error_dbs": exec_error_dbs,
        # Schemas where *some* sampled gold failed but at least one query executed and
        # agreed. Reported, never fatal: the grader is demonstrably working there, and
        # one slow or genuinely-broken gold row out of the dataset is not a reason to
        # discard a run.
        "partial_exec_error_dbs": partial_exec_error_dbs,
        "dbs_without_usable_gold": unverified_dbs,
        # Excludes hash mismatches too. A schema whose gold executed and disagreed is
        # not verified, and it sits in ``failed_dbs`` rather than either bucket above.
        # Unreachable through :func:`_assert_gold_is_trustworthy` — any mismatch drags
        # the aggregate ``agree_rate`` below 1.0 and aborts — but this function is
        # importable, and a field that only tells the truth when a caller happens to
        # gate correctly is the shape of defect this module keeps finding.
        "n_dbs_verified": (
            len(by_db) - len(exec_error_dbs) - len(unverified_dbs) - len(per_db_fail)
        ),
    }


class ArmServingPlan(NamedTuple):
    """How one arm gets served: which corpus, how wide, and under which rung."""

    corpus_arm: str
    rung: "OracleRung | None"
    n_workers: int
    needs_factory: bool


def plan_arm_serving(
    *,
    rung: "OracleRung | None",
    source_arm: str,
    oracle_base: str | None,
    effective_workers: int,
    has_model: bool,
) -> ArmServingPlan:
    """Decide the serving shape for one arm. Pure, so it can be tested.

    Extracted because the decision it encodes is unreachable before the paid run:
    ``--oracle-only`` rejects every rung but ``oracle_sql``, and ``effective_workers``
    is forced to 1 without a model, so no offline command exercises "oracle rung at
    width > 1". A mutation that dropped ``rung`` on the way to the worker factory left
    the whole suite green while making every rung serve as an ordinary arm under a
    rung's name — silently replacing the headroom bounds that every other number in
    the runbook is read against.

    ``corpus_arm`` is the BASE arm for a rung, never the rung's own name: a rung is a
    narrowing of some arm's corpus and its name is not a corpus key, so keying on
    ``source_arm`` raises ``KeyError`` at serve time.
    """
    if rung is not None and oracle_base is None:
        raise ValueError(f"oracle rung {rung.value} has no base arm to narrow")
    corpus_arm = oracle_base if rung is not None else source_arm
    assert corpus_arm is not None  # narrowed by the guard above
    # ``oracle_sql`` submits gold SQL and never calls a model; every other rung serves
    # through the real graph and cannot run without one.
    servable = has_model or rung is None or rung is OracleRung.sql
    needs_factory = effective_workers > 1 and servable
    return ArmServingPlan(
        corpus_arm=corpus_arm,
        rung=rung,
        n_workers=effective_workers if needs_factory else 1,
        needs_factory=needs_factory,
    )


class ServeBindings(NamedTuple):
    """The run-wide objects a worker factory needs. Built once per run."""

    corpora_serve: dict
    pg_dsn: str
    settings: Any
    identity: Any
    model: Any
    embedder: Any
    gold: Any


def arm_worker_factory(
    plan: ArmServingPlan, bindings: "ServeBindings"
) -> "Callable[[int], ServeWorker]":
    """Curry :func:`make_serve_worker_factory` onto one arm's plan.

    Module-level, and takes the plan WHOLE, because this is the last place the rung
    can be lost. As a closure over ``run_datalake``'s locals it was unreachable from
    any test, and dropping ``rung=`` here left the entire suite green while making
    every oracle rung serve as an ordinary arm under a rung's name — replacing the
    headroom bounds the runbook reads every other number against. No offline command
    reaches this path either: ``--oracle-only`` rejects all rungs but ``oracle_sql``,
    and the worker count is forced to 1 without a model.
    """
    return make_serve_worker_factory(
        corpus=bindings.corpora_serve[plan.corpus_arm],
        pg_dsn=bindings.pg_dsn,
        settings=bindings.settings,
        identity=bindings.identity,
        model=bindings.model,
        embedder=bindings.embedder,
        arm=plan.corpus_arm,
        rung=plan.rung,
        gold=bindings.gold if plan.rung is not None else None,
        n_workers=plan.n_workers,
    )


def make_serve_worker_factory(
    *,
    corpus: Any,
    pg_dsn: str,
    settings: Any,
    identity: Any,
    model: Any,
    embedder: Any = None,
    arm: str,
    rung: "OracleRung | None" = None,
    gold: "GoldIndex | None" = None,
    n_workers: int = 1,
) -> "Callable[[int], ServeWorker]":
    """Build the per-worker ``(connector, gateway, solver)`` factory for one arm.

    Each worker owns an unpinned connector (``schema=None`` — the pooled driver spans
    every schema), its own gateway, its own solver and therefore its own graph, and a
    distinct ``session_id``. That is the whole isolation argument for serving an arm
    concurrently.

    **Oracle rungs go through here too.** They were pinned to one worker on the
    grounds that they "rebuild a graph per narrowed corpus, so they cannot share the
    per-arm worker factory" — but that cache is closure-local to a single
    ``oracle_solver`` call, which is exactly why a *per-worker* solver is safe rather
    than why it is impossible. It is the same isolation every fair arm already had.
    Serialising them cost step 3 of the runbook three rungs x the whole split in
    strictly sequential agent loops, on the diagnostics that bound every other number.

    The per-solver graph cap is *divided* by the worker count rather than multiplied,
    so each worker keeps a shorter reuse tail instead of a full one. That holds the
    total flat **up to 8 workers**; past that the floor of 4 dominates and the total
    does grow (16 workers -> 64 graphs, 32 -> 128). The floor is deliberate: a cap of 1
    or 2 defeats the reuse that matters, which is consecutive questions over one
    schema. The runbook's ``--workers 8`` sits exactly on the flat part.

    ``oracle_sql`` compiles nothing at all — it hands gold SQL straight to the grader —
    so it fans out for free.

    Module-level and fully parameterised rather than a closure over ``run_datalake``'s
    locals, so the wiring above can be asserted on behaviour instead of on source text.
    """
    if rung is not None and gold is None:
        raise ValueError(f"oracle rung {rung.value} needs a gold index")

    def factory(idx: int) -> ServeWorker:
        conn = PostgresConnector(pg_dsn, schema=None)
        gw = Gateway(conn, max_rows=200_000, timeout_s=60.0)
        if rung is not None:
            slv = oracle_solver(
                rung,
                corpus,
                gw,
                settings,
                identity,
                model=model,
                embedder=embedder,
                gold=gold,
                session_id=f"eval-{rung.value}-w{idx}",
                graph_cache_max=max(4, 32 // max(1, n_workers)),
            )
        else:
            slv = agent_solver(
                corpus,
                gw,
                settings,
                identity,
                model=model,
                embedder=embedder,
                session_id=f"eval-{arm}-w{idx}",
            )
        return ServeWorker(connector=conn, gateway=gw, solver=slv)

    return factory


def _run_pool_arm(
    *,
    arm: str,
    solver,
    pairs: list[tuple[Any, str]],
    gold_hashes: dict[str, Any],
    gateway: Gateway,
    identity: Identity,
    bird_dir: Path,
    suspect_by_db: dict[str, frozenset[str]],
    # The corpus this arm served, for resolving ``tables_used`` asset ids to schemas —
    # ids cannot be split on ``_`` because schema names contain underscores.
    #
    # Required, not defaulted to ``None``. With a default, forgetting it at the driver
    # call site silently disabled the routing-escape metric for the whole run and left
    # the suite green: every row reported ``routing_escaped: None`` and the summary a
    # null rate, which reads as "nothing escaped" rather than "nothing was measured".
    # A missing argument is now a TypeError at the call, which is the loudest cheap
    # failure available.
    arm_corpus: Any,
    # Question ids whose gold SQL has a structural twin in train (``eval.leakage``).
    # Passed in rather than recomputed per arm: it is a property of the split, not of
    # the arm, and recomputing it would put a few thousand regex substitutions on the
    # critical path of every serve pass.
    # REQUIRED, both of them, deliberately without defaults. Each decides what is in
    # the EX denominator or which stratum a row lands in, so a silently-empty default
    # reads as "no twins, nothing excluded" — the absent-vs-zero failure this module
    # keeps finding, and one a forgetful call site would never be told about. Removing
    # ``ungradeable_ids=`` from the driver used to leave the whole suite green.
    # Callers that genuinely do not care pass ``frozenset()`` and say so.
    twin_ids: frozenset[str] | set[str],
    ungradeable_ids: frozenset[str] | set[str],
    dialect: str,
    out_path: Path,
    split: str = "test",
    resume: bool = False,
    # Keep crashed rows on resume instead of re-serving them. Off by default: a
    # crash is a bug rather than a measurement, and replaying one costs the run.
    replay_crashed: bool = False,
    serve_workers: int = 1,
    worker_factory: "Callable[[int], ServeWorker] | None" = None,
    stage_sink: "_RowSink | None" = None,
    # How many notes the corpus this arm served actually held. Passed through to
    # the treatment fingerprint so "held notes, injected none" is checkable; that
    # check is unreachable without it.
    corpus_note_assets: int | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Serve + grade one arm over the pooled (item, db_id) stream. Decoy touches
    use the item's OWN db suspect set; routing recall is scored from the router
    provenance returned in the per-question ``meta``.

    ``serve_workers == 1`` (default) runs the serial loop against the passed
    ``solver`` / ``gateway`` — byte-identical to the pre-concurrency path.
    ``serve_workers > 1`` fans the per-question ``solve+grade`` unit across a
    thread pool of ``worker_factory``-built workers (each its own unpinned
    connector + gateway + graph); freshly served rows reassemble in the original
    pair order, so rows and every aggregate match the serial run. (On resume the
    returned list is replayed rows first, then fresh ones — aggregation is
    order-independent, and the summary is what callers use.)

    Rows stream to ``out_path`` as they are scored. With ``resume=True`` the rows
    already there are replayed instead of re-served, so an interrupted multi-hour
    run continues where it stopped; the summary is computed over replayed and fresh
    rows alike, so it matches an uninterrupted run.

    ``stage_sink`` (when given) receives the per-stage timing records of every
    question served *in this attempt*. A replayed row is deliberately absent from it:
    it has no fresh timings, and synthesising one — or copying the row's total
    latency onto a stage — would put a fabricated number in the one file whose
    purpose is attributing time. So the stage file is a subset of the row file on a
    resumed run, joinable by ``(question_id, arm)``; a question re-served after a
    torn row write can appear there twice, which is why the row file stays the
    authority on what was scored.
    """
    pairs = list(pairs)
    wanted_ids = {str(item.question_id or item.question) for item, _ in pairs}

    done_rows: list[dict[str, Any]] = []
    if resume:
        on_disk = _read_rows(out_path)
        # Check the split across EVERY row on disk, before narrowing to this pool.
        # train and test question ids are disjoint, so filtering first would drop
        # the foreign-split rows and leave the guard permanently unreachable —
        # while the file itself silently accumulated two splits. A row that records
        # NO split counts as foreign too: rows written before the field existed are
        # of unknown split, and treating unknown as "matches whatever you asked for"
        # is what let a `--split train` resume append onto an old test file.
        stale = {r.get("split") for r in on_disk} - {split}
        if stale:
            raise RuntimeError(
                f"{out_path.name} holds rows from split(s) {sorted(map(str, stale))} "
                f"but this run is --split {split}. Resuming would mix splits into one "
                "file; use a different --out directory or drop --resume."
            )
        # Only replay rows belonging to THIS pool: a narrower --limit / --dbs must
        # not smuggle stale questions into the denominator. They stay in the file,
        # so the summary and the artifact can disagree — reported below.
        done_rows = [r for r in on_disk if str(r.get("question_id")) in wanted_ids]
        if len(on_disk) != len(done_rows):
            print(
                f"  [{arm}] {len(on_disk) - len(done_rows)} row(s) on disk fall "
                "outside this question pool; scored summary excludes them but the "
                "JSONL still contains them"
            )
    else:
        out_path.unlink(missing_ok=True)  # fresh run: never append to a stale file

    # A crashed row is not a measurement, and ``quotable()`` refuses any arm with a
    # non-zero crash rate, so a resume re-serves crashed rows rather than handing them
    # back. The stale row is deleted from the file first: two rows under one
    # ``question_id`` double-count in every denominator, and ``eval.analysis`` rejects
    # the file outright. Re-serving would otherwise launder the run back to quotable
    # (audit E1), so ``n_re_served`` goes into the arm summary and ``quotable()``
    # refuses any non-zero count — finishing the artifact is useful, quoting it is not.
    # ``--replay-crashed`` keeps the crashed rows and leaves ``crash_rate > 0``.
    n_re_served = 0
    if resume and not replay_crashed:
        crashed = [r for r in done_rows if classify_row(r)[0] is Outcome.crashed]
        if crashed:
            n_re_served = len(crashed)
            crashed_ids = {str(r.get("question_id")) for r in crashed}
            done_rows = [
                r for r in done_rows if str(r.get("question_id")) not in crashed_ids
            ]
            _write_jsonl(
                out_path,
                [
                    r
                    for r in _read_rows(out_path)
                    if str(r.get("question_id")) not in crashed_ids
                ],
            )
            print(
                f"  [{arm}] resume: re-serving {n_re_served} crashed turn(s) "
                "(recorded as n_re_served — run will not be quotable; pass "
                "--replay-crashed to keep the crashed rows instead)"
            )

    done_ids = {str(r.get("question_id")) for r in done_rows}
    todo = [p for p in pairs if str(p[0].question_id or p[0].question) not in done_ids]
    if done_rows:
        print(
            f"  [{arm}] resume: {len(done_rows)} scored, {len(todo)} to go "
            f"({len(pairs)} total)"
        )

    def _grade_one(
        pair: tuple[Any, str], *, solver, gateway
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Solve, grade and record ONE pooled ``(item, db)`` pair — crash-contained.

        The scoring body below guards only the solver call, and everything after it
        — grading, hash comparison, the ~150-field row, and the ``json.dumps`` that
        persists it — ran unguarded. ``run_ordered_pool`` re-raises task exceptions on
        purpose (absorbing them would turn a crashing arm into a merely-refusing one),
        the serve loop's enclosing block is a ``try``/``finally`` with no ``except``,
        and ``summary.json`` plus ``index_run`` come after it. So a single
        non-serialisable field, or any bug in one row's bookkeeping, took down a
        multi-hour run leaving no summary and no ledger entry at all — invisible to
        the ledger rather than flagged in it, which is worse than every reason
        ``quotable()`` can state.

        The third option the re-raise comment did not consider is the one the solver
        call already uses: catch, stamp ``Outcome.crashed``, keep going. A crashed row
        is still counted as a crash — ``classify_row`` prefers the stamped ``outcome``
        — so nothing is laundered into a refusal or a wrong answer.
        """
        item, db = pair
        try:
            return _grade_one_scored(pair, solver=solver, gateway=gateway)
        except Exception as err:
            detail = f"{type(err).__name__}: {err}"
            print(
                f"*** WARNING: grading {item.question_id or item.question!r} on {db!r} "
                f"raised after the solver returned ({detail}) — recorded as a crashed "
                "row so the run survives; the row's other fields are unmeasured ***"
            )
            traceback.print_exc()
            row = {
                "question_id": str(item.question_id or item.question),
                "db_id": db,
                "arm": arm,
                "split": split,
                "generated_sql": None,
                "correct": False,
                "correct_strict": False,
                "error": detail,
                "error_type": type(err).__name__,
                "difficulty": getattr(item, "difficulty", None) or "unknown",
                # Stamped, so no downstream reader re-derives this as a refusal.
                "outcome": Outcome.crashed.value,
                "failed_stage": None,
            }
            return row, []

    def _grade_one_scored(
        pair: tuple[Any, str], *, solver, gateway
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Solve + grade ONE pooled (item, db) pair against the given
        (solver, gateway), returning the row that is both persisted and aggregated,
        plus that question's per-stage timing records."""
        item, db = pair
        qid = item.question_id or item.question
        t0 = time.perf_counter()
        try:
            sql, meta_raw = solver.solve_with_meta(item.question)
            err_msg = None
        except Exception as err:  # one crashed question must not lose the run
            # Class name AND message. ``str(KeyError("schema"))`` is just "'schema'",
            # which names neither the failure kind nor the frame — and this string is
            # the only record of the crash that reaches the row.
            sql, meta_raw = None, {}
            err_msg = f"{type(err).__name__}: {err}"
        latency = time.perf_counter() - t0

        gold = gold_hashes.get(str(qid))
        grade = score_sql_hashes(sql, gold, gateway, identity, bird_dir)
        if err_msg and grade.get("error") in (None, "refusal"):
            grade["error"] = err_msg

        meta = dict(meta_raw or {})
        # Classified HERE, where a solver exception is still distinguishable from
        # everything else. ``grade["error"]`` holds a grader verdict, a solver crash
        # message, a model SQL fault (``exec_error:``), or an infrastructure failure
        # (``infra_error:``). Only the first of the gateway cases is a wrong answer;
        # infra failures must crash the turn so they cannot silently move accuracy
        # (audit E4). ``err_msg`` (solver) or the infra prefix (grader) is what gets
        # passed; the stamp is what every later reader uses instead of re-deriving.
        grade_err = grade.get("error")
        infra_msg = (
            grade_err
            if isinstance(grade_err, str) and grade_err.startswith(INFRA_ERROR_PREFIX)
            else None
        )
        outcome, failed_stage, refused_by_known = classify_outcome(
            generated_sql=sql,
            exception=err_msg or infra_msg,
            refused_by=meta.get("refused_by"),
            recursion_exhausted=meta.get("recursion_exhausted"),
        )
        if infra_msg and outcome is Outcome.crashed and failed_stage is None:
            failed_stage = Stage.execute
        if not refused_by_known:
            print(
                f"*** WARNING: unrecognised refused_by={meta.get('refused_by')!r} on "
                f"{qid} — counted in n_unmapped_refused_by, not attributed to a stage"
            )
        # ``.get(...)`` without an ``or []`` tail: absent and empty are different
        # facts here and the tail erased the difference. A turn that never reached
        # ``assemble`` records no ``routed_schemas`` at all, and coercing that to
        # ``[]`` made ``routed_hit=False`` — a routing MISS, indistinguishable from a
        # router that ran and picked wrong. The whole-split no-model ceiling (what
        # ``--oracle-only`` now runs) published ``routing_recall: 0.0`` over 2030 rows
        # on that path, for a router that was never invoked. Same for the shortlist:
        # coerced to ``[]`` it gave
        # every oracle row ``gold_schema_rank=None``, filing all 2030 under the
        # ``by_gold_rank["miss"]`` bucket whose documented meaning is "retrieval never
        # surfaced the schema" — a 100%-retrieval-failure reading at EX 1.0.
        routed = meta.get("routed_schemas")
        shortlisted = meta.get("shortlisted_schemas")
        pick = meta.get("schema_pick")
        # One definition, read by both the row's own ``routing_bypassed`` field and the
        # routing-escape verdict. They were spelled separately and the escape used the
        # narrow form, so a row could carry ``routing_bypassed=True`` beside a non-null
        # escape verdict — two fields disagreeing about whether the router ran.
        bypassed = bool(meta.get("routing_bypassed")) or (
            isinstance(meta.get("total_schemas"), int) and meta["total_schemas"] <= 1
        )
        used_schemas, unresolved_tables = _schema_of_assets(
            arm_corpus, meta.get("tables_used")
        )
        # ``escaped``, not ``routing_escaped``: the predicate now carries the
        # unprefixed name (it moved to ``eval.statistics``), and a local of the
        # same name would shadow it.
        escaped = routing_escaped(
            used_schemas,
            routed,
            bypassed=bypassed,
            unresolved_ids=unresolved_tables,
        )
        # Unknown escape: non-empty tables_used could not be fully resolved, and we
        # did not already prove an escape from the resolved subset. Distinct from
        # genuinely unobserved (empty/missing tables_used → routing_escaped None
        # without this flag).
        routing_escape_unknown = bool(
            unresolved_tables
            and not bypassed
            and meta.get("tables_used")
            and escaped is None
        )
        row = {
            "request_id": str(qid),
            "question_id": str(qid),
            "db_id": db,
            "arm": arm,
            "split": split,
            "generated_sql": sql,
            "latency_sec": round(latency, 4),
            "usage": meta.get("usage"),
            # Per-source token spend (router / agent_core / narrator / repair), so a
            # cost delta between arms is attributable to a stage rather than only
            # visible as a bigger total.
            "token_usage": meta.get("token_usage"),
            "cost_est_usd": meta.get("cost_est_usd"),
            "correct": grade["correct"],
            "correct_strict": grade["correct_strict"],
            "error": grade.get("error"),
            # Result shape: same row count + wrong hash is a projection /
            # ordering failure, a different count is a different answer.
            "pred_nrows": grade.get("pred_nrows"),
            "pred_ncols": grade.get("pred_ncols"),
            "gold_nrows": grade.get("gold_nrows"),
            "nrows_match": grade.get("nrows_match"),
            "difficulty": item.difficulty or "unknown",
            # Gold that is a literal VALUES(...) constant can never be matched;
            # flagged per row so ``ex_gradeable`` can exclude it by denominator.
            "gold_frozen": is_frozen_constant(item.sql),
            # Could the curator have answered this from train rather than generalised
            # to it? ``seeded`` derives its seed from train gold SQL and ``curated``
            # runs an agent over train, so on a question whose statement already exists
            # there — verbatim modulo literals — an EX gain is consistent with recall.
            # 246 of 2030 test questions (12.1%) qualify, up to 46% in one schema.
            "gold_twin_in_train": str(qid) in twin_ids,
            # The obfuscation repo ships these and its own note says to exclude them:
            # gold with LIMIT-without-total-order or a float aggregate returns a
            # different-but-VALID result, which hashes differently, so each was scored
            # wrong for every arm. Uniform across arms, so deltas were fine — but every
            # absolute EX was depressed, including the one read against the oracle_sql
            # ceiling. Excluded from ``ex_gradeable`` the same way frozen gold is.
            "gold_order_sensitive": str(qid) in ungradeable_ids,
            "routed_schemas": routed,
            # ``None`` when the turn recorded no routing decision at all — see the
            # ``routed``/``shortlisted`` note above. ``summarise_rows`` drops those
            # rows from the recall denominator and counts them in
            # ``n_routing_unrecorded``.
            "routed_hit": (db in routed) if routed is not None else None,
            # Did the answer actually use a schema the router excluded? The agent core is
            # built with the POOLED corpus (``agent.py``'s ``agent_core_node`` passes
            # ``corpus``, not the routed ``retrieval_corpus``), so ``search_corpus``
            # retrieves across every schema whatever the router decided. That is arguably
            # good for EX — the agent can recover from a routing miss — but it means the
            # router is not a gate, and therefore
            # ``EX = routing_recall x cond_ex_given_routing`` is not an identity.
            #
            # Resolved from ``tables_used`` — the tables in the SQL that was delivered —
            # via the arm's own corpus. NOT from ``licensed_tables``: that is the
            # assemble-time seed license computed from the *routed* corpus and never
            # amended, so it cannot contain an out-of-routed schema however far the agent
            # went, and a metric built on it scored a demonstrated escape as compliant.
            "tables_used": meta.get("tables_used"),
            "tables_used_unresolved": unresolved_tables or None,
            "n_tables_used_unresolved": len(unresolved_tables),
            "routing_escaped": escaped,
            "routing_escape_unknown": routing_escape_unknown,
            "shortlisted_schemas": shortlisted,
            # 1-based position of the TRUE schema in the relevance-ordered
            # shortlist, or None when retrieval never surfaced it at all.
            #
            # ``None`` is overloaded — it is also what an absent shortlist gives —
            # so ``rank_report`` reads ``shortlisted_schemas`` to tell the two
            # apart rather than bucketing both as a retrieval miss.
            "gold_schema_rank": (
                shortlisted.index(db) + 1
                if shortlisted is not None and db in shortlisted
                else None
            ),
            "schema_pick": pick,
            "schema_pick_fallback": meta.get("schema_pick_fallback"),
            "pick_hit": (pick == db) if pick is not None else None,
            "total_schemas": meta.get("total_schemas"),
            "retrieved_tables": meta.get("retrieved_tables"),
            "licensed_tables": meta.get("licensed_tables"),
            "injected_note_ids": meta.get("injected_note_ids"),
            "n_notes_injected": meta.get("n_notes_injected"),
            "n_few_shots_injected": meta.get("n_few_shots_injected"),
            "n_joins_injected": meta.get("n_joins_injected"),
            "n_metrics_injected": meta.get("n_metrics_injected"),
            "n_terms_injected": meta.get("n_terms_injected"),
            "n_caveats_injected": meta.get("n_caveats_injected"),
            "context_chars": meta.get("context_chars"),
            # Identity of the delivered context. ``eval.treatment`` compares these
            # across arms; without it, two arms that never actually differed still
            # produce different scores and read as a measured null result.
            "context_hash": meta.get("context_hash"),
            # Which counterfactual rung produced this row, if any. ``None`` on every
            # fair arm. This is the stamp that keeps an oracle number from being
            # read later as system performance: the rung reads the answer key, so a
            # row from one is a headroom bound and nothing else.
            "oracle_rung": meta.get("oracle_rung"),
            "oracle_applied": meta.get("oracle_applied"),
            # What the rung handed over, so the delta is inspectable. Compare
            # against a fair arm's `licensed_tables` for the same question: if
            # licensing already held every gold table, the rung removed no
            # selection error and its lift is something else.
            "oracle_gold_tables": meta.get("oracle_gold_tables"),
            "oracle_offered_tables": meta.get("oracle_offered_tables"),
            "oracle_corpus_tables": meta.get("oracle_corpus_tables"),
            "oracle_padding_degenerate": meta.get("oracle_padding_degenerate"),
            # True when the router never engaged, so its absence of provenance is
            # not a miss. Two ways that happens: an oracle rung pinned the corpus to
            # one schema, or the pool itself only holds one (``--limit-dbs 1``, or a
            # build that dropped everything else). Both leave `routed_hit=False` on
            # a row where routing was never asked a question, and the taxonomy would
            # otherwise charge every wrong answer in the run to the picker.
            # Set only on positive evidence that the router never engaged. The
            # earlier form, ``(total_schemas or 0) <= 1``, failed OPEN: a row that
            # never recorded ``total_schemas`` folded to 0, read as bypassed, and had
            # its genuine routing miss suppressed. Suppressing an error because a
            # field is missing is the same shape of defect as counting a crash as a
            # refusal, so an unrecorded count now means "not known to be bypassed"
            # and the miss is attributed.
            "routing_bypassed": bypassed,
            # Computed in the solver meta but previously dropped before the row.
            "attempts": meta.get("attempts"),
            # Prompt identity per row, from the serve path's own stamp. ``None``
            # when nothing served this row (the offline refuse-all path), which is
            # the honest value: no prompt was sent.
            "prompt_variants": meta.get("prompt_variants"),
            "prompt_set_hash": meta.get("prompt_set_hash"),
            "token_sum": meta.get("token_sum"),
            "run_id": meta.get("run_id"),
            "turn_id": meta.get("turn_id"),
            "decoy_touch": (
                sql is not None
                and _touches_suspect(sql, suspect_by_db.get(db, frozenset()), dialect)
            ),
            "refused_by": meta.get("refused_by"),
            # Which exception class produced a crash, so a rate-limit storm is
            # distinguishable from a defect at a glance rather than by re-reading logs.
            "error_type": meta.get("error_type"),
            "failed_layer": meta.get("failed_layer"),
            "graded_delivery": meta.get("graded_delivery"),
            "tier": meta.get("tier"),
            "semantic_assurance": meta.get("semantic_assurance"),
            # The safety axis of the two-axis stamp. Recorded here so the pooled
            # run — the one that produces the scale numbers — can report whether
            # guardrails cleared.
            "safety_clearance": meta.get("safety_clearance"),
            "coverage_best_effort": meta.get("coverage_best_effort"),
            # How the turn ended and where, in the one vocabulary the summary, the
            # offline analysis and the run ledger all read (``governed_bi.stages``).
            "outcome": outcome.value,
            "failed_stage": failed_stage.value if failed_stage is not None else None,
            # Also computed in the solver meta and previously dropped: the ledger
            # length, and the per-tool call counts that are the only record of
            # search_corpus / inspect_schema activity.
            "ledger_len": meta.get("ledger_len"),
            "governance_ledger": meta.get("governance_ledger"),
            "n_tool_calls": meta.get("n_tool_calls"),
            "by_guardrail_layer": meta.get("by_guardrail_layer"),
        }
        return row, _stage_event_rows(meta, question_id=str(qid), arm=arm, db_id=db)

    # Only touch the file when there is something to write: a fully-resumed arm
    # (or an arm with no questions) should not leave an empty generations file
    # that later reads back as an arm scored over zero rows.
    fresh: list[dict[str, Any]] = []
    if todo:
        sink = _RowSink(out_path)
        progress = _ServeProgress(arm=arm, total=len(todo))

        def _persist(scored: tuple[dict[str, Any], list[dict[str, Any]]]) -> None:
            """Flush one question's row and its stage records before the next starts."""
            row, stage_rows = scored
            sink.write(row)
            if stage_sink is not None:
                for stage_row in stage_rows:
                    stage_sink.write(stage_row)
            progress.tick()

        try:
            if serve_workers > 1:
                if worker_factory is None:
                    raise ValueError("serve_workers > 1 requires a worker_factory")
                scored = run_ordered_pool(
                    todo,
                    workers=serve_workers,
                    make_worker=worker_factory,
                    run_task=lambda w, pair: _grade_one(
                        pair, solver=w.solver, gateway=w.gateway
                    ),
                    on_result=_persist,
                )
                fresh = [row for row, _stage_rows in scored]
            else:
                for pair in todo:
                    row, stage_rows = _grade_one(pair, solver=solver, gateway=gateway)
                    _persist((row, stage_rows))
                    fresh.append(row)
        finally:
            sink.close()

    rows = [*done_rows, *fresh]
    # Gold SQL keyed by question id, so wrong answers can be attributed to a stage
    # and an error class. Built from the same ``pairs`` the arm was served with, so
    # a resumed run attributes exactly as an uninterrupted one does.
    gold_sql = {
        str(item.question_id or item.question): item.sql
        for item, _db in pairs
        if item.sql
    }
    summary = summarise_rows(
        arm, rows, gold=gold_sql, corpus_note_assets=corpus_note_assets
    )
    # Durable, not stdout-only: ``quotable()`` reads this from the arm summary in
    # ``summary.json``. Always present so absence cannot be confused with zero.
    summary["n_re_served"] = n_re_served
    return rows, summary


def run_datalake(
    *,
    bird_dir: Path,
    pg_dsn: str,
    out_dir: Path,
    # Where the arm corpora live. Defaults to ``out_dir``, which is the single-split
    # case. Split out so two scored splits can share ONE build: the curator is
    # stochastic, so rebuilding per split would make a train-vs-test gap a mix of
    # overfitting and curator variance — the confound the gap exists to measure.
    corpus_dir: Path | None = None,
    db_ids: list[str] | None = None,
    arms: tuple[str, ...] = _ARMS,
    limit_dbs: int | None = None,
    limit: int | None = None,
    # Tool-call budget per curator invoke. ``None`` (the default, and what an unset
    # ``--max-agent-steps`` gives) derives it per schema from that schema's size; an
    # explicit int is an operator override that caps cost for every schema alike.
    max_agent_steps: int | None = None,
    # Serve ONLY oracle rungs: no fair arm, no model load. The Option A replacement
    # for the retired ``skip_agent`` global bypass (M3 N10, decision 12) — "no model
    # was called" is now an INFERENCE from an empty fair-arm set, made once from
    # ``arms``/``oracles`` below, rather than a second flag a caller could set
    # inconsistently with the arms/oracles it actually asked for. When set, ``arms``
    # is forced empty and ``oracles`` defaults to ``(oracle_sql,)`` if the caller left
    # it empty too, so this always resolves to a servable, model-free scope.
    oracle_only: bool = False,
    resume: bool = True,
    split: str = "test",
    # Shortlist width. Widening recovers schemas the picker would otherwise never
    # see, at the cost of two more candidate summaries; measure recall@k with
    # ``eval.analysis`` (``by_gold_rank``) before changing it.
    route_top_k: int = 10,
    route_llm_pick: bool = True,
    use_embedder: bool = True,
    serve_workers: int = 1,
    # Per-db corpus builds to run concurrently. The dbs are independent (each
    # profiles its own Postgres schema through its own connector), so this is the
    # single biggest wall-clock lever on a scale run: 69 sequential deep-agent
    # curator passes become 69/N. Each build gets a private staging root because the
    # curator writes its sidecars at the arm root (see ``_promote_build``).
    build_workers: int = 1,
    # Gold rows sampled per schema by the pre-flight. More than one buys redundancy
    # against a single slow or genuinely-broken gold row: a schema counts as verified
    # when ANY sampled row executes and agrees, so raising this can only help.
    gold_per_db: int = 1,
    # Keep crashed rows on resume rather than re-serving them (see _run_pool_arm).
    replay_crashed: bool = False,
    prompt_variants: dict[str, str] | None = None,
    # ``None`` keeps whatever Settings says, so a caller that does not care about
    # the picker's column budget need not know the default.
    schema_pick_max_columns: int | None = None,
    # ADR 0003 PIN. False (the default) is the arm every prior run served: keyword
    # triggers authored into the corpus stay inert, and notes reach the prompt only by
    # landing in retrieval's semantic top-k. True turns the trigger channel on, which
    # changes TWO things — pinned note text, and the router's shortlist (a pinned
    # note's schema is prepended to it) — so it is a separate arm, not a variation of
    # ``curated_sme``. Reaches Settings through ``NoteGovernance``; recorded in the
    # manifest as a gate key, so the ledger stops comparing across it.
    pin_triggers: bool = False,
    # Serve this arm a second time as ``<arm>__replicate`` to measure the run's own
    # noise floor. Costs one extra serve pass and is the only way to know what the
    # run could resolve, because the proxy drops temperature and the sampling cannot
    # be pinned. Without it, comparisons report significance but not resolution.
    replicate_of: str | None = None,
    # Counterfactual rungs to serve alongside the fair arms (``oracle_sql``,
    # ``oracle_schema``, ``oracle_tables``). Each hands one stage the gold answer and
    # re-measures, so its lift IS that stage's headroom rather than an estimate
    # summed from per-class counts. Test-aware: diagnostics, never performance.
    oracles: tuple[str, ...] = (),
    # Adopt whatever is already complete under ``corpus_dir`` even when ``resume`` is
    # off. Set by ``--split both`` for the second split, and for nothing else.
    #
    # ``resume`` used to govern both halves at once, so ``--split both --no-resume``
    # re-ran the STOCHASTIC deep-agent curator into the shared roots between the two
    # passes: test scored against corpus v1, train against v2, and the gap that comes
    # out is a mix of overfitting and curator variance measuring neither (see
    # ``eval.split_gap``). It also paid for the build twice, which is the run's
    # dominant cost. The two halves are separable — this one is "is the treatment
    # already built", ``resume`` is "are these rows already scored" — and the second
    # must stay honest per split directory when an operator asks for a clean start.
    reuse_corpus: bool = False,
) -> dict[str, Any]:
    """Build all arms for the requested dbs into shared corpora, then serve the
    pooled split through the unpinned (data-lake) agentic core. Writes
    ``generations.<arm>.jsonl`` + ``stage_events.jsonl`` + ``summary.json`` +
    ``manifest.json`` under ``out_dir``, appends one record to the run ledger
    (``runs/index.jsonl``) on completion, and returns the summary dict.

    ``split="train"`` scores the very questions the curator read to build the
    ``curated`` / ``curated_sme`` corpora (few-shots, table descriptions and the
    SME brief all derive from them). That makes it a **diagnostic** — useful for
    measuring a routing or prompt change at higher sample size — and *not* a
    held-out result. The manifest records the split so a train number can never be
    mistaken for a test number later.

    ``prompt_variants`` (``stage -> variant``, empty = all ``v1``) selects
    registered prompt text per stage. It reaches the serve path through
    ``Settings``, so ``serve_config_hash`` and every stamped row move with it; the
    resolved map and its text hash go in the manifest, and the hash is a resume
    knob so a resume cannot mix two prompt sets into one arm's rows.
    """
    if split not in _SPLITS:
        raise ValueError(f"split must be one of {_SPLITS}, got {split!r}")
    if oracle_only:
        # Ignore any fair arms the caller passed rather than erroring: the CLI
        # refuses ``--arms`` alongside ``--oracle-only`` up front, but this is a
        # library entry point too (tests call it directly), and forcing the scope
        # here is what makes "no model was called" true BY CONSTRUCTION rather than
        # by a caller remembering to pass ``arms=()`` itself.
        #
        # Resume safety rides on the same line. The retired ``--skip-agent`` path
        # wrote fair-arm ``generations.*.jsonl`` rows that were construction-
        # refusals scoring 0; resume REPLAYS those rows rather than re-serving
        # them, so hours of live model calls could land on a permanently poisoned
        # denominator (see the docstring that used to live on
        # ``test_resuming_a_skip_agent_directory_with_a_model_is_fatal``). With
        # ``arms = ()`` here, an ``--oracle-only`` run never emits fair-arm
        # generation rows at all — there is nothing to replay — so that hazard is
        # structurally impossible. If a future change lets ``--oracle-only`` write
        # placeholder fair rows, restore an explicit resume guard; the two facts
        # must stay linked.
        arms = ()
        if not oracles:
            oracles = (OracleRung.sql.value,)
    # Computed once, here, because the corpora-loading section below and the serve
    # loop both need to know which arm's corpus an oracle rung narrows. A rung
    # measures headroom *relative to* one arm's corpus — the last requested arm under
    # the default ordering — but ``--oracle-only`` forces ``arms`` empty, so it falls
    # back to ``baseline``: that arm is always built regardless of what ``arms``
    # names (see ``_build_db_corpora``), so a rung always has a corpus to narrow even
    # when nothing else is being served. Outside ``oracle_only``, an empty ``arms``
    # alongside a requested rung is still refused — that combination was never valid
    # and this fallback exists for the one flag that makes it a real scope, not a
    # blanket "no arms, no problem".
    oracle_rungs = {r.value: r for r in OracleRung if r.value in (oracles or ())}
    oracle_base = arms[-1] if arms else (_ARMS[0] if (oracle_only and oracle_rungs) else None)
    if oracle_rungs and oracle_base is None:
        raise ValueError("--oracle needs at least one arm to measure against")
    # Resolve (and validate) before touching Postgres or a corpus: a bad stage or
    # variant must cost nothing, and the resolved map is what gets recorded.
    resolved_prompts = resolve_prompts(prompt_variants)
    load_dotenv()
    dataset_dir = bird_dir / "eval_dataset"
    out_dir.mkdir(parents=True, exist_ok=True)
    from ..logging_setup import configure_logging

    configure_logging(log_path=out_dir / "run.log")
    corpus_dir = out_dir if corpus_dir is None else corpus_dir
    corpus_dir.mkdir(parents=True, exist_ok=True)
    roots = {arm: corpus_dir / f"corpus_{arm}" for arm in _ARMS}
    if split == "train":
        print(
            "\n*** NOTE: --split train scores the questions the curator was BUILT "
            "from (few-shots, descriptions, SME brief all derive from them). Treat "
            "these numbers as a diagnostic, never as a held-out result. ***\n"
        )

    # --- resolve the db set: requested (or every db in the split), on Postgres ---
    probe = PostgresConnector(pg_dsn, schema=None)
    try:
        present = set(probe.list_schemas())
    finally:
        probe.close()
    wanted = db_ids if db_ids is not None else sorted(available_dbs(dataset_dir, split))
    if limit_dbs is not None:
        wanted = wanted[:limit_dbs]
    # Requested but not loaded. This is a *third* kind of attrition, distinct from the
    # two that already have gates: a schema absent from Postgres never enters ``wanted``,
    # so neither the build-coverage check nor the gold share can see it — both measure
    # against ``wanted``, which is already filtered here. Until now it produced one
    # truncated print and nothing durable, so a default run against a partially-loaded
    # Postgres scored 40 of 69 schemas and reported full coverage of what it attempted.
    # Recorded and carried into the ledger, where it blocks quoting: a 40-schema result
    # is not the 69-schema benchmark, whatever its internal consistency.
    n_requested = len(wanted)
    missing = [d for d in wanted if d not in present]
    if missing:
        print(
            f"*** WARNING: {len(missing)} of {n_requested} requested db(s) are not on "
            f"Postgres and will be skipped: {sorted(missing)[:10]}"
            + (f" (+{len(missing) - 10} more)" if len(missing) > 10 else "")
            + " — the pool scored is smaller than the pool requested, and this blocks "
            "quotability ***"
        )
    wanted = [d for d in wanted if d in present]
    if not wanted:
        raise RuntimeError("no requested db_ids are loaded on the Postgres instance")

    # --- Settings + clients (built once; models resolved from config) ---
    base_settings = load_settings()
    datasource = DataSourceConfig(
        kind="postgres", corpus_pin="datalake", schema=None, dsn=pg_dsn
    )
    settings = Settings.for_env(
        Environment.dev,
        models=base_settings.models,
        datasource=datasource,
        corpus_root=str(corpus_dir),
        # ADR 0003 note governance. Carried across the rebuild instead of left at the
        # dataclass defaults: this Settings replaces the one ``load_settings`` just
        # produced, so without it the ``[notes]`` table configures every deployment
        # except the one that measures them, and ``--pin-triggers`` has nowhere to land.
        notes=NoteGovernance.from_settings(base_settings, pin_triggers=pin_triggers),
    )
    # D5 (deliver-and-grade semantic failures); D15 routing knobs.
    settings = replace(
        settings,
        hard_block_suspect_columns=False,
        grade_semantic_failures=True,
        schema_route_top_k=route_top_k,
        schema_route_llm_pick=route_llm_pick,
        # ``None`` keeps the Settings default. Without this the knob was recorded in
        # the manifest, guarded on resume, and used as a comparability key while
        # being permanently 12 — three guards on a value nothing could change.
        schema_pick_max_columns=(
            settings.schema_pick_max_columns
            if schema_pick_max_columns is None
            else schema_pick_max_columns
        ),
        # The full resolved map, not just the overrides: every consumer downstream
        # (graph build, config hash, per-row stamp) then reads one description of
        # what this run sends instead of re-deriving the defaults itself.
        prompt_variants=resolved_prompts,
    )

    chat_client = None
    lc_model = None
    embedder = None
    # A model is needed to serve a fair arm, or an oracle rung other than
    # ``oracle_sql`` — the one rung that submits gold SQL straight to the grader and
    # never touches the graph. This is an INFERENCE from ``arms``/``oracles``, not a
    # flag: the retired ``skip_agent`` was a global bypass that could combine with any
    # configuration (the exact hazard Option A / M3 N10 / decision 12 retires it
    # over), so "no model was called" is decided here, once, from the scope that was
    # actually requested rather than restated by the caller.
    needs_model = bool(arms) or bool(set(oracles) - {OracleRung.sql.value})
    if needs_model:
        from ..llm import LangChainChatClient, LangChainEmbedder

        chat_client = LangChainChatClient.from_config(settings.models)
        lc_model = chat_client.model
        if use_embedder:
            embedder = LangChainEmbedder.from_config(settings.models)

    # Every knob that changes what a scored row MEANS. Written before any work so
    # a crashed run still leaves one to validate a later --resume against; the same
    # dict is re-written at the end. (A manifest that only appeared on success
    # would be missing in exactly the case resume exists for.)
    # Scope hash over the effective pool AFTER ``limit_dbs`` and Postgres filtering,
    # so a resume that changes caps fails before build/serve spend.
    scope_pairs = _pooled_items(dataset_dir, wanted, limit=limit, split=split)
    manifest = _build_manifest(
        bird_dir=bird_dir,
        split=split,
        # ``None`` verbatim when this run needs no model — decided above, once, from
        # ``arms``/``oracles`` — never the configured name restated regardless of
        # whether a model was actually loaded.
        model_name=settings.models.llm_model if needs_model else None,
        prompt_variants=resolved_prompts,
        route_top_k=route_top_k,
        route_llm_pick=route_llm_pick,
        schema_pick_max_columns=settings.schema_pick_max_columns,
        use_embedder=bool(embedder),
        # Off ``settings``, i.e. what the serve path will actually read: the CLI flag is
        # only one of the three inputs (flag, ``[notes]`` TOML, dataclass default).
        always_note_global_max=settings.always_note_global_max,
        always_note_char_max=settings.always_note_char_max,
        pin_triggers_enabled=settings.pin_triggers_enabled,
        pin_require_certified=settings.pin_require_certified,
        pin_max=settings.pin_max,
        # Off ``settings`` for the same reason as the note knobs: this driver forces the
        # shipped default off a few lines above, and the manifest has to record the policy
        # the serve path will read rather than the literal someone typed there.
        grade_semantic_failures=settings.grade_semantic_failures,
        serve_workers=serve_workers,
        build_workers=build_workers,
        arms=arms,
        oracles=oracles,
        replicate_of=replicate_of,
        db_ids=db_ids,
        limit=limit,
        limit_dbs=limit_dbs,
        question_scope_hash=_question_scope_hash(scope_pairs),
        # Same rows as the scope hash, plus the gold each is graded against. Both come
        # off ``scope_pairs``, which is already in memory, so the dataset is not read
        # again for either.
        question_pool_hash=metrics.question_pool_hash(
            (db, item.question_id or item.question, item.sql)
            for item, db in scope_pairs
        ),
        llm_temperature=settings.models.llm_temperature,
    )
    if resume:
        _check_resume_manifest(out_dir, manifest)
        manifest = _merge_resume_manifest(_read_manifest(out_dir), manifest)
    metrics.write_manifest(out_dir, manifest)

    # --- GOLD PRE-FLIGHT, before the build phase spends anything on a model ---
    # Needs only Postgres and the split files, never a corpus, and costs seconds over
    # the full split — the cheapest place to learn the grader cannot be trusted. Runs
    # here, not after the builds, or a wrong DSN aborts a run that already paid for a
    # curator pass and an SME round on every schema.
    #
    # Sampled over ``wanted`` because nothing is built yet; ``built`` is a subset, and
    # the post-build check re-runs over the exact scored rows anyway.
    if replicate_of and replicate_of not in arms:
        raise ValueError(
            f"--replicate {replicate_of!r} is not one of the arms being run "
            f"({', '.join(arms)}); there is nothing to replicate"
        )

    preflight_identity = Identity(user="eval", all_access=True)
    preflight_gold: dict[str, Any] = {}
    for db in wanted:
        preflight_gold.update(load_gold_hashes(bird_dir, db_id=db, split=split))
    print(f"  gold pre-flight over {len(wanted)} schema(s)...")
    _assert_gold_is_trustworthy(
        _datalake_gold_selfcheck(
            _pooled_items(dataset_dir, wanted, limit=limit, split=split),
            preflight_gold,
            pg_dsn,
            preflight_identity,
            per_db=gold_per_db,
        ),
        n_schemas=len(wanted),
    )

    # --- BUILD phase (per-db) ---
    # The dominant wall-clock cost of a scale run, and until now fully serial: one
    # deep-agent curator pass per db per arm, 69 dbs deep. The dbs are independent —
    # each profiles its own Postgres schema through its own connector — so the only
    # thing that made this serial was the shared arm root (see ``_promote_build``).
    built: list[str] = []
    build_errors: dict[str, str] = {}
    build_lock = threading.Lock()
    # Inside the run directory, not a system temp dir: a build that dies partway
    # leaves its staging root next to the run it belongs to, where it can be
    # inspected, rather than somewhere the operator has to be told about.
    staging_root = corpus_dir / "_staging"
    build_workers = resolve_workers(build_workers)

    # "Is the treatment already built" — deliberately NOT ``resume``, which answers
    # "are these rows already scored". See ``reuse_corpus`` in the signature.
    build_resume = resume or reuse_corpus
    if reuse_corpus and not resume:
        print("  corpus: reusing the build already under corpus_dir (--split both)")

    built = run_build_phase(
        wanted,
        roots=roots,
        staging_root=staging_root,
        build_workers=build_workers,
        resume=build_resume,
        build_errors=build_errors,
        build_lock=build_lock,
        build_one_db=lambda db, build_roots: _build_db_corpora(
            db_id=db,
            pg_dsn=pg_dsn,
            bird_dir=bird_dir,
            roots=build_roots,
            arms=arms,
            chat_client=chat_client,
            lc_model=lc_model,
            max_agent_steps=max_agent_steps,
            resume=build_resume,
            prompt_variants=resolved_prompts,
            settings=settings,
        ),
    )
    _assert_build_coverage(built, wanted, build_errors)

    # Lift swallowed curator build errors per db. The pooled driver relocates each db's
    # run_manifest.json into <root>/<db>/_build/, so the single-schema root reader never
    # sees them — read the relocated location.
    #
    # Read HERE, immediately after the build and before the pool is fixed, rather than
    # after the corpora are loaded where it used to sit. The information was already in
    # hand before a single serve dollar was spent and was used for a warning only, so 13
    # partial corpora out of 55 were served and scored anyway (see
    # ``_quarantine_curator_failures``). Nothing writes into ``_build/`` between the
    # build phase and here, so moving it earlier reads the same files.
    curator_errors: dict[str, dict] = {}
    for db in built:
        # EVERY arm that ran, not a hardcoded pair. This listed only
        # ``curated``/``curated_sme``, which was complete when those were the only
        # arms that invoked the curator — and silently stopped being complete the
        # moment ``seeded`` was added. A swallowed curator error, or an
        # unpromoted-diagnostic marker, on a newly added arm was invisible to
        # ``summary.json`` and therefore to ``quotable()``.
        #
        # ``baseline`` writes a manifest too and never runs an agent, so it simply has
        # nothing to report; including it costs one missing-file check and removes the
        # need to keep this list in step with the ladder.
        errs = _collect_curator_errors(
            {arm: roots[arm] / db / "_build" for arm in arms}
        )
        if errs:
            curator_errors[db] = errs
            for arm, block in errs.items():
                print(
                    f"\n*** WARNING: curator error on {db!r}/{arm} was swallowed "
                    f"during build: error={block['error']!r} "
                    f"fix_pass_error={block['fix_pass_error']!r} ***"
                )
    # ``built`` becomes the pool that is served and scored, so every derivation below it
    # — questions, gold, corpora, the router's index, the census — narrows with it in one
    # place instead of each remembering to filter.
    #
    # Resume-safe in both directions. Re-inclusion: the ``run_manifest.json`` that
    # records the error is promoted next to the corpus and a resumed build adopts the
    # completed tree rather than re-running the curator, so the same schema is withheld
    # again — and if a rebuild genuinely succeeds, the error is gone from the file and
    # the schema returns, which is a decision the artifact records rather than hides.
    # Double-counting: rows for a withheld schema that a pre-quarantine invocation left
    # in ``generations.<arm>.jsonl`` are outside ``wanted_ids`` in ``_run_pool_arm``, so
    # they are excluded from the summary and reported as out-of-pool instead of being
    # replayed into the denominator. The manifest's ``question_scope_hash`` /
    # ``question_pool_hash`` are computed over ``wanted`` before the build, so a
    # quarantine does not move them and cannot make a legitimate resume look like a
    # scope change.
    built_ok, quarantined_dbs = _quarantine_curator_failures(
        built, curator_errors, n_requested=len(wanted)
    )
    n_built = len(built)
    built = built_ok

    # --- POOL gold + test + per-db suspects (only successfully-built dbs) ---
    leakage = _assert_train_test_disjoint(dataset_dir, built)
    # The FINE form of leakage the id check cannot see: a scored question whose gold
    # statement already exists in train, modulo literals. Not a gate — twins are a
    # property of the benchmark, and refusing to score them would discard an eighth of
    # the split and change the denominator every published BIRD number uses. Reported,
    # stamped per row, and given its own EX stratum so the defensible headline (the
    # twin-free stratum) can be stated separately from the recall-flavoured one.
    pairs = _pooled_items(dataset_dir, built, limit=limit, split=split)
    # Zero-question schemas after rescreening (eval-rebuild §4): still in ``built``
    # because the curator ran, but they contribute no graded rows. Drop them before
    # corpora / census / routing, or they look built-but-unscored and inflate the
    # pool census against a denominator that never includes them.
    built, zero_question_dbs = _quarantine_zero_question_schemas(built, pairs)
    # The dataset's OWN exclusion list, which nothing here read. Its note says to
    # exclude these from cross-variant EX; 25 of the 2030 test questions qualify and
    # each was scored wrong for every arm, depressing every absolute EX including the
    # one read against the oracle_sql ceiling.
    _excl = ungradeable_question_ids(dataset_dir)
    ungradeable_ids = frozenset().union(*_excl.values()) if _excl else frozenset()
    leakage["dataset_ungradeable"] = {
        "source": "order_sensitive_qids.json",
        "by_reason": {k: len(v) for k, v in sorted(_excl.items())},
        "n_in_pool": sum(
            1 for _item, _db in pairs if str(_item.question_id) in ungradeable_ids
        ),
        "file_present": bool(_excl),
    }
    if not _excl:
        print(
            "  *** WARNING: order_sensitive_qids.json not found — the dataset's own EX "
            "exclusions are not being applied ***"
        )
    # Restricted to the questions this run actually SCORED. Computed over every test
    # row of the built schemas, ``twin_rate`` described the dataset while sitting in the
    # artifact beside numbers that describe the run — on the smoke command
    # (``--limit-dbs 3 --limit 5``) that is 15 scored questions and a rate over several
    # hundred.
    # Minus the dataset's own exclusions too, so the twin rate and the strata it
    # labels share ONE denominator. Filtering only frozen gold left the quoted rate
    # over 1652 rows while ``ex_no_twin``/``ex_twin`` used 1627 — the same
    # different-populations mismatch this filter was added to remove, 25 rows smaller.
    _scored_ids = {
        str(item.question_id)
        for item, _db in pairs
        if str(item.question_id) not in ungradeable_ids
    }
    twins = twin_report(dataset_dir, built, split=split, only_ids=_scored_ids)
    twin_ids = twins.pop("twin_ids")
    leakage["structural_gold_twins"] = twins
    print(
        f"  structural gold twins: {twins['n_twin']}/{twins['n_scored']} gradeable "
        f"question(s) have a same-schema train twin"
        + (f" — worst: {', '.join(twins['worst_dbs'][:3])}" if twins["worst_dbs"] else "")
    )

    # ``question_id`` is the key gold hashes are pooled under AND the resume dedup
    # key, so a collision across dbs would score one db's question against another
    # db's gold and silently dedup two questions into one. Globally unique in today's
    # dataset; a regeneration that broke that would otherwise be invisible.
    counts: dict[str, int] = {}
    for item, _db in pairs:
        key = str(item.question_id or item.question)
        counts[key] = counts.get(key, 0) + 1
    collisions = sorted(k for k, c in counts.items() if c > 1)
    if collisions:
        raise RuntimeError(
            f"{len(collisions)} question_id(s) appear in more than one pooled db "
            f"(e.g. {collisions[:5]}): gold association and resume dedup key on it."
        )

    # Question text + gold SQL as a side-car (not inlined into generations rows).
    # Analysis tools join this at read time; old runs without it fall back to BIRD.
    write_questions_sidecar(
        out_dir,
        [
            {
                "question_id": item.question_id,
                "db_id": db,
                "question": item.question,
                "gold_sql": item.sql,
                "evidence": item.evidence,
                "difficulty": item.difficulty,
                "split": split,
            }
            for item, db in pairs
        ],
    )
    gold_hashes: dict[str, Any] = {}
    suspect_by_db: dict[str, frozenset[str]] = {}
    # Which dbs actually had a trap manifest to load. ``load_trap_columns`` reports
    # this precisely so a missing manifest cannot read as a trap-free db, but the
    # flag is only worth carrying if something records it: without this, a
    # ``decoy_touch_rate`` of 0.0 across an arm is indistinguishable from having
    # measured no traps at all.
    trap_manifest_missing: list[str] = []
    for db in built:
        gold_hashes.update(load_gold_hashes(bird_dir, db_id=db, split=split))
        trap = load_trap_columns(bird_dir, db)
        if not getattr(trap, "manifest_present", True):
            trap_manifest_missing.append(db)
        suspect_by_db[db] = _suspect_from_corpus(roots["baseline"], db) | trap
    if trap_manifest_missing:
        print(
            f"*** WARNING: no trap manifest for {', '.join(trap_manifest_missing)} — "
            "decoy_touch_rate for their questions counts only corpus-flagged suspects"
        )

    # --- SERVE phase: ONE unpinned connector spans every schema ---
    connector = PostgresConnector(pg_dsn, schema=None)
    gateway = Gateway(connector, max_rows=200_000, timeout_s=60.0)
    identity = Identity(user="eval", all_access=True)

    # Re-run over the *scored* pool, which is a subset of what the pre-flight above
    # already cleared. Cheap (~40 ms per row per schema) and it keeps the assertion
    # attached to the exact rows about to be graded.
    gold_check = _datalake_gold_selfcheck(
        pairs, gold_hashes, pg_dsn, identity, per_db=gold_per_db
    )
    _assert_gold_is_trustworthy(
        gold_check, n_schemas=len(wanted), on_abort=connector.close
    )

    # Load each requested arm's corpus for exactly the dbs being scored — NOT
    # whatever the shared root happens to hold (see :func:`_load_built_corpus`). Plus
    # ``oracle_base``, even when it is not itself a requested arm: ``--oracle-only``
    # serves a rung narrowed from ``baseline`` with ``arms == ()``, and a rung with no
    # corpus to narrow is not a rung.
    arms_with_oracle_base = tuple(
        dict.fromkeys([*arms, *([oracle_base] if oracle_base is not None else [])])
    )
    corpora = {arm: _load_built_corpus(roots[arm], built) for arm in arms_with_oracle_base}
    # Amend the manifest with the digest of what was actually built. The manifest is
    # written before the build (the gold pre-flight has to run before anything is
    # spent on a model), so the corpus hash — the identity of the *treatment* — can
    # only be recorded here. Without it two runs over different curator draws
    # compared as if comparable (AUDIT E5).
    observed_corpus_hash = metrics.stamp_corpus_hashes(
        manifest, {arm: roots[arm] for arm in sorted(arms_with_oracle_base)}
    )
    # Already filled by ``stamp_corpus_hashes`` when this run declared none, so it is
    # never ``None`` here — re-implementing that fill in the caller only created a
    # second place for the two to disagree.
    prior_hash = manifest.get("corpus_content_hash")
    if prior_hash != observed_corpus_hash:
        # A resume whose corpus is not the corpus the run started on. Leave the
        # original in place so the ledger's comparability/drift check sees the
        # mismatch instead of having it overwritten out of existence.
        print(
            f"*** corpus content changed since this run started "
            f"({prior_hash} -> {observed_corpus_hash}); rows from the two builds are "
            "not the same experiment"
        )
    metrics.write_manifest(out_dir, manifest)
    corpus_validation = _validate_corpora(corpora)  # no connector: public-default
    _warn_if_not_green(corpus_validation)
    # Serve gets the Analyst view (D6): a governance.excluded asset must reach
    # neither SQL-gen nor the schema-routing index. Validation above and the census
    # below deliberately keep the full corpus — the C5 note scan needs the excluded
    # identifiers to check prose against, and the census counts what was excluded.
    corpora_serve = {arm: corpora[arm].for_analyst() for arm in arms_with_oracle_base}

    # Census the independent variable. An arm-to-arm EX delta is uninterpretable
    # without knowing what the higher arm actually added, and a rung that added
    # nothing is not evidence that its layer does not work. Only over ``arms`` (not
    # ``arms_with_oracle_base``): ``oracle_base`` is loaded to serve, not to be
    # reported as a rung of its own when it was not actually requested.
    corpus_census_by_arm = {arm: corpus_census(corpora[arm]) for arm in arms}
    census_deltas: dict[str, dict[str, Any]] = {}
    for lo, hi in ladder_steps(corpus_census_by_arm):
        delta = census_delta(corpus_census_by_arm[lo], corpus_census_by_arm[hi])
        census_deltas[f"{hi}_minus_{lo}"] = delta
        if not delta:
            print(
                f"\n*** WARNING: {hi} corpus is numerically identical to {lo} — "
                "it added no assets, so any EX difference between them is noise, "
                "not a measurement of that layer ***\n"
            )

    # SME no-op signals per db: flag any db whose curated_sme ended byte-identical to
    # curated (no real fold). Over the pool that will actually be SERVED — a withheld
    # schema's fold cannot affect an EX nobody computes for it, and naming it in the
    # ledger's ``sme_noop_dbs`` would report a defect in a measurement that was never
    # taken.
    sme_fold: dict[str, dict] = {}
    if "curated" in arms and "curated_sme" in arms:
        for db in built:
            fold = _sme_fold_signal(roots["curated"], roots["curated_sme"], db)
            sme_fold[db] = fold
            _warn_if_sme_noop(fold, db_id=db)

    # Serve concurrency (docs/measurement.md): only fan out when
    # there is a live model. The refuse-all path returns without work, so there is
    # nothing to overlap. The one exception is ``oracle_sql``, which executes real
    # gold SQL under ``--oracle-only`` and so *would* overlap — left serial on purpose:
    # the whole 2030-question split grades in well under ten minutes, once, and it is
    # the run every other number is read against. Not worth a concurrency bug.
    effective_workers = serve_workers if lc_model is not None else 1
    if effective_workers > 1:
        print(
            f"  serve concurrency: {effective_workers} worker(s)/arm — each owns "
            f"its own unpinned connector+gateway+graph (schema=None)"
        )



    # One stage-event file spans every arm (each record carries its own ``arm``), so
    # it is cleared once here rather than per arm — clearing it inside the arm loop
    # would delete the first arm's records while writing the second's.
    stage_events_path = out_dir / "stage_events.jsonl"
    if not resume:
        stage_events_path.unlink(missing_ok=True)
    stage_sink = _RowSink(stage_events_path)

    summaries: dict[str, Any] = {}
    rows_by_arm: dict[str, list[dict[str, Any]]] = {}
    try:
        # The replicate is the same corpus served a second time under a distinct
        # arm name. It is the only way to measure this pipeline's noise, because the
        # proxy drops the temperature parameter and the sampling cannot be pinned.
        # Appended last so a run that dies partway still has its real arms scored.
        serve_order = list(arms)
        # Oracle rungs append after the fair arms: they are diagnostics, and a run
        # that dies partway should still have scored the arms that are results.
        # ``oracle_rungs`` / ``oracle_base`` were resolved once, above, because the
        # corpora-loading section needed them before this loop exists.
        # Built before any serving so an ambiguous gold aborts the run up front,
        # rather than after a rung has already spent its model budget. Only five
        # BIRD questions collide by text and all of them share gold SQL, so this is
        # a guard against a future dataset change, not a live hazard.
        oracle_gold = (
            GoldIndex.build(
                [
                    {
                        "question": item.question,
                        "sql_rename": item.sql,
                        "question_id": item.question_id,
                        "db_id": db,
                    }
                    for item, db in pairs
                ]
            )
            if oracle_rungs
            else None
        )
        serve_order.extend(oracle_rungs)
        if replicate_of:
            # Validated before the build phase, above.
            serve_order.append(f"{replicate_of}__replicate")

        for arm in serve_order:
            # A replicate serves its source arm's corpus; only the name differs, and
            # that is the point — any disagreement between them is pure noise.
            source_arm = arm[: -len("__replicate")] if arm.endswith("__replicate") else arm
            rung = oracle_rungs.get(arm)
            # Which corpus this arm actually serves. A replicate serves its source
            # arm's; an oracle rung serves the base arm's, narrowed per question.
            # The rung's own name is never a corpus key, so anything looking one up
            # has to go through this rather than through ``source_arm``.
            plan = plan_arm_serving(
                rung=rung,
                source_arm=source_arm,
                oracle_base=oracle_base,
                effective_workers=effective_workers,
                has_model=lc_model is not None,
            )
            served_corpus_arm = plan.corpus_arm

            # ONE construction, read by both the serial solver and the per-worker
            # factory. They used to decide independently — the serial branch keyed on
            # ``oracle_base`` and the parallel one on the plan — so a change to either
            # could silently serve the two paths different corpora under one arm name.
            # ``plan`` is the single answer to "what is this arm", and both paths take
            # it whole rather than picking pieces out of it.
            #
            # ``oracle_sql`` is the exception to the model requirement: it submits
            # gold SQL straight to the grader and never calls a model, so gating it
            # behind ``lc_model`` made the one rung that costs nothing silently
            # degrade to refuse-all under a no-model run (what ``--oracle-only`` now
            # runs) — reporting EX 0.000 for the grader ceiling, which is the number
            # every other number is supposed to be read against. The other rungs do
            # serve through the real graph and genuinely need a model.
            def _solver_for(plan: ArmServingPlan):
                """The serial solver. The pool builds its own, per worker, via
                ``make_serve_worker_factory`` — same branch, same plan."""
                if plan.rung is not None and (
                    lc_model is not None or plan.rung is OracleRung.sql
                ):
                    # A counterfactual rung: same serve path, corpus narrowed toward
                    # the gold answer. Diagnostic only — it reads the answer key, so
                    # its number is a headroom bound and never system performance.
                    return oracle_solver(
                        plan.rung,
                        corpora_serve[plan.corpus_arm],
                        gateway,
                        settings,
                        identity,
                        model=lc_model,
                        embedder=embedder,
                        gold=oracle_gold,
                        session_id=f"eval-{arm}",
                    )
                if lc_model is not None:
                    return agent_solver(
                        corpora_serve[plan.corpus_arm],
                        gateway,
                        settings,
                        identity,
                        model=lc_model,
                        embedder=embedder,
                        session_id=f"eval-{arm}",
                    )
                return _RefuseAllSolver()

            solver = _solver_for(plan)
            # Oracle rungs parallelise too — each worker gets its own solver, and the
            # graph cache that was cited as the blocker is closure-local to one. The
            # rung serves the BASE arm's corpus (``served_corpus_arm``), narrowed per
            # question inside the solver, so the factory is keyed on that and not on
            # ``source_arm``: the rung's own name is never a corpus key.
            worker_factory = (
                arm_worker_factory(
                    plan,
                    ServeBindings(
                        corpora_serve=corpora_serve,
                        pg_dsn=pg_dsn,
                        settings=settings,
                        identity=identity,
                        model=lc_model,
                        embedder=embedder,
                        gold=oracle_gold,
                    ),
                )
                if plan.needs_factory
                else None
            )
            arm_workers = plan.n_workers
            arm_started = time.time()
            _rows, summary = _run_pool_arm(
                arm=arm,
                solver=solver,
                pairs=pairs,
                gold_hashes=gold_hashes,
                gateway=gateway,
                identity=identity,
                bird_dir=bird_dir,
                suspect_by_db=suspect_by_db,
                # The corpus this arm served, for resolving ``tables_used`` ids to schemas.
                # The base corpus rather than ``corpora_serve``: ``for_analyst()`` is a
                # projection for the prompt, and the id->schema mapping must come from the
                # same object the ids were minted against.
                arm_corpus=corpora[served_corpus_arm],
                twin_ids=twin_ids,
                ungradeable_ids=ungradeable_ids,
                dialect="postgres",
                out_path=out_dir / f"generations.{arm}.jsonl",
                split=split,
                resume=resume,
                replay_crashed=replay_crashed,
                serve_workers=arm_workers,
                worker_factory=worker_factory,
                stage_sink=stage_sink,
                # Counted off the corpus actually served (post-`for_analyst`, so
                # excluded assets are already gone), which is the honest
                # denominator for "held notes and injected none".
                corpus_note_assets=sum(
                    1
                    for a in corpora_serve[served_corpus_arm].assets
                    if isinstance(a, NoteAsset)
                ),
            )
            # Where this arm sat in wall-clock time. Arms serve sequentially, hours
            # apart on a scale run, against a hosted provider — so any drift in provider
            # behaviour maps monotonically onto the ladder and is indistinguishable from a
            # rung's effect. Interleaving arms per question would remove the confound, but
            # it would restructure the serve loop, the per-arm generations files and the
            # resume contract; recording the position makes the confound *detectable*
            # instead, which is the cheap half.
            #
            # Read it by checking whether EX tracks ``serve_index`` rather than the
            # ladder. The replicate helps here too: it is appended last, so it is
            # maximally distant in time from the arm it replicates, and the noise floor
            # measured from that pair already absorbs drift across at least one arm's
            # serve rather than being a within-moment figure.
            summary["serve_index"] = serve_order.index(arm)
            summary["serve_started_utc"] = datetime.fromtimestamp(
                arm_started, tz=timezone.utc
            ).isoformat(timespec="seconds")
            summary["serve_seconds"] = round(time.time() - arm_started, 1)
            summaries[arm] = summary
            # Kept for the cross-arm checks below: whether two arms actually
            # delivered different context, and how much of their score difference
            # this run could resolve. Both are pairwise and cannot be computed from
            # per-arm summaries alone.
            rows_by_arm[arm] = _rows
            print(
                f"  [{arm}] EX={fmt_rate(summary['ex_lenient'])} "
                f"EX_gradeable={fmt_rate(summary['ex_gradeable'])} "
                f"routing_recall={fmt_rate(summary['routing_recall'])} "
                f"cond_EX|routed={fmt_rate(summary['cond_ex_given_routing'])} "
                f"decoy={fmt_rate(summary['decoy_touch_rate'], 4)} "
                f"refuse={fmt_rate(summary['refusal_rate'])} "
                f"crash={fmt_rate(summary['crash_rate'])}"
            )
            # A metric nobody prints is a metric nobody reads, and the calibration is
            # the one that decides whether the trust stamp means anything. Ordered by
            # the assurance ladder rather than alphabetically, because the whole
            # question is whether EX falls as assurance drops.
            _cal = summary.get("ex_by_semantic_assurance") or {}
            # ``n_unstamped`` lives inside the block (it qualifies these numbers), so
            # it is not a stamp level and must not be printed as one.
            _levels = {k: v for k, v in _cal.items() if isinstance(v, dict)}
            if _levels:
                ladder = [
                    k for k in ("unflagged", "heuristic", "unverified") if k in _levels
                ]
                ladder += [k for k in sorted(_levels) if k not in ladder]
                print(
                    "         calibration  "
                    + "  ".join(
                        f"{k}={fmt_rate(_levels[k]['ex_lenient'])}(n={_levels[k]['n']})"
                        for k in ladder
                    )
                    + f"  unstamped={_cal.get('n_unstamped', 0)}"
                )
            _rep = summary.get("ex_by_repair") or {}
            _note = summary.get("ex_by_note_injected") or {}
            _gc = summary.get("guardrail_cost_ceiling") or {}
            print(
                "         EX|repaired="
                f"{fmt_rate(_rep.get('with'))}(n={_rep.get('n_with', 0)}) "
                f"EX|first_try={fmt_rate(_rep.get('without'))}"
                f"(n={_rep.get('n_without', 0)})  "
                f"EX|note={fmt_rate(_note.get('with'))}"
                f"(n={_note.get('n_with', 0)}) "
                f"EX|no_note={fmt_rate(_note.get('without'))}"
                f"(n={_note.get('n_without', 0)})  "
                f"blocked_then_wrong={fmt_rate(_gc.get('blocked_then_wrong_rate'))}"
                f"(n={_gc.get('n_blocked', 0)}, ceiling)"
            )
            # The most quotable numbers on this line are the least causal, and a
            # terminal copy-paste carries no context. Both strata are selected by an
            # outcome of the turn: repaired = the questions that already failed once,
            # note = the questions retrieval had a note for. Not effects.
            print(
                "         ^ strata are self-selected (repaired = already failed once; "
                "note = retrieval matched) — differences are not effects"
            )
    finally:
        stage_sink.close()
        connector.close()

    deltas = ladder_deltas(summaries, rows_by_arm=rows_by_arm)

    # Pairwise arm comparisons, each carrying what the run could resolve alongside
    # what it measured. A delta reported without its resolution is how "+5 questions,
    # not significant" got published as evidence that an intervention does nothing,
    # when the run could not have detected a real effect six times that size.
    comparisons, divergences = compare_arms(rows_by_arm, replicate_of=replicate_of)

    result = {
        "mode": "datalake",
        "split": split,
        "split_note": (
            "train scores the questions the curator was built from — diagnostic, "
            "not held out"
            if split == "train"
            else "held-out evaluation split"
        ),
        # Every arm actually served, replicate and oracle rungs included. Listing
        # only the fair arms made a summary carrying four arms' scores announce one,
        # so a reader could not tell from the manifest what the run had done.
        "arms_run": list(summaries),
        "fair_arms": list(arms),
        # Requested AND present on Postgres — the denominator the build coverage gate
        # actually measures against. Distinct from ``n_dbs_requested`` below, which
        # counts before the presence filter. Both were spelled ``n_dbs_requested`` in
        # this one dict literal: the later key won, which happened to be the right
        # one, and a reorder would have silently changed the number.
        "n_dbs_attempted": len(wanted),
        "n_dbs_built": len(built),
        "built_dbs": built,
        "build_errors": build_errors,
        # Built, then withheld from the serve loop because the curator recorded an error
        # for it (``_quarantine_curator_failures``). A FOURTH kind of attrition, beside
        # absent-from-Postgres, failed-to-build and gold-unverified, and the one with no
        # home before now: these schemas are absent from ``built_dbs`` and
        # absent from ``build_errors``, so without this field they would simply be gone
        # and the run would report full coverage of a pool it had shrunk — the failure
        # ``dbs_absent_from_postgres`` exists to catch, one layer in. ``n_dbs_built``
        # counts what was SERVED, so the pre-quarantine count is stated beside it rather
        # than left to be inferred from two lists.
        "dbs_quarantined_curator_error": quarantined_dbs,
        "n_dbs_built_before_quarantine": n_built,
        # Built, then withheld because the scored split has zero questions for them
        # (``_quarantine_zero_question_schemas``). Same silent-attrition shape as
        # curator quarantine: absent from ``built_dbs`` and from ``build_errors``,
        # so without this field they vanish from the census while looking like a
        # fully covered smaller pool.
        "dbs_zero_questions": zero_question_dbs,
        # Requested but not loaded on Postgres. Distinct from ``build_errors`` (loaded
        # but the build failed): neither the coverage gate nor the gold share can see
        # these, because both measure against the already-filtered ``wanted``.
        "dbs_absent_from_postgres": sorted(missing),
        "n_dbs_requested": n_requested,
        "n_questions": len(pairs),
        "arms": summaries,
        "deltas": deltas,
        # Paired (McNemar) comparisons with the run's own noise floor and minimum
        # detectable effect. Prefer these over ``deltas``: a difference of marginal
        # rates ignores that both arms answered the same questions, which is the
        # single largest source of variance on a benchmark this uneven.
        "comparisons": comparisons,
        # Did the arms actually differ in what they sent the model? An arm pair that
        # delivered identical context is one experiment run twice, whatever their
        # corpora contain on disk.
        "treatment_divergence": divergences,
        "routing": {
            "top_k": route_top_k,
            "llm_pick": route_llm_pick,
            "embedder": bool(embedder),
            "note": (
                "routing_recall per arm is the share of questions whose true schema "
                "survived routing; it caps EX (a mis-routed question scores 0)."
            ),
        },
        "corpus_validation": corpus_validation,
        "corpus_census": corpus_census_by_arm,
        "corpus_census_deltas": census_deltas,
        "curator_errors": curator_errors,
        "sme_fold": sme_fold,
        "gold_hash_self_check": gold_check,
        # Decoy-touch is only meaningful where traps were actually loaded. Naming the
        # dbs that had no manifest keeps a 0.0 rate from reading as "clean".
        "decoy_manifest_missing_dbs": trap_manifest_missing,
        "serve_policy": {
            "hard_block_suspect_columns": settings.hard_block_suspect_columns,
            "grade_semantic_failures": settings.grade_semantic_failures,
        },
        "leakage": leakage,
    }
    (out_dir / "summary.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    metrics.write_manifest(out_dir, {**manifest, "completed_at_utc": _utc_ts()})

    # Offline analysis.json — same report ``python -m governed_bi.eval.analysis``
    # produces. Failures are loud: a missing analysis.json used to go unnoticed
    # forever because the driver never called analyse_run.
    try:
        analysis = analyse_run(out_dir, bird_dir=bird_dir, split=split)
        analysis_path = out_dir / "analysis.json"
        analysis_path.write_text(
            json.dumps(analysis, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print(f"wrote {analysis_path}")
    except Exception as exc:
        print(
            f"\n*** WARNING: analyse_run failed after summary.json was written "
            f"({type(exc).__name__}: {exc}). Re-run:\n"
            f"  uv run python -m governed_bi.eval.analysis {out_dir} "
            f"--bird-dir {bird_dir}\n"
        )
        traceback.print_exc()

    # A crashy arm is not a result: the crashes it absorbed are OUR failures, and
    # they are not distributed equally across arms, so the deltas move with them.
    crashy = {
        arm: s["crash_rate"] for arm, s in summaries.items() if s.get("n_crashed")
    }
    if crashy:
        detail = ", ".join(f"{a}={r:.3f}" for a, r in sorted(crashy.items()))
        # Accumulate, don't comprehend. A dict comprehension over every arm is
        # last-writer-wins per stage, so a stage that several arms hit would print
        # only the last arm's count and read as *smaller* than a stage one arm hit —
        # in the one line whose job is telling an operator where to look first.
        stages_hit: Counter[str] = Counter()
        for s in summaries.values():
            stages_hit.update(s.get("by_failed_stage") or {})
        print(
            f"\n*** WARNING: solver CRASHES during serve ({detail}). These are bugs in "
            "this system, not model refusals: they depress EX and inflate nothing "
            "you can subtract, and the arms do not crash equally — so no arm-to-arm "
            "delta from this run is quotable until they are fixed. Failing stages "
            f"(all outcomes, not only crashes): {dict(stages_hit) or 'unattributed'}. "
            "See stage_events.jsonl. ***\n"
        )

    # Self-register in the run ledger so "which runs exist, and which two may be
    # compared" is computed from artifacts instead of remembered. Deliberately not
    # wrapped: summary.json is already on disk, so a raise here costs visibility
    # only, and a ledger that silently fails to record a run is the failure mode the
    # ledger exists to prevent.
    record = index_run(out_dir)
    if not record["quotable"]:
        print(f"*** run indexed as NOT quotable: {out_dir}")
        for reason in record["not_quotable_because"]:
            print(f"  - {reason}")
    # Carried so `main` can exit non-zero on it: a scripted run had no way to tell a
    # clean result from a disqualified one (AUDIT E5).
    result["quotable"] = record["quotable"]
    result["not_quotable_because"] = list(record["not_quotable_because"])
    return result


def main(argv: list[str] | None = None) -> int:
    """Run the pooled data-lake eval. Returns a process exit code.

    Non-zero when the run indexed as NOT quotable. The only ``SystemExit`` in
    ``eval/`` returned a hardcoded 0, so a scripted or CI-driven run could not tell a
    clean result from one the ledger had already disqualified (AUDIT E5) — the
    operator had to read stdout.
    """
    from ..logging_setup import configure_logging

    configure_logging()
    p = argparse.ArgumentParser(description="Pooled data-lake BIRD eval (D15 scale run)")
    p.add_argument(
        "--bird-dir", type=Path, default=Path("../BIRD-Data-Obfuscation"),
        help="Path to BIRD-Data-Obfuscation checkout",
    )
    p.add_argument(
        # No credential in the source. The obfuscated-BIRD Postgres is a local
        # throwaway, but a password committed to git is a password committed to git,
        # and this default is the one every runbook copies (AUDIT S7). Set
        # GOVERNED_BI_PG_DSN (or pass --pg-dsn) instead.
        "--pg-dsn",
        default=os.environ.get(
            "GOVERNED_BI_PG_DSN", "host=127.0.0.1 port=5435 dbname=bird user=bird"
        ),
    )
    p.add_argument("--out", type=Path, default=Path("runs/datalake"))
    p.add_argument(
        "--split",
        choices=(*_SPLITS, "both"),
        default="test",
        help=(
            "Question split to score. 'train' is larger but is what the curator was "
            "built from — a diagnostic, not a held-out result, and eval.index.quotable "
            "refuses it. 'both' builds the corpora ONCE and scores each split into its "
            "own subdirectory, then writes split_gap.json: train-minus-test per arm, "
            "which is how much of an arm's score does not survive a new question. "
            "Sharing the build is the point — the curator is stochastic, so rebuilding "
            "per split would mix overfitting with curator variance."
        ),
    )
    p.add_argument(
        "--resume-from",
        type=Path,
        default=None,
        help=(
            "Continue an existing run directory instead of creating a new "
            "timestamped one: questions already in generations.<arm>.jsonl are "
            "replayed, the rest are served."
        ),
    )
    p.add_argument("--dbs", default=None, help="Comma-separated db_ids (default: all dbs in the split)")
    p.add_argument(
        "--arms",
        default=None,
        help=(
            "Comma-separated arms (subset of baseline,seeded,curated,curated_sme; "
            "default all). The ladder is designed so each adjacent step changes one "
            "thing: seeded adds the deterministic train-SQL seed (no model calls to "
            "build), curated adds the LLM curator agent on top of it, curated_sme "
            "adds the clarification round. Dropping a middle rung leaves the "
            "surrounding delta bundling two interventions."
        ),
    )
    p.add_argument("--limit-dbs", type=int, default=None, help="Cap the number of dbs")
    p.add_argument("--limit", type=int, default=None, help="Cap questions PER db")
    p.add_argument(
        "--max-agent-steps",
        type=int,
        default=None,
        help=(
            "Per-schema curator budget in TOOL CALLS (not super-steps). Unset "
            "derives it from each schema's size — tables, columns, rendered pairs — "
            "so a 3-table and a 73-table schema do not share one constant. An "
            "explicit N is an operator override that caps cost and applies to every "
            "schema alike. The effective LangGraph recursion_limit is 3 * budget + 4, "
            "because one tool call costs up to three super-steps."
        ),
    )
    p.add_argument(
        "--oracle-only",
        action="store_true",
        help=(
            "Serve only oracle rungs (default oracle_sql if --oracle names none): no "
            "fair arm, no model load, effectively free. Replaces the retired "
            "--skip-agent — Option A (M3 N10, decision 12) makes 'no model was "
            "called' an inference from an empty fair-arm set rather than a global "
            "flag that could combine with any configuration, so it refuses "
            "combination with --arms or with an oracle rung other than oracle_sql."
        ),
    )
    p.add_argument(
        "--no-resume",
        action="store_true",
        help=(
            "Start clean: rebuild corpora even if present, and re-serve every "
            "question even if already scored in the run directory."
        ),
    )
    p.add_argument("--route-top-k", type=int, default=10, help="Schema shortlist size")
    p.add_argument(
        "--schema-pick-max-columns",
        type=int,
        default=None,
        help=(
            "Column names per table shown to the LLM schema picker (0 = names only). "
            "Column vocabulary is what separates same-topic sibling schemas."
        ),
    )
    p.add_argument("--no-llm-pick", action="store_true", help="Keep shortlist (no single-schema LLM pick)")
    p.add_argument(
        "--pin-triggers",
        action="store_true",
        help=(
            "Turn ADR 0003 keyword PINs on (default off, which is the baseline every "
            "prior run served). A note whose triggers match the question is forced "
            "into the prompt ahead of RRF ranking AND its schema is prepended to the "
            "router shortlist, so this moves ROUTING as well as note text. It is its "
            "own knob, not part of an arm: the manifest records it and the ledger "
            "refuses to compare a PIN run against a non-PIN one."
        ),
    )
    p.add_argument("--no-embedder", action="store_true", help="BM25-only routing (no embeddings)")
    p.add_argument(
        "--workers",
        type=int,
        default=None,
        help=(
            "Serve-loop worker threads (overrides [eval] workers in "
            "governed_bi.toml; default 1 = serial). Size to your Postgres "
            "max_connections; each worker holds its own connection + graph."
        ),
    )
    p.add_argument(
        "--build-workers",
        type=int,
        default=None,
        help=(
            "Concurrent per-db corpus builds (overrides [eval] build_workers, then "
            "[eval] workers; default 1 = serial). The dbs are independent, so this "
            "is the biggest wall-clock lever on a scale run: each build is a "
            "deep-agent curator pass and there are as many as there are schemas. "
            "Each build runs in a private staging root and is promoted into the "
            "shared arm root on success. Size to your Postgres max_connections AND "
            "your model provider's rate limit."
        ),
    )
    p.add_argument(
        "--replay-crashed",
        action="store_true",
        help=(
            "On --resume-from, keep crashed turns instead of re-serving them. Off by "
            "default: a crash is a bug rather than a measurement, and any crash makes "
            "the run unquotable — so replaying one preserves nothing and costs the run."
        ),
    )
    p.add_argument(
        "--gold-per-db",
        type=int,
        default=1,
        help=(
            "Gold rows the pre-flight verifies per schema (default 1). A schema counts "
            "as verified when ANY sampled row executes and agrees, so raising this buys "
            "redundancy against one slow or genuinely-broken gold row rather than more "
            "ways to fail. Costs about 40 ms per row per schema."
        ),
    )
    p.add_argument(
        "--prompt",
        action="append",
        metavar="STAGE=VARIANT",
        default=None,
        help=(
            "Select a registered prompt variant for one stage, e.g. "
            "--prompt schema_pick=v2 (repeatable). Default: every stage on v1. "
            "An unknown stage or variant is an error, never a fallback to v1."
        ),
    )
    p.add_argument(
        "--replicate",
        metavar="ARM",
        default=None,
        help=(
            "Serve ARM twice (as ARM__replicate) to measure this run's noise floor "
            "and minimum detectable effect. Costs one extra serve pass. Without it "
            "the run reports p-values but cannot say what size of effect it was "
            "able to resolve — the gap that let a null result inside the noise be "
            "published as a finding."
        ),
    )
    p.add_argument(
        "--oracle",
        default=None,
        metavar="RUNG[,RUNG...]",
        help=(
            "ALSO serve counterfactual rungs, in addition to --arms: oracle_sql "
            "(the grader's own ceiling, free), oracle_schema (routing cannot miss), "
            "oracle_tables (table selection cannot miss), oracle_tables_padded (the "
            "control for oracle_tables — same gold tables, padded back to a "
            "comparable count). Each rung's EX lift is that stage's headroom, "
            "measured rather than estimated. These read the answer key: diagnostics, "
            "never system performance. Note ALSO: --arms baseline --oracle X,Y,Z is "
            "FOUR serve passes, not three."
        ),
    )
    args = p.parse_args(argv)

    arms = (
        tuple(a.strip() for a in args.arms.split(","))
        if args.arms
        else _ARMS
    )
    oracles: tuple[str, ...] = ()
    if args.oracle:
        oracles = tuple(o.strip() for o in args.oracle.split(",") if o.strip())
        known = {r.value for r in OracleRung}
        unknown = sorted(set(oracles) - known)
        if unknown:
            p.error(
                f"unknown oracle rung(s): {', '.join(unknown)}. "
                f"Choose from: {', '.join(sorted(known))}"
            )
    if args.oracle_only:
        # ``--oracle-only`` is the Option A replacement for the retired
        # ``--skip-agent``: "no model was called" is now an INFERENCE from an empty
        # fair-arm set (``run_datalake``'s ``oracle_only``), not a global flag that
        # could combine with any configuration (M3 N10, decision 12). Forbidding
        # explicit fair arms here, rather than silently dropping them, keeps that
        # inference honest — a caller who typed --arms meant to serve them.
        if args.arms:
            p.error(
                "--oracle-only and --arms are mutually exclusive: an oracle-only run "
                "serves no fair arms by construction"
            )
        # ``oracle_sql`` submits gold SQL and needs no model. Every other rung serves
        # through the real graph, so without one it would silently fall through to the
        # refuse-all solver and produce an arm that is shape-identical to a genuinely
        # refused one — an unmeasured rung indistinguishable from a measurement. Refuse
        # the combination instead of scoring it.
        needs_model = sorted(set(oracles) - {OracleRung.sql.value})
        if needs_model:
            p.error(
                f"--oracle-only cannot serve {', '.join(needs_model)}: these rungs "
                "serve through the real graph and need a model, so they would "
                "score as refusals rather than as counterfactuals. Only "
                f"{OracleRung.sql.value} runs without a model — it submits gold "
                "SQL straight to the grader, which is why it is step 0 of the "
                "runbook."
            )
    bad = [a for a in arms if a not in _ARMS]
    if bad:
        p.error(f"--arms must be a subset of {_ARMS}; unknown: {bad}")
    if args.resume_from is not None and args.no_resume:
        p.error("--resume-from and --no-resume are contradictory")
    try:
        prompt_overrides = parse_cli_overrides(args.prompt)
    except (KeyError, ValueError) as err:
        # argparse's own error path, so a typo reads as a usage error rather than a
        # traceback — and exits before the run opens a database.
        p.error(str(err.args[0] if err.args else err))

    # CLI overrides config; config overrides the code default of 1.
    _cfg = load_settings()
    workers = args.workers if args.workers is not None else _cfg.serve_worker_count()
    workers = resolve_workers(workers)
    build_workers = (
        args.build_workers
        if args.build_workers is not None
        else _cfg.build_worker_count()
    )

    bird_dir = args.bird_dir.resolve()
    if args.resume_from is not None:
        out_dir = args.resume_from
        if not out_dir.is_dir():
            p.error(f"--resume-from {out_dir} is not a directory")
        # A single-split run directory holds its artifacts FLAT (``manifest.json``,
        # ``summary.json``, ``generations.<arm>.jsonl`` at the top), while ``both``
        # holds them per split under ``train/`` and ``test/``. Resuming the first as
        # the second silently strands the flat files beside the new subdirectories:
        # the resume guard reads ``<split_dir>/manifest.json`` and there is none, so
        # nothing compares knobs, nothing replays the rows already scored, and the run
        # directory ends up carrying two runs' artifacts with only the flat ones
        # visible to ``analyse_run`` / the ledger. Refused rather than handled — the
        # useful shapes (resume the single split, or start ``both`` fresh) are both
        # one command away, and neither loses the earlier work.
        if args.split == "both" and (out_dir / "manifest.json").exists():
            p.error(
                f"--resume-from {out_dir} is a single-split run directory (it has a "
                "flat manifest.json) and --split both writes per-split subdirectories. "
                "Resume it with the --split it was run under, or start a fresh "
                "--split both run; mixing the two layouts leaves stale flat artifacts "
                "that the resume guard cannot see."
            )
        print(f"run dir: {out_dir} (resuming)")
    else:
        out_dir = args.out / _utc_ts()
        print(f"run dir: {out_dir}")
    # ``both`` scores each split into its own subdirectory off ONE corpus build.
    # Per-split directories rather than per-split filenames inside one: every
    # downstream reader (``analyse_run``, ``index_run``, ``quotable``, the resume
    # guard) is keyed to a run directory holding one split's artifacts, and the
    # resume guard refuses a directory whose manifest names a different ``--split``
    # precisely so two splits cannot be mixed in one generations file.
    splits = _SPLITS if args.split == "both" else (args.split,)
    corpus_dir = out_dir if len(splits) == 1 else out_dir / "corpora"
    results: dict[str, dict[str, Any]] = {}
    for index, split in enumerate(splits):
        split_dir = out_dir if len(splits) == 1 else out_dir / split
        if len(splits) > 1:
            print(f"\n=== scoring split {split!r} -> {split_dir} ===")
        result = _score_one_split(
            args,
            bird_dir=bird_dir,
            out_dir=split_dir,
            corpus_dir=corpus_dir,
            split=split,
            # ONE build for the whole run, even under ``--no-resume``. The corpus is
            # the treatment and the curator is stochastic, so a rebuild between the
            # splits makes the gap a mix of overfitting and curator variance (and
            # pays twice for the run's dominant cost). ``resume`` still governs row
            # replay, which is per-split-directory and stays clean when asked for.
            reuse_corpus=index > 0,
            arms=arms,
            oracles=oracles,
            prompt_overrides=prompt_overrides,
            workers=workers,
            build_workers=build_workers,
        )
        results[split] = result
    if len(splits) > 1:
        from .split_gap import format_split_gap, write_split_gap

        gap = write_split_gap(out_dir, out_dir / "train", out_dir / "test")
        print("\ntrain-vs-test gap (diagnostic; train is never quotable):")
        print(format_split_gap(gap))

    # 2, not 1: distinguishes "ran to completion but is not quotable" from a crash.
    # With ``--split both`` the train split is unquotable BY DESIGN, so its verdict
    # must not decide the exit code — otherwise every combined run exits 2 and the
    # signal stops meaning anything. Only the held-out split gates.
    gating = results.get("test") or results[splits[0]]
    return 0 if gating.get("quotable", True) else 2


def _score_one_split(
    args: argparse.Namespace,
    *,
    # Per-split, computed in ``main``: which split, where its artifacts go, which
    # corpus it serves, and whether it may adopt a build the previous split made.
    split: str,
    out_dir: Path,
    corpus_dir: Path,
    reuse_corpus: bool,
    # RESOLVED once in ``main`` — parsed, validated and defaulted against config —
    # rather than re-derived per split from ``args``. Two derivations of one knob is
    # how the two splits of a single run could disagree about what they measured, and
    # ``--prompt`` / ``--workers`` in particular go through validation that must not
    # run twice.
    bird_dir: Path,
    arms: tuple[str, ...],
    oracles: tuple[str, ...],
    prompt_overrides: dict[str, str],
    workers: int,
    build_workers: int,
) -> dict[str, Any]:
    """One split's build-or-reuse, serve, score and report.

    Extracted from ``main`` so ``--split both`` runs it twice without duplicating the
    twenty-odd argument hand-off. The second call passes ``reuse_corpus=True`` and so
    finds every corpus already complete under ``corpus_dir``, whatever ``--no-resume``
    says: one build per run is what makes the train-vs-test gap mean overfitting
    rather than curator variance.
    """
    try:
        result = run_datalake(
            bird_dir=bird_dir,
            pg_dsn=args.pg_dsn,
            out_dir=out_dir,
            corpus_dir=corpus_dir,
            db_ids=[d.strip() for d in args.dbs.split(",")] if args.dbs else None,
            arms=arms,
            limit_dbs=args.limit_dbs,
            limit=args.limit,
            max_agent_steps=args.max_agent_steps,
            oracle_only=args.oracle_only,
            resume=not args.no_resume,
            split=split,
            route_top_k=args.route_top_k,
            schema_pick_max_columns=args.schema_pick_max_columns,
            route_llm_pick=not args.no_llm_pick,
            use_embedder=not args.no_embedder,
            pin_triggers=args.pin_triggers,
            serve_workers=workers,
            build_workers=build_workers,
            gold_per_db=args.gold_per_db,
            replay_crashed=args.replay_crashed,
            prompt_variants=prompt_overrides,
            replicate_of=args.replicate,
            oracles=oracles,
            reuse_corpus=reuse_corpus,
        )
        arms_path = out_dir / "arms_summary.json"
        out_dir.mkdir(parents=True, exist_ok=True)
        arms_path.write_text(
            json.dumps(result["arms"], indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print(
            f"arms summary: {len(result['arms'])} arm(s) -> {arms_path}"
        )
        # Printed BEFORE any delta, because these are the two questions that decide
        # whether a delta means anything: did the arms differ, and could this run have
        # seen it if they did. They used to come after, which is the reading order
        # that let a null inside the noise get published as a finding.
        print("\ntreatment delivery:")
        print(divergence_table(result["treatment_divergence"]))
        print("\npaired comparisons (Holm-adjusted across the fair family):")
        for comparison in result["comparisons"]:
            tag = " [diagnostic rung]" if comparison.get("diagnostic_pair") else ""
            # A compound step and a backwards one are the two ways to misread this
            # line, so both are said here rather than left in the JSON. An operator
            # watching a long run reads stdout; the artifact is for afterwards.
            if comparison.get("bundles"):
                tag += " [bundles " + ", ".join(comparison["bundles"]) + "]"
            if comparison.get("confounded_mechanisms"):
                # One rung, more than one mechanism: no rung was skipped, so the
                # ``bundles`` tag above stays silent and this step would otherwise read
                # as single-variable on stdout.
                tag += (
                    " [one rung, "
                    + " + ".join(comparison["confounded_mechanisms"])
                    + " — cannot attribute to either]"
                )
            if comparison.get("ladder_descending"):
                tag += " [reads DOWN the ladder — sign is reversed]"
            p_adj = comparison.get("p_value_holm")
            adj = f", holm={p_adj:.4g}" if isinstance(p_adj, float) else ""
            print(
                f"  {comparison['arm_a']} -> {comparison['arm_b']}: "
                f"{comparison['net_questions']:+d} questions "
                f"(p={comparison['p_value']:.4g}{adj}){tag} — {comparison['reading']}"
            )
            cluster = comparison.get("cluster") or {}
            if cluster.get("reading"):
                print(f"      by database: {cluster['reading']}")
        # Raw marginal-rate differences last, and labelled: they ignore that both arms
        # answered the same questions, so the paired comparisons above supersede them.
        deltas_path = out_dir / "deltas.json"
        deltas_path.write_text(
            json.dumps(result["deltas"], indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print(
            f"\nunpaired marginal deltas (prefer the paired comparisons above) "
            f"-> {deltas_path}"
        )
    finally:
        from ..obs import flush_tracing

        flush_tracing()

    return result


if __name__ == "__main__":
    raise SystemExit(main())
