"""Does a corpus satisfy this engine's rules? One question, two scopes, one answer type.

**The interface.** :func:`problems_with_corpus` for a tree, :func:`problems_with_asset_file` for the
one file a rebuild loop just wrote. Both return a :class:`ConformanceReport`, and everything else in
this package is behind them: the twenty-two rules, which of them need a second asset, and which
could not be answered at all. ``tools/check_corpus_conformance.py`` is an adapter over these two
functions -- it owns argv, the printed report and the three exit codes, and nothing else.

Named for ``corpus/validate.py::problems_with`` and shaped like it on purpose: **a rule set that
returns its problems rather than raising**, so a caller can report all of them instead of the first.
The two are different questions and both are needed -- ``problems_with`` is what the *loader*
enforces on a model it has already parsed, and ADR 0015 requires a patch to be judged by that same
validator; this asks the wider question ADR 0005 §1.2 states in prose, over raw YAML, on a tree that
may not load yet. V14 is the rule that runs the loader, so the narrower one is *inside* the wider
one rather than beside it.

**A report is an inventory, not a verdict.** ``../BIRD-corpus`` carries 125 findings on 101
identities today (ADR 0016 §Context 2), so a caller demanding zero rejects production, gets waived,
and a waiver is how a real finding goes green. What the gates ask is whether a *change* added one:
``tools/check_ratchet.py`` against a pin file, ``tools/check_corpus_delta.py`` against a git
revision. Both read the CLI's ``--json``, which exits 0 either way.

**Why this layer.** Every rule is a statement about what *this engine* will do with an asset, which
is ADR 0016 §Context 1's argument for the checker living here rather than beside the data: V16
measures a table with ``serve/context.py``'s own renderer, V17a parses a metric expression at the
dialect ``govern/`` parses generated SQL at, V21 runs ``govern/guard.py``'s own ``GUARD_RULES`` over
model-visible text. Those three imports are what fix the position: ``serve`` is the latest layer any
rule reaches, so this package sits directly above it. Below ``serve`` the set would have to be split
across three layers, which would mean three rule tables and three reports for one question -- and
the ratchet keys ``(rule, where)`` across all twenty-two.
"""


from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from .findings import Finding, where_of
from .rules_asset import check_local
from .rules_metric_and_content import (
    check_excluded_not_named,
    check_metric_bindings,
    check_unique_ids,
)
from .rules_tree import (
    check_delivery_closure,
    check_loadable,
    check_references,
    check_split_leak,
    check_suspect_set,
    check_suspect_summaries,
)
from .tree import load_assets, walk

#: One conformance run's input tree, as the rules take it: ``(asset_type, mapping, file)``.
RawAsset = tuple[str, dict[str, Any], Path]


RULE_DESCRIPTIONS: dict[str, str] = {
    "V0": "the file parses and declares a known asset_type",
    "V1": "1 <= len(summary) <= summary_max_chars",
    "V2": "summary is not the scaffold sentinel",
    "V3": "summary contains the identifier ASSET_REGISTER declares",
    "V4": "summary is prose, not a template or an identifier roster",
    "V5": "summary carries no values, examples or '(column x)' tail",
    "V6": "a type whose spec names a body has a non-empty one",
    "V7": "a column body is not a tautology",
    "V8": "a term's summary contains every one of its synonyms",
    "V9": "every declared reference resolves to a real asset",
    "V10": "no text discloses how an unreliable column was made",
    "V11": "a suspect column's summary omits the column it resembles",
    "V12": "no asset quotes a held-out question",
    "V13": "no asset body exceeds its cap (few_shot 4k, else 8k)",
    "V14": "the engine's loader accepts the file",
    "V15": "exactly the manifest's columns are marked suspect",
    "V16": "a table and its folded column roster fit the delivery cap",
    "V17a": "a metric expression parses as SQL at the engine's dialect",
    "V17b": "every identifier in a metric expression resolves on base_table or a declared join",
    "V19": "no model-visible body names a governance-excluded column or asset",
    "V21": "model-visible text passes govern/guard.py's GUARD_RULES",
    "V23": "asset ids are unique across the tree",
}

#: Rules that need **a second asset**, and are therefore reported *not evaluated* rather than
#: passed in ``--file`` mode. Every entry states its reason, because this list carried six while
#: its comment justified two, and the four unexamined ones are how V11 and V12 got on it.
#:
#: * V9 -- a reference resolves against the set of ids in the tree, which one file does not hold.
#: * V15 -- *exactly* the manifest's columns are marked. The "no more" half answers from one file;
#:   the "no fewer" half is a mark missing on some other table, so it needs every table.
#: * V17b -- "reachable through a declared join" is a question about the join assets.
#: * V23 -- a duplicate needs a second file to duplicate.
#:
#: **Needing an external manifest is a different thing, and it does not defer a rule.** V11 and
#: V12 read one asset's own ``summary`` and ``body`` against a file on disk; neither looks at a
#: second asset. Both sat here, so the rebuild loop -- the moment a writer is authoring prose --
#: ran without the leakage gate and printed "needs the whole tree", which is not true and reads as
#: a limitation rather than a hole. They run in both modes now, and a missing manifest is reported
#: as *that*, which is a reason a writer can act on by passing a path.
#: The rule id -> the function that answers it, for every rule that needs a second asset and
#: takes the whole asset list. **This mapping is the dispatch**, so a rule cannot be run
#: whole-tree-only without appearing in the list a reader checks. Three rules got onto the wrong
#: side of that when the list and the ``if whole:`` block were two places saying one thing: V11 and
#: V12 were declared deferred while answerable from one asset, and V19 ran only under ``whole``
#: while absent from the list -- so ``--file`` printed ``V19  0``, a disclosure gate reporting
#: clean when it was never asked.
WHOLE_TREE_CHECKS: dict[str, Callable[[list[tuple[str, dict[str, Any], Path]]], list[Finding]]] = {
    "V9": check_references,
    "V17b": check_metric_bindings,
    "V19": check_excluded_not_named,
    "V23": check_unique_ids,
}

#: V15 is the one entry outside the mapping: it takes three manifests rather than ``assets``
#: alone. Named here so the exception stays a single declared one rather than a second habit.
WHOLE_TREE_ONLY = (*WHOLE_TREE_CHECKS, "V15")

@dataclass(frozen=True, slots=True)
class Manifests:
    """Where the three rules with an external input read it from. ``None`` is *not supplied*.

    Not defaults: this package resolves nothing. ``tools/check_corpus_conformance.py`` resolves the
    obfuscation dataset relative to the repository's parent, and ADR 0016 records why that belongs
    in the CLI -- under CI's checkout layout the two data repositories are *nested* rather than
    siblings, so a default that resolved would turn a forgotten flag into a working-looking gate.
    A ``None`` here reports its rule as *not evaluated* and names what was missing.
    """

    #: ``trap_manifest.json`` -- V11 (how a suspect column is worded) and V15 (which are marked).
    trap: Path | None = None
    #: ``trap_table_manifest.json`` -- V15's whole-decoy-table half.
    table: Path | None = None
    #: ``schema_rename_map.json`` -- V15 keys tables by their upstream BIRD name.
    rename: Path | None = None
    #: ``test_final.jsonl`` -- V12, the held-out-split leakage rule.
    test_split: Path | None = None


@dataclass(frozen=True, slots=True)
class ConformanceReport:
    """What one run found, and what it could not ask.

    ``not_evaluated`` is the half a caller must read. A rule that did not run reports **zero
    findings**, which is indistinguishable from a rule that passed -- and the rules with an external
    input are V11, V12 and V15, the last of which is the held-out-split leakage gate. That is why
    ``check_corpus_delta.py`` has an ``--every-rule-must-run`` flag and why it maps to "this checked
    nothing" rather than to "clean".
    """

    #: Rule id -> its findings. **A rule with nothing to say is absent**, so ``findings`` and
    #: ``not_evaluated`` between them never claim a rule both ran and did not.
    findings: Mapping[str, tuple[Finding, ...]]
    #: Rule id -> why it could not be answered here.
    not_evaluated: Mapping[str, str]
    #: ``asset_type`` -> how many, sorted by type. What the tree held, which is the population
    #: every count above is out of.
    assets_by_type: Mapping[str, int]
    #: Whole tree, or one file. The rules in :data:`WHOLE_TREE_ONLY` are only asked when true.
    whole_tree: bool

    @property
    def total(self) -> int:
        """Findings across every rule that ran."""
        return sum(len(lines) for lines in self.findings.values())

    @property
    def asset_count(self) -> int:
        return sum(self.assets_by_type.values())


def problems_with_corpus(root: Path, manifests: Manifests = Manifests()) -> ConformanceReport:
    """Every rule this engine has, over a whole corpus tree.

    Raises ``NotADirectoryError`` rather than reporting a clean tree for a path that is not one: a
    gate that answers "no findings" about a directory it never read is the silent green this whole
    package exists to prevent. An *empty* directory is a different thing and does report clean --
    read ``asset_count`` before believing it.
    """
    if not root.is_dir():
        raise NotADirectoryError(
            f"no corpus at {root}. A conformance report over a tree that is not there would be "
            "zero findings, which reads exactly like a clean corpus."
        )
    return _report(walk(root), whole_tree=True, manifests=manifests)


def problems_with_asset_file(path: Path, manifests: Manifests = Manifests()) -> ConformanceReport:
    """Every rule answerable from one asset file, and a stated reason for each that is not.

    What the rebuild loop calls after each write. The rules needing a second asset come back in
    ``not_evaluated`` rather than as zero findings, because a rule that silently skips is worse than
    one that fails.
    """
    return _report(load_assets(path), whole_tree=False, manifests=manifests)


def _report(assets: list[RawAsset], *, whole_tree: bool, manifests: Manifests) -> ConformanceReport:
    """The one dispatch. Both scopes run the same rules in the same order, and ``whole_tree``
    decides only whether the rules needing a second asset are asked or deferred."""
    findings: dict[str, list[Finding]] = defaultdict(list)
    for kind, a, path in assets:
        for rule, lines in check_local(kind, a, where_of(kind, a, path)).items():
            findings[rule].extend(lines)

    files = sorted({p for _, _, p in assets})
    findings["V14"].extend(check_loadable(files))
    findings["V16"].extend(check_delivery_closure(files))

    skipped: dict[str, str] = {}
    if whole_tree:
        for rule, answer in WHOLE_TREE_CHECKS.items():
            findings[rule].extend(answer(assets))
        if _present(manifests.trap) and _present(manifests.table) and _present(manifests.rename):
            findings["V15"].extend(
                check_suspect_set(assets, manifests.trap, manifests.table, manifests.rename)
            )
        else:
            skipped["V15"] = "needs the trap, table and rename manifests"
    else:
        for rule in WHOLE_TREE_ONLY:
            skipped[rule] = "needs the whole tree"

    # V11 and V12 run in **both** scopes: each reads one asset's own text against a file on disk, so
    # a single asset is a complete population for them. Outside the branch above rather than
    # duplicated inside it, so ``--file`` and the whole-tree walk cannot answer differently.
    #
    # The reason recorded when the input is missing is the manifest, never the tree. Those are two
    # different facts -- one says the rule cannot be answered here, the other that it can and the
    # input is absent -- and the second is the one a writer fixes.
    if _present(manifests.trap):
        findings["V11"].extend(check_suspect_summaries(assets, manifests.trap))
    else:
        skipped["V11"] = _absent(manifests.trap, "trap manifest")
    if _present(manifests.test_split):
        findings["V12"].extend(check_split_leak(assets, manifests.test_split))
    else:
        skipped["V12"] = _absent(manifests.test_split, "test split")

    return ConformanceReport(
        # Empty entries dropped, so "this rule found nothing" is one state and not two: ``V14`` and
        # ``V16`` are extended unconditionally above and would otherwise carry an empty list while
        # every clean rule beside them carries no key at all.
        findings={rule: tuple(lines) for rule, lines in findings.items() if lines},
        not_evaluated=skipped,
        assets_by_type=dict(sorted(Counter(kind for kind, _, _ in assets).items())),
        whole_tree=whole_tree,
    )


def _present(path: Path | None) -> bool:
    return path is not None and path.exists()


def _absent(path: Path | None, what: str) -> str:
    """Why a rule with an external input did not run. Two different facts, two sentences: a path
    that was never given is a caller's omission, a path that does not exist is a checkout."""
    return f"no {what} at {path}" if path is not None else f"no {what} was supplied"


def _every_dispatched_rule_is_described() -> None:
    """Import-time closure: every dispatched rule id is one :data:`RULE_DESCRIPTIONS` names.

    The description table and the dispatch were one file until this package existed, and a rule can
    now fall between them -- dispatched here and undescribed, so the CLI's report never prints its
    row and a reader counts twenty-one rules as the whole set. The rules :func:`check_local`
    answers are not enumerable statically; this closes the direction that is.
    """
    undescribed = sorted(set(WHOLE_TREE_ONLY) - set(RULE_DESCRIPTIONS))
    if undescribed:  # pragma: no cover - import-time guard
        raise AssertionError(
            f"WHOLE_TREE_ONLY names {undescribed}, which RULE_DESCRIPTIONS does not describe"
        )


_every_dispatched_rule_is_described()
