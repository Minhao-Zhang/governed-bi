"""Offline routing/note gates for M4 (R4 / R10).

These are CI-friendly HashingEmbedder proxies — not live EX. Live EX ON-vs-OFF
is a documented manual gate (see implementation plan §5).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..corpus.schemas import NoteAsset, ProvenanceStatus, TableAsset
from ..retrieval import retrieve
from ..retrieval.schema_router import list_schemas, shortlist_schemas
from ..retrieval.triggers import fire_triggers

if TYPE_CHECKING:
    from ..config import Settings
    from ..corpus import Corpus
    from ..llm import Embedder


@dataclass(frozen=True)
class GateResult:
    name: str
    passed: bool
    detail: str
    # A gate that could not actually run (missing inputs, cue didn't fire, empty
    # corpus) reports skipped=True. It stays passed=True so CI does not fail on a
    # setup gap, but callers/summaries must not count it as a real green pass.
    skipped: bool = False


@dataclass(frozen=True)
class GateSummary:
    """Fold of a gate run, with skips counted apart from real passes.

    ``all(r.passed)`` cannot see a skip — a skipped gate carries ``passed=True``
    deliberately — so a corpus/settings combination that skips every gate reads as
    fully green. That is the distinction this type exists to keep: ``verdict`` is
    ``"inconclusive"`` whenever no gate produced an actual verdict, which is the
    difference between "the gates held" and "the gates never ran".
    """

    n_gates: int
    n_passed: int  # ran AND passed; a skip is not counted here
    n_failed: int
    n_skipped: int
    verdict: str  # "pass" | "fail" | "inconclusive"
    detail: str

    @property
    def clean_pass(self) -> bool:
        """Every gate ran and passed — the only state that should read as green."""
        return self.verdict == "pass" and self.n_skipped == 0


def note_injection_recall_proxy(
    corpus: "Corpus",
    questions: list[tuple[str, set[str]]],
    *,
    top_k: int = 8,
) -> GateResult:
    """R4 offline proxy: notes ON must not drop table recall@k vs notes OFF.

    ``questions`` is ``(question, gold_table_ids)``. Compares retrieve() with
    ``note_k=5`` vs ``note_k=0``; fails if ON misses a gold table that OFF hit.
    """
    regressions: list[str] = []
    for q, gold in questions:
        on = set(retrieve(corpus, q, top_k=top_k, note_k=5).table_ids)
        off = set(retrieve(corpus, q, top_k=top_k, note_k=0).table_ids)
        lost = (gold & off) - on
        if lost:
            regressions.append(f"{q!r} lost {sorted(lost)}")
    if regressions:
        return GateResult("no-EX-regression-proxy", False, "; ".join(regressions[:5]))
    return GateResult(
        "no-EX-regression-proxy",
        True,
        f"checked {len(questions)} questions; notes ON did not drop gold tables",
        # Zero questions compared nothing: the loop above never ran, so this is a
        # setup gap, not evidence that notes ON is safe.
        skipped=not questions,
    )


def gate_recall(
    corpus: "Corpus",
    questions: list[tuple[str, str]],
    *,
    settings: "Settings",
    embedder: "Embedder | None" = None,
    top_k: int = 3,
    baseline_recall: float | None = None,
) -> GateResult:
    """GATE-RECALL: fraction of questions whose true schema is in shortlist@top_k."""
    if not questions:
        return GateResult("GATE-RECALL", True, "no questions", skipped=True)
    hits = 0
    for q, true_schema in questions:
        short = shortlist_schemas(
            corpus, q, top_k=top_k, embedder=embedder, settings=settings
        )
        if true_schema in short:
            hits += 1
    recall = hits / len(questions)
    if baseline_recall is not None and recall + 1e-9 < baseline_recall:
        return GateResult(
            "GATE-RECALL",
            False,
            f"recall@3={recall:.3f} < baseline {baseline_recall:.3f}",
        )
    return GateResult(
        "GATE-RECALL",
        True,
        f"recall@3={recall:.3f} n={len(questions)}",
        # Without a baseline there is nothing to regress against, so this branch is
        # tautological: recall was measured but no verdict was reached. Reporting it
        # as a pass is how a caller ends up believing a regression was ruled out.
        skipped=baseline_recall is None,
    )


def gate_adv_wrong_note(
    corpus: "Corpus",
    question: str,
    *,
    true_schema: str,
    wrong_schema: str,
    settings: "Settings",
    embedder: "Embedder | None" = None,
    top_k: int = 3,
) -> GateResult:
    """GATE-ADV-WRONG-NOTE: a certified wrong-schema PIN must not evict true schema.

    Injects a temporary certified note with a keyword from ``question`` scoped to
    ``schema:wrong_schema``, enables pins, and asserts ``true_schema`` remains in
    the shortlist (or that wrong PIN does not solely replace the true hit).
    """
    schemas = set(list_schemas(corpus))
    if true_schema not in schemas or wrong_schema not in schemas:
        return GateResult(
            "GATE-ADV-WRONG-NOTE",
            True,
            "skipped: need both schemas in corpus",
            skipped=True,
        )

    # Baseline without the adversarial note.
    base = shortlist_schemas(
        corpus, question, top_k=top_k, embedder=embedder, settings=settings
    )
    if true_schema not in base:
        return GateResult(
            "GATE-ADV-WRONG-NOTE",
            True,
            "skipped: true schema not in baseline shortlist",
            skipped=True,
        )

    from dataclasses import replace

    from ..corpus import Corpus as CorpusCls

    keyword = question.split()[0] if question.split() else "pinme"
    evil = NoteAsset(
        id="note_adv_wrong_schema_pin",
        kind="routing",
        scope=[f"schema:{wrong_schema}"],
        summary=f"Adversarial pin toward {wrong_schema}",
        triggers=[{"kind": "keyword", "value": keyword}],
        publication_status=ProvenanceStatus.certified,
        activation="on_match",
    )
    poisoned = CorpusCls(assets=[*corpus.assets, evil])
    pin_settings = replace(
        settings, pin_triggers_enabled=True, pin_require_certified=True, pin_max=3
    )
    # Confirm the pin fires.
    fired = fire_triggers(poisoned, question, settings=pin_settings)
    if evil.id not in fired:
        return GateResult(
            "GATE-ADV-WRONG-NOTE",
            True,
            "skipped: adversarial keyword did not fire",
            skipped=True,
        )
    after = shortlist_schemas(
        poisoned, question, top_k=top_k, embedder=embedder, settings=pin_settings
    )
    if true_schema not in after:
        return GateResult(
            "GATE-ADV-WRONG-NOTE",
            False,
            f"certified wrong-schema PIN evicted {true_schema}; shortlist={after}",
        )
    return GateResult(
        "GATE-ADV-WRONG-NOTE",
        True,
        f"true schema {true_schema} survived PIN; shortlist={after}",
    )


def run_offline_note_gates(
    corpus: "Corpus",
    *,
    settings: "Settings",
    embedder: "Embedder | None" = None,
) -> list[GateResult]:
    """Convenience bundle used by CI tests. **Not a measurement.**

    Read the limitation before quoting anything from this: the gold pairs are built
    from the table descriptions the gates then retrieve *against* (see the loop
    below), so a passing result means the embedder can find a document from its own
    text. It cannot fail unless retrieval is catastrophically broken, and it says
    nothing about recall on real questions (AUDIT E5).

    Kept because that smoke-level assurance is genuinely worth having in CI — a dead
    embedder or a broken PIN path does show up here — and because the PIN-off
    baseline below makes GATE-RECALL non-tautological in the one dimension that
    matters (a wrong PIN must not reduce recall). It has no production caller and
    should not acquire one; real retrieval measurement is ``eval/retrieval_eval.py``
    against the obfuscated split.
    """
    # Build cheap gold pairs from tables present in the corpus.
    tables = [a for a in corpus.assets if isinstance(a, TableAsset)]
    questions: list[tuple[str, set[str]]] = []
    schema_qs: list[tuple[str, str]] = []
    for t in tables[:5]:
        q = (t.description or t.physical_name or t.id).split(".")[0][:80]
        if not q.strip():
            continue
        questions.append((q, {t.id}))
        schema_qs.append((q, t.schema))
    # Baseline recall with PINs disabled; GATE-RECALL then asserts the active
    # settings do not drop below it (a wrong PIN must not reduce recall). Without
    # a baseline the gate is tautological (always passes).
    from dataclasses import replace as _replace

    baseline_recall: float | None = None
    if schema_qs:
        pins_off = _replace(settings, pin_triggers_enabled=False)
        base_hits = sum(
            1
            for q, sch in schema_qs
            if sch
            in shortlist_schemas(corpus, q, top_k=3, embedder=embedder, settings=pins_off)
        )
        baseline_recall = base_hits / len(schema_qs)
    results = [
        note_injection_recall_proxy(corpus, questions),
        gate_recall(
            corpus,
            schema_qs,
            settings=settings,
            embedder=embedder,
            baseline_recall=baseline_recall,
        ),
    ]
    if len({t.schema for t in tables}) >= 2:
        schemas = sorted({t.schema for t in tables})
        results.append(
            gate_adv_wrong_note(
                corpus,
                schema_qs[0][0],
                true_schema=schemas[0],
                wrong_schema=schemas[1],
                settings=settings,
                embedder=embedder,
            )
        )
    return results


def summarise_gates(results: list[GateResult]) -> GateSummary:
    """Fold gate results into one verdict that a skip cannot pass off as green.

    Every caller of :func:`run_offline_note_gates` should assert on this rather
    than on ``all(r.passed)``: the latter is satisfied by a run in which nothing
    ran at all.
    """
    failed = [r for r in results if not r.passed]
    skipped = [r for r in results if r.passed and r.skipped]
    passed = [r for r in results if r.passed and not r.skipped]
    if failed:
        verdict = "fail"
    elif passed:
        verdict = "pass"
    else:
        # No failures and no real passes: nothing was measured (an empty corpus, a
        # cue that never fired). "pass" here would be a fabricated green.
        verdict = "inconclusive"
    parts = [
        f"{len(results)} gate(s): {len(passed)} passed, "
        f"{len(failed)} failed, {len(skipped)} skipped"
    ]
    if failed:
        parts.append("failed: " + ", ".join(r.name for r in failed))
    if skipped:
        parts.append(
            "skipped: " + ", ".join(f"{r.name} ({r.detail})" for r in skipped)
        )
    return GateSummary(
        n_gates=len(results),
        n_passed=len(passed),
        n_failed=len(failed),
        n_skipped=len(skipped),
        verdict=verdict,
        detail="; ".join(parts),
    )
