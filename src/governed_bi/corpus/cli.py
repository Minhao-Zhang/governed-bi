"""Corpus validation CLI.

Loads a ``corpus/<schema>/`` tree, runs the CI reference-integrity + ID checks, and
prints findings. A green run is the curator's machine-checkable "done-enough"
signal (D9). Physical-existence and few-shot leakage checks are skipped here:
they need a live catalog / the eval split, so they belong to the eval harness.

Run it with:

    uv run python -m governed_bi.corpus.cli corpus/beer_factory
    uv run python -m governed_bi.corpus.cli --help
"""

from __future__ import annotations

import argparse
from pathlib import Path

from .loader import load_corpus
from .validate import is_green, validate_corpus

# Exit codes (documented in --help and docs/usage.md).
EXIT_GREEN = 0  # no findings
EXIT_FINDINGS = 1  # one or more findings
EXIT_USAGE = 2  # bad arguments / path not found (argparse also uses 2)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m governed_bi.corpus.cli",
        description=(
            "Validate a corpus tree: ID conventions + reference integrity. "
            "A green run is the curator's 'done-enough' signal (D9)."
        ),
        epilog=(
            "PATH may be a corpus root (validates every <schema> under it) or a single "
            "<schema> directory. Exit codes: 0 = green, 1 = findings, 2 = bad usage / path "
            "not found. Physical-existence and leakage checks are not run here."
        ),
    )
    parser.add_argument(
        "path",
        nargs="?",
        default="corpus",
        help="corpus root or a single <schema> directory (default: %(default)s)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    ns = parser.parse_args(argv)

    root = Path(ns.path)
    if not root.is_dir():
        parser.error(f"path not found: {root}")  # prints usage, exits 2

    # Accept either a corpus root or a single <schema> directory.
    if (root / "tables").is_dir():
        corpus = load_corpus(root.parent, schema=root.name)
    else:
        corpus = load_corpus(root)

    findings = validate_corpus(corpus.assets)
    n_assets = len(corpus.assets)

    if is_green(findings):
        print(f"CI green: {n_assets} assets, 0 findings.")
        return EXIT_GREEN

    print(f"CI failed: {n_assets} assets, {len(findings)} findings:")
    for f in findings:
        print(f"  - {f}")
    return EXIT_FINDINGS


if __name__ == "__main__":
    raise SystemExit(main())
