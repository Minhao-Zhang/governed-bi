"""Corpus conformance: the twenty-two rules ADR 0005 §1.2 states in prose, executed.

    from governed_bi.conform import Manifests, problems_with_corpus

    report = problems_with_corpus(Path("../BIRD-corpus"), Manifests(trap=..., test_split=...))
    report.total, report.findings["V17a"], report.not_evaluated

Two entry points, one report type, and every rule behind them. ``check.py`` carries the argument
for the interface and for this package's position in the layering; read that first.

**Why the engine owns these rules.** They are statements about what *this engine* will do with an
asset, not about the data: V16 measures a table with ``serve/context.py``'s own renderer, V17a
parses a metric expression at the dialect ``govern/`` parses generated SQL at, V21 runs
``govern/guard.py``'s own ``GUARD_RULES`` over model-visible text. ADR 0016 §Context 1 is that
argument, and it is why the checker cannot move to the corpus repository it checks -- a second copy
of any of those beside the data is a second answer free to disagree with the first. The rules lived
under ``tools/`` until 2026-08-25, which made the argument true of a CLI rather than of the engine:
the only way to ask "does this corpus satisfy my rules" was to spawn a subprocess and parse its
JSON.

**What is still a CLI, and why.** ``tools/check_corpus_conformance.py`` owns argv, the printed
per-rule report, the ``--json`` inventory and three exit codes CI depends on (0 nothing added, 1 a
finding, 2 could not run). ``tools/check_ratchet.py`` and ``tools/check_corpus_delta.py`` own the
two *policies* over that inventory -- a pin file and a git revision -- and ADR 0016 records why
those disagree on purpose. None of that is a rule, so none of it is here.
"""


from __future__ import annotations

from .check import (
    RULE_DESCRIPTIONS,
    WHOLE_TREE_ONLY,
    ConformanceReport,
    Manifests,
    RawAsset,
    problems_with_asset_file,
    problems_with_corpus,
)
from .findings import Finding, where_of, where_of_file
from .tree import load_assets, walk

__all__ = [
    "ConformanceReport",
    "Finding",
    "Manifests",
    "RULE_DESCRIPTIONS",
    "RawAsset",
    "WHOLE_TREE_ONLY",
    "load_assets",
    "problems_with_asset_file",
    "problems_with_corpus",
    "walk",
    "where_of",
    "where_of_file",
]
