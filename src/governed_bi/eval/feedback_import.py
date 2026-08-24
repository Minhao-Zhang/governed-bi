"""Turn measured engine failures into observations the return path can act on (ADR 0015 §5).

**Why this and not a capture button.** On this deployment there is one person and there are zero
complaints, so every taxonomy and clustering choice in the design was a guess. The eval artifact is
not a guess: ``docs/failure-modes.md`` §1 partitions 438 v4 failures six ways, each row carries the
reference statement, and the partition reproduces from the artifact in code. So the loop starts with
real rows, the category is *derived* rather than picked, and the falsifiable claim on each row is a
fingerprint comparison rather than a sentence somebody typed.

**It lives in ``eval/`` and not in ``feedback/``.** ``eval`` sits above ``feedback`` in
``tools/check_imports.py::LAYERS``, so this is the one placement that can read both an artifact and
the store with no injected callable and no layer inversion — and it keeps ``feedback/`` free of
``sqlglot``, the dataset, and any knowledge that a benchmark exists.

**This module opens a leakage channel, and naming it is the point of this paragraph.** The
question text comes from ``test_final.jsonl`` — the **held-out** split — and the loop's whole
purpose is that a person reads it and then writes corpus prose. Nothing stops a phrase travelling
from a held-out question into a ``summary`` or a ``body``, and if one does, every EX number measured
afterwards is contaminated and the contamination is invisible.

Three things about that, in order of how much they are worth:

1. **The control already exists and is not this module's.** Conformance rule V12 is "no asset quotes
   a held-out question", and ``tools/check_train_only.py`` is the wider version — provenance citing
   the test split, verbatim containment, and an n-gram rate against a train-only control. The
   obligation this module creates is that **the bundle exporter runs V12 and treats a finding as
   fatal**, not as a report. That exporter does not exist yet; this sentence is the requirement it
   inherits, and a bundle produced without it is a bundle nobody may apply.
2. **A pass is not cleanliness.** ``check_train_only.py``'s own docstring says paraphrase leaks are
   undetectable. So the honest posture is that a human reading a held-out question and writing prose
   *from* it is a judgement no gate can check, and the person doing it has to know that — which is
   why the question text belongs on the review surface labelled as held-out rather than presented as
   neutral context.
3. **Importing from the train split instead would not work**, and the reason is worth recording so
   nobody proposes it as a fix. The failures are *measured* on the held-out split; a train-split
   import would be a queue of failures nobody observed.

**What it deliberately refuses to import.** The frozen-literal golds. They are a *dataset* defect:
unwinnable by design, and the engine matched a third of them by luck, so they oscillate between
correct and failed across arms and would churn the store. 85 of 438 is 19% of the queue permanently
unactionable, which is how a queue gets abandoned. They are counted in the report and excluded from
the rows, and ``--include-flags degenerate`` is there for somebody who wants to look at them
deliberately rather than by default.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping

from governed_bi.corpus.hash import corpus_content_hash
from governed_bi.eval.datalake import gold_table_ids, gold_tables, load_questions
from governed_bi.feedback.events import Category, Kind, Observation, ObservationState, Source
from governed_bi.feedback.store import FeedbackStore, mint_observation_id, utc_now

__all__ = [
    "ImportReport",
    "DATASET_DEFECT_FLAGS",
    "import_failures",
    "partition_failures",
]

#: Quality flags that make a failure the dataset's rather than the engine's or the corpus's.
#:
#: ``degenerate`` is a frozen-literal gold — a reference statement that selects a constant, so no
#: query over the warehouse can match it. ``exec_failed`` is a gold that does not run at all, and an
#: unexecutable reference grades every prediction wrong.
DATASET_DEFECT_FLAGS: frozenset[str] = frozenset({"degenerate", "exec_failed"})


@dataclass(frozen=True, slots=True)
class ImportReport:
    """What one import run saw, filed, and refused. Printed, and asserted against in tests.

    ``by_category`` is the derived partition, and it is the reason this report matters beyond one
    run: ``docs/failure-modes.md`` §1 carries the same table under a "hand-run, no producer in the
    tree" warning, and this is the producer.
    """

    artifact: str
    arm: str | None
    rows_read: int
    failures: int
    inserted: int
    already_present: int
    skipped_dataset_defect: int
    skipped_crashed: int
    skipped_full_coverage: int
    gold_unparsed: int
    missing_question_text: tuple[str, ...] = ()
    by_category: Mapping[str, int] = field(default_factory=dict)
    refused: tuple[str, ...] = ()

    #: ``corpus_content_hash`` of the corpus **loaded now**, or ``None`` when no corpus was given
    #: to compare against. ``None`` is "nobody told me", which is not "they agree".
    loaded_corpus_hash: str | None = None
    #: Distinct ``corpus_content_hash`` values the imported rows carry, most common first.
    artifact_corpus_hashes: tuple[str, ...] = ()
    #: How many imported rows were measured against a corpus that is **not** the one loaded now.
    #: ``None`` when there was nothing to compare against.
    rows_on_another_corpus: int | None = None
    #: How many imported rows carry no ``corpus_content_hash`` at all. Counted apart from the line
    #: above rather than folded into it: "measured somewhere else" and "nobody recorded where" are
    #: different facts, and adding them would be the sentinel ``corpus/hash.py`` refuses to return
    #: -- a missing value comparing unequal and reading as a finding.
    rows_with_no_corpus_hash: int = 0

    def render(self) -> str:
        """One block a person reads, with the excluded populations named rather than dropped."""
        lines = [
            f"artifact          {self.artifact}",
            f"arm               {self.arm or '(unnamed)'}",
            f"rows read         {self.rows_read}",
            f"failures          {self.failures}",
            "",
            f"filed             {self.inserted}",
            f"already present   {self.already_present}",
            "",
            "not imported, and why:",
            f"  dataset defect  {self.skipped_dataset_defect}  (frozen-literal or unexecutable gold)",
            f"  crashed         {self.skipped_crashed}  (an engine crash is not a corpus gap)",
            f"  full coverage   {self.skipped_full_coverage}  (every gold table was licensed; needs T4/T5, not T3)",
            f"  gold unparsed   {self.gold_unparsed}  (no table set to compare against)",
        ]
        lines += ["", "the corpus these rows were measured against:"]
        if self.loaded_corpus_hash is None:
            lines += [
                "  not compared    no corpus was given, so nothing here says whether these rows",
                "                  are about the corpus the engine loads. Pass --corpus-dir.",
            ]
        else:
            lines.append(f"  loaded now      {self.loaded_corpus_hash[:16]}")
            lines.append(
                "  rows measured   "
                + (", ".join(h[:16] for h in self.artifact_corpus_hashes[:3]) or "(none carried)")
            )
            lines.append(
                f"  ELSEWHERE       {self.rows_on_another_corpus} of {self.inserted} row(s) were "
                "measured against a corpus that is not the one loaded now."
            )
            if self.rows_on_another_corpus:
                lines += [
                    "                  They are kept: an observation is a record of something that",
                    "                  did happen, and dropping it would erase a true fact about a",
                    "                  real run. But 'still open' is not 'still true'. Measured on",
                    "                  2026-08-24: 52 of the 71 open rows no longer reproduced.",
                    "                  Settle it, do not read past it --",
                    "                  tools/reproduce_observation.py --state open --embed",
                ]
            if self.rows_with_no_corpus_hash:
                lines.append(
                    f"  no hash         {self.rows_with_no_corpus_hash} row(s) record no corpus at "
                    "all, so they are neither. Not counted above: unknown is not elsewhere."
                )
        if self.by_category:
            lines += ["", "filed by category:"]
            lines += [
                f"  {name:18} {count}"
                for name, count in sorted(self.by_category.items(), key=lambda kv: -kv[1])
            ]
        if self.missing_question_text:
            lines += [
                "",
                f"question text missing for {len(self.missing_question_text)} id(s): "
                f"{', '.join(self.missing_question_text[:5])}",
            ]
        if self.refused:
            lines += ["", "refused by the store:"] + [f"  {r}" for r in self.refused]
        return "\n".join(lines)


def partition_failures(
    rows: Iterable[Mapping[str, Any]],
) -> dict[str, list[Mapping[str, Any]]]:
    """The six-way partition of ``docs/failure-modes.md`` §1, computed rather than remembered.

    Buckets: ``dataset_defect``, ``crashed``, ``gold_unparsed``, ``coverage_miss``,
    ``full_coverage``. A row lands in exactly one, and the order of the tests is what makes that
    true — a frozen-literal gold whose tables were also not licensed is a dataset defect first,
    because patching the corpus cannot win a question no query can answer.
    """
    out: dict[str, list[Mapping[str, Any]]] = {
        "dataset_defect": [],
        "crashed": [],
        "gold_unparsed": [],
        "coverage_miss": [],
        "full_coverage": [],
    }
    for row in rows:
        if row.get("correct"):
            continue
        flags = set(row.get("quality_flags") or ())
        if flags & DATASET_DEFECT_FLAGS:
            out["dataset_defect"].append(row)
            continue
        if row.get("crashed"):
            out["crashed"].append(row)
            continue
        missing = _missing_tables(row)
        if missing is None:
            out["gold_unparsed"].append(row)
        elif missing:
            out["coverage_miss"].append(row)
        else:
            out["full_coverage"].append(row)
    return out


def import_failures(
    artifact: Path | str,
    *,
    dataset: Path | str,
    store: FeedbackStore,
    dry_run: bool = True,
    include_flags: frozenset[str] = frozenset(),
    corpus_dir: Path | str | None = None,
) -> ImportReport:
    """File one observation per coverage-miss failure. Idempotent, and a no-op under ``dry_run``.

    Only the ``coverage_miss`` bucket is imported, and that is the decision the whole cut rests
    on: it is the population **T3 can verify per question at no cost**, which makes the input set
    and the free half of the verification ladder the same set. The 280 full-coverage failures are
    genuine semantics, invisible below a paired arm, and importing them would fill the queue with
    rows nothing in this cut can act on.

    **``corpus_dir`` is the comparability half, and it changes nothing about what gets filed.**
    Every row carries the ``corpus_content_hash`` of the corpus it was measured on, and the loaded
    corpus has one too, and this compared them nowhere: on 2026-08-23, 71 of the 73 rows in the live
    store carried ``86ed1dbfef8b325e...`` (the other two carried none) while ``../BIRD-corpus`` hashed
    to ``6e5c7b4be83d5682...`` — a whole queue about a corpus the engine does not load, and no way to
    find that out. Re-checked on 2026-08-24: 52 of the 71 no longer reproduced.

    A mismatch is **reported and never acted on**. Not fatal on a commit and not a reason to drop a
    row: an observation is a record of something that *did happen*, so refusing to file it would
    erase a true fact about a real run without making the queue any more current — and it would keep
    the rows away from ``tools/reproduce_observation.py``, which is the only thing that can say which
    of them are still true. The number is the pointer to that check, not a gate in front of it.

    Raises ``FileNotFoundError`` for the artifact, the dataset, or a ``corpus_dir`` that is not
    there, and ``KeyError`` when the dataset cannot supply a question's text — a missing join is a
    broken dataset pairing, and filing a blank question would make a row that cannot be reviewed.
    """
    artifact_path, dataset_path = Path(artifact), Path(dataset)
    rows = list(_read_jsonl(artifact_path))
    buckets = partition_failures(rows)
    questions = _question_text(dataset_path)

    arm = next((str(r["arm"]) for r in rows if r.get("arm")), None)
    importable = list(buckets["coverage_miss"])
    if "degenerate" in include_flags or "exec_failed" in include_flags:
        importable += [
            r
            for r in buckets["dataset_defect"]
            if set(r.get("quality_flags") or ()) & include_flags
        ]

    inserted = already = 0
    refused: list[str] = []
    by_category: dict[str, int] = {}
    absent: list[str] = []

    for row in importable:
        qid = str(row.get("question_id") or "")
        text = questions.get(qid)
        if text is None:
            absent.append(qid)
            continue
        obs = _observation_from_row(row, question=text)
        by_category[obs.category.value if obs.category else "(none)"] = (
            by_category.get(obs.category.value if obs.category else "(none)", 0) + 1
        )
        if dry_run:
            inserted += 1
            continue
        try:
            returned = store.file(obs)
        except Exception as exc:  # noqa: BLE001 — one bad row must not lose the other 72
            refused.append(f"{qid}: {exc}")
            continue
        if returned == obs.observation_id:
            inserted += 1
        else:
            already += 1

    if absent:
        raise KeyError(
            f"{dataset_path} has no question text for {len(absent)} id(s) the artifact names, "
            f"starting {absent[:5]}. A missing join is a broken artifact/dataset pairing, and "
            "filing a blank question would make a row nobody can review."
        )

    loaded_hash = None if corpus_dir is None else corpus_content_hash(corpus_dir)
    carried = Counter(str(r.get("corpus_content_hash") or "") for r in importable)

    return ImportReport(
        artifact=str(artifact_path),
        arm=arm,
        rows_read=len(rows),
        failures=sum(len(v) for v in buckets.values()),
        inserted=inserted,
        already_present=already,
        skipped_dataset_defect=len(buckets["dataset_defect"]),
        skipped_crashed=len(buckets["crashed"]),
        skipped_full_coverage=len(buckets["full_coverage"]),
        gold_unparsed=len(buckets["gold_unparsed"]),
        by_category=by_category,
        refused=tuple(refused),
        loaded_corpus_hash=loaded_hash,
        artifact_corpus_hashes=tuple(h for h, _ in carried.most_common() if h),
        rows_on_another_corpus=(
            None
            if loaded_hash is None
            else sum(count for h, count in carried.items() if h and h != loaded_hash)
        ),
        rows_with_no_corpus_hash=carried.get("", 0),
    )


# ── row → observation ─────────────────────────────────────────────────────────


def _observation_from_row(row: Mapping[str, Any], *, question: str) -> Observation:
    """One artifact row as an observation, with every identifying field **copied**.

    Copied and not joined: a foreign key into the conversation store would return nothing six
    months from now, which is exactly when a reviewer reads the queue. ~2 KB per row buys a
    self-describing one.
    """
    outcome = str(row.get("outcome") or "")
    kind = Kind.wrong_answer if outcome == "answered" else Kind.from_refusal
    missing = _missing_tables(row) or set()
    return Observation(
        observation_id=mint_observation_id(),
        filed_at=utc_now(),
        source=Source.eval,
        kind=kind,
        state=ObservationState.open,
        category=_category_for(row, kind),
        question=question,
        outcome=outcome or None,
        refused_by=row.get("refused_by"),
        generated_sql=row.get("generated_sql"),
        licensed=tuple(str(t) for t in (row.get("licensed") or ())),
        schemas=tuple(str(s) for s in (row.get("schemas") or ())),
        missing_tables=tuple(sorted(missing)),
        gold_sql=row.get("gold_sql"),
        gold_fingerprint=row.get("gold_fingerprint"),
        pred_fingerprint=row.get("pred_fingerprint"),
        quality_flags=tuple(str(f) for f in (row.get("quality_flags") or ())),
        corpus_content_hash=row.get("corpus_content_hash"),
        prompt_set_hash=row.get("prompt_set_hash"),
        git_sha=(row.get("knobs_resolved") or {}).get("git_sha"),
        arm=row.get("arm"),
        question_id=str(row.get("question_id") or "") or None,
        db_id=row.get("db_id"),
        external_key=external_key(row),
    )


def _category_for(row: Mapping[str, Any], kind: Kind) -> Category | None:
    """The derived category. **The design's weakest assumption, replaced by a measurement.**

    A reader picks a category by guessing which of nine sentences fits. An artifact row carries
    the outcome and the reason, so the category follows from them — and the mapping is small
    enough to read, which is the test of whether the derivation is honest.
    """
    outcome = str(row.get("outcome") or "")
    refused_by = str(row.get("refused_by") or "")

    if outcome == "clarification":
        return Category.bad_clarification
    if outcome == "capped" or refused_by == "attempt_cap":
        # Its own member: "the attempt budget ran out" is a statement about the engine, where
        # `unverifiable` is a statement about a reader who is not here.
        return Category.attempt_capped
    if kind is Kind.from_refusal:
        # Every refusal in this population is a coverage miss, so the reader-facing sentence
        # ("this data exists and it should have been able to answer") is exactly true of it.
        return Category.false_refusal
    # A delivered answer that missed a gold table used the wrong data, whatever else it also did.
    return Category.wrong_scope


def external_key(row: Mapping[str, Any]) -> str:
    """Idempotency key: the arm, the question, and both treatment hashes.

    **Not** ``turn_id`` — an artifact carries none. **Not** ``run_id`` — it is constant per arm, so
    it would collapse every question into one key. Including both hashes is deliberate: the same
    question failing again under a different corpus or prompt set is a *second* observation, about
    a different treatment, with different evidence. Re-reading one artifact is idempotent; running
    a new arm is new information.
    """
    parts = "|".join(
        str(row.get(name) or "")
        for name in ("arm", "question_id", "corpus_content_hash", "prompt_set_hash")
    )
    return hashlib.sha256(parts.encode("utf-8")).hexdigest()[:32]


def _missing_tables(row: Mapping[str, Any]) -> set[str] | None:
    """Gold tables the turn was not allowed to read. ``None`` when the gold does not parse.

    Compared case-insensitively, because ``licensed`` carries the corpus's spelling and a gold
    statement carries the dataset's, and a case difference is not a coverage miss.

    Through ``gold_table_ids`` for the same reason one level up: ``licensed`` holds asset **ids**
    and the gold holds the engine's identifier, which differ wherever a name needed a slug. The
    string returned is still the gold's spelling — it is what the reviewer will look for in the
    statement on the row — but whether it is *missing* is decided against the id. Getting that
    backwards files ``coverage_miss`` against a table the turn was allowed to read, and sends
    somebody to curate an asset that already exists.

    The id is derived from the gold's **verbatim** spelling and lowercased afterwards, never the
    other way round: the slug's digest is taken over the exact name, so folding the case first
    derives a real-looking id for a table that does not exist and every slugged name reads as
    missing again.
    """
    gold = gold_tables(str(row.get("gold_sql") or ""))
    if gold is None:
        return None
    licensed = {str(t).lower() for t in (row.get("licensed") or ())}
    return {str(g).lower() for g in gold if not gold_table_ids(str(g)) & licensed}


def _read_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"no artifact at {path}")
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                yield json.loads(line)


def _question_text(dataset: Path) -> dict[str, str]:
    """``question_id`` → question, from the dataset the artifact was measured on.

    Read through ``eval/datalake.py::load_questions`` rather than by parsing the file here, so the
    ``gold_sql`` column this importer never uses is still the one the harness would have used —
    one reader of that file, not two.
    """
    if not dataset.exists():
        raise FileNotFoundError(
            f"no dataset at {dataset}. An eval artifact carries no question text on any row, so "
            "the text has to be joined by question_id from the split the arm was measured on."
        )
    return {
        str(q.get("question_id")): str(q.get("question") or "")
        for q in load_questions(dataset)
    }
