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
        return GateResult("GATE-RECALL", True, "no questions")
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
    return GateResult("GATE-RECALL", True, f"recall@3={recall:.3f} n={len(questions)}")


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
    """Convenience bundle used by CI tests."""
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
