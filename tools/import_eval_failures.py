#!/usr/bin/env python
"""File measured engine failures into the return path's store.

    uv run --frozen python tools/import_eval_failures.py --dry-run
    uv run --frozen python tools/import_eval_failures.py --commit

``--dry-run`` is the default and prints the partition without touching the store, because the
partition **is** the interesting output the first time: it reproduces ``docs/failure-modes.md`` §1,
which that page carries under a "hand-run, no producer in the tree" warning.

Importing twice is safe. Every row carries an ``external_key`` over the arm, the question and both
treatment hashes, so re-reading one artifact files nothing new — while running a *new* arm and
importing that is new information about a different treatment, and files fresh rows.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from governed_bi.eval.feedback_import import import_failures
from governed_bi.feedback.cluster import clusters
from governed_bi.feedback.store import FeedbackStore
from governed_bi.paths import REPO_ROOT

#: The v4 arm: the population every figure in ``docs/failure-modes.md`` is about.
DEFAULT_ARTIFACT = "runs/eval/proxy_v4_corpus30872d3.jsonl"
#: The split those numbers were measured on. An artifact carries no question text on any row.
DEFAULT_DATASET = "../BIRD-Data-Obfuscation/eval_dataset/test_final.jsonl"
DEFAULT_DB = "runs/feedback.sqlite"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact", default=DEFAULT_ARTIFACT)
    parser.add_argument("--dataset", default=DEFAULT_DATASET)
    parser.add_argument("--db", default=DEFAULT_DB)
    writing = parser.add_mutually_exclusive_group()
    writing.add_argument(
        "--commit",
        action="store_true",
        help="actually write. Without it nothing is stored and the partition is printed.",
    )
    # Accepted although it is the default, so the command in this module's docstring runs. A
    # documented flag that does not exist is the same defect as an undocumented one.
    writing.add_argument(
        "--dry-run",
        action="store_true",
        help="the default: print the partition and write nothing.",
    )
    parser.add_argument(
        "--include-flags",
        default="",
        help=(
            "comma-separated quality flags to import despite being dataset defects, e.g. "
            "'degenerate'. Excluded by default: 19%% of the queue permanently unactionable is "
            "how a queue gets abandoned."
        ),
    )
    parser.add_argument(
        "--show-clusters",
        type=int,
        default=0,
        metavar="N",
        help="print the N largest clusters after importing, to answer 'do these group at all'",
    )
    args = parser.parse_args(argv)

    artifact = _resolve(args.artifact)
    dataset = _resolve(args.dataset)
    store = FeedbackStore(_resolve(args.db))

    report = import_failures(
        artifact,
        dataset=dataset,
        store=store,
        dry_run=not args.commit,
        include_flags=frozenset(f for f in args.include_flags.split(",") if f),
    )
    print(report.render())
    if not args.commit:
        print("\n(dry run: nothing was written. Pass --commit to file these.)")

    if args.show_clusters:
        _print_clusters(store, limit=args.show_clusters, live=args.commit)
    return 1 if report.refused else 0


def _print_clusters(store: FeedbackStore, *, limit: int, live: bool) -> None:
    """The first real answer to ADR 0015's open question 7, "do complaints cluster at all?"."""
    if not live:
        print("\n(clusters need --commit: they are computed over stored rows)")
        return
    found = clusters(store.queue(limit=10_000).rows)
    sized = sorted(found, key=lambda c: (-c.n, c.key))[:limit]
    print(f"\n{len(found)} cluster(s) over {sum(c.n for c in found)} observation(s).")
    print(f"{'n':>3}  {'distinct qs':>11}  key")
    for cluster in sized:
        print(f"{cluster.n:>3}  {cluster.n_distinct_questions:>11}  {cluster.key}")
    singletons = sum(1 for c in found if c.n == 1)
    print(
        f"\n{singletons} of {len(found)} clusters hold one observation. A population that is "
        "mostly singletons means a batch pipeline would be a per-event pipeline wearing a batch "
        "pipeline's name -- which is the thing this print exists to find out."
    )


def _resolve(value: str) -> Path:
    """Relative to the repository root, not to the shell's working directory.

    The same rule ``GOVERNED_BI_CORPUS_DIR`` follows, and for the same reason: a path that means
    something different depending on where it was typed is a path that reads a different artifact
    in CI than on a laptop.
    """
    path = Path(value)
    return path if path.is_absolute() else (REPO_ROOT / path)


if __name__ == "__main__":
    sys.exit(main())
