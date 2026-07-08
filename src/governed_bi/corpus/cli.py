"""Corpus validation CLI.

    uv run python -m governed_bi.corpus.cli corpus/california_schools

Loads a ``corpus/<db>/`` tree, runs the CI reference-integrity + ID checks, and
prints findings. Exit 0 == green (the curator's "done-enough" signal); exit 1 ==
findings. Physical-existence and leakage checks are skipped here (they need a
live catalog / the eval split).
"""

from __future__ import annotations

import sys
from pathlib import Path

from .loader import load_corpus
from .validate import is_green, validate_corpus


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if not args:
        print("usage: python -m governed_bi.corpus.cli <corpus-dir>", file=sys.stderr)
        return 2

    root = Path(args[0])
    # Accept either a corpus root or a single <db> directory.
    if (root / "tables").is_dir():
        corpus = load_corpus(root.parent, db=root.name)
    else:
        corpus = load_corpus(root)

    findings = validate_corpus(corpus.assets)
    n_assets = len(corpus.assets)
    n_skills = len(corpus.skills)

    if is_green(findings):
        print(f"CI green: {n_assets} assets, {n_skills} skills, 0 findings.")
        return 0

    print(f"CI failed: {n_assets} assets, {n_skills} skills, {len(findings)} findings:")
    for f in findings:
        print(f"  - {f}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
