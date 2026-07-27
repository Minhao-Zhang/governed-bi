"""Did the thing we are measuring actually happen?

An experiment compares arms that are supposed to differ. When they do not — when
the intervention never reaches the model — the arms still produce different
numbers, because the model is not deterministic. Those numbers look exactly like a
measured null result, and they get reported as one.

This has now happened twice on this project, and cost a full set of conclusions
each time:

* The Simulated-SME arm read its clarification ledger from a path a build step had
  already moved. It folded nothing, every run produced a corpus byte-identical to
  the arm it was supposed to improve on, and "SME adds no accuracy" was reported
  for weeks before the ledger bug was found.
* The "oracle" corpus — 9,154 gold business rules, built to establish the ceiling
  on what any semantic layer could be worth — wrote each note with ``scope:
  ['<schema>']``. Scope matching wants ``schema:<name>``, a bare asset id, or
  nothing. Every one of the 9,154 notes failed to match, none reached a prompt, and
  the median per-question prompt changed by *one token*. The resulting
  "+5 questions, not significant" was published as proof that enriching the
  semantic layer is an exhausted lever, and a roadmap was written on top of it.

Both were silent because nothing in the pipeline asserted the treatment had been
delivered. The corpus was on disk; the arm ran; rows came out. Every check that
existed passed.

So this module makes delivery a measured precondition rather than an assumption.
Two questions, both answered from artifacts:

**Did this arm deliver anything?** An arm whose corpus carries notes but which
injected none into any prompt is not a null result — it is a broken arm.

**Did these two arms deliver anything *different*?** This is the general form, and
the one that needs no per-arm knowledge: if two arms hand the model byte-identical
context on nearly every question, no comparison between them can mean anything,
whatever their corpora contain. It catches both failures above, and the next one,
without knowing what the treatment was supposed to be.

Failing closed matters here. An arm that did not *record* what it delivered has not
shown that it delivered anything, and reads as unverified rather than fine — the
same rule :func:`governed_bi.eval.index.quotable` applies to crash rates, for the
same reason.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

__all__ = [
    "ArmTreatment",
    "PairDivergence",
    "fingerprint_arm",
    "compare_arms",
    "treatment_reasons",
    "DEFAULT_MIN_DIVERGENCE",
]

#: An arm pair must deliver different context on at least this share of shared
#: questions to count as a real comparison. Not 1.0: two corpora can legitimately
#: agree on questions where neither has anything extra to say, and on a wide
#: benchmark most questions touch a handful of tables. But agreement on ~all of
#: them means the arms are the same experiment run twice. The oracle failure sat at
#: 0.0; a working treatment on the same benchmark moved essentially every row.
DEFAULT_MIN_DIVERGENCE = 0.05


@dataclass
class ArmTreatment:
    """What one arm actually handed to the model, summed over its rows."""

    arm: str
    n_rows: int = 0
    #: Rows carrying the fields at all. Absence is unverified, not zero.
    n_rows_observed: int = 0
    n_rows_with_notes: int = 0
    n_notes_injected: int = 0
    distinct_note_ids: int = 0
    n_rows_with_context_hash: int = 0
    distinct_context_hashes: int = 0
    mean_context_chars: float | None = None
    #: Notes present in the corpus this arm served, when the caller knows it.
    corpus_note_assets: int | None = None

    @property
    def observed(self) -> bool:
        """True when the rows recorded delivery at all."""
        return self.n_rows_observed > 0

    @property
    def note_injection_rate(self) -> float | None:
        if not self.n_rows_observed:
            return None
        return self.n_rows_with_notes / self.n_rows_observed

    def to_dict(self) -> dict[str, Any]:
        return {
            "arm": self.arm,
            "n_rows": self.n_rows,
            "n_rows_observed": self.n_rows_observed,
            "n_rows_with_notes": self.n_rows_with_notes,
            "n_notes_injected": self.n_notes_injected,
            "distinct_note_ids": self.distinct_note_ids,
            "note_injection_rate": self.note_injection_rate,
            "distinct_context_hashes": self.distinct_context_hashes,
            # Its denominator. Without it ``distinct_context_hashes: 1`` cannot be
            # told from "exactly one row recorded a hash at all" — and
            # ``docs/measurement.md`` already claimed this field was published.
            "n_rows_with_context_hash": self.n_rows_with_context_hash,
            "mean_context_chars": self.mean_context_chars,
            "corpus_note_assets": self.corpus_note_assets,
        }


@dataclass
class PairDivergence:
    """Whether two arms delivered materially different context."""

    arm_a: str
    arm_b: str
    n_shared: int = 0
    #: Questions where both sides recorded a context hash — the only ones on which
    #: divergence can actually be judged.
    n_comparable: int = 0
    n_different: int = 0
    reasons: list[str] = field(default_factory=list)

    @property
    def divergence(self) -> float | None:
        if not self.n_comparable:
            return None
        return self.n_different / self.n_comparable

    @property
    def delivered(self) -> bool:
        """True only on positive evidence that the arms differ."""
        d = self.divergence
        return d is not None and d >= DEFAULT_MIN_DIVERGENCE

    def to_dict(self) -> dict[str, Any]:
        return {
            "arm_a": self.arm_a,
            "arm_b": self.arm_b,
            "n_shared": self.n_shared,
            "n_comparable": self.n_comparable,
            "n_different": self.n_different,
            "divergence": self.divergence,
            "treatment_delivered": self.delivered,
            "reasons": list(self.reasons),
        }


def _rows_by_qid(rows: Iterable[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    out: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        qid = row.get("question_id") or row.get("request_id")
        if qid is not None:
            out[str(qid)] = row
    return out


def _delivery(row: Mapping[str, Any]) -> Mapping[str, Any]:
    """The delivery fields, whether at row top level or nested under ``meta``.

    Both drivers flatten provenance onto the row, but a row read straight from a
    solver's metadata has them one level down. Accepting both keeps this usable in
    a live assertion as well as over an archived file.
    """
    meta = row.get("meta")
    if isinstance(meta, Mapping) and "context_hash" in meta:
        return meta
    return row


def fingerprint_arm(
    arm: str,
    rows: Iterable[Mapping[str, Any]],
    *,
    corpus_note_assets: int | None = None,
) -> ArmTreatment:
    """Summarise what one arm delivered, from its generation rows."""
    fp = ArmTreatment(arm=arm, corpus_note_assets=corpus_note_assets)
    note_ids: set[str] = set()
    hashes: set[str] = set()
    chars: list[int] = []

    for row in rows:
        fp.n_rows += 1
        d = _delivery(row)
        n_notes = d.get("n_notes_injected")
        ids = d.get("injected_note_ids")
        chash = d.get("context_hash")
        cchars = d.get("context_chars")

        observed = any(v is not None for v in (n_notes, ids, chash, cchars))
        if not observed:
            continue
        fp.n_rows_observed += 1

        if isinstance(ids, (list, tuple)):
            note_ids.update(str(i) for i in ids)
            if ids:
                fp.n_rows_with_notes += 1
            fp.n_notes_injected += len(ids)
        elif isinstance(n_notes, int):
            fp.n_notes_injected += n_notes
            if n_notes:
                fp.n_rows_with_notes += 1

        if isinstance(chash, str) and chash:
            fp.n_rows_with_context_hash += 1
            hashes.add(chash)
        if isinstance(cchars, int):
            chars.append(cchars)

    fp.distinct_note_ids = len(note_ids)
    fp.distinct_context_hashes = len(hashes)
    fp.mean_context_chars = (sum(chars) / len(chars)) if chars else None
    return fp


def compare_arms(
    arm_a: str,
    rows_a: Iterable[Mapping[str, Any]],
    arm_b: str,
    rows_b: Iterable[Mapping[str, Any]],
) -> PairDivergence:
    """Did these two arms hand the model different context?

    Compares per-question context hashes. Questions where either side lacks a hash
    are excluded from the denominator and reported, so a run predating the hash does
    not read as a delivered treatment — it reads as unverified.
    """
    by_a, by_b = _rows_by_qid(rows_a), _rows_by_qid(rows_b)
    shared = sorted(set(by_a) & set(by_b))
    result = PairDivergence(arm_a=arm_a, arm_b=arm_b, n_shared=len(shared))

    n_missing = 0
    for qid in shared:
        ha = _delivery(by_a[qid]).get("context_hash")
        hb = _delivery(by_b[qid]).get("context_hash")
        if not isinstance(ha, str) or not isinstance(hb, str) or not ha or not hb:
            n_missing += 1
            continue
        result.n_comparable += 1
        if ha != hb:
            result.n_different += 1

    if not shared:
        result.reasons.append(
            f"{arm_a} and {arm_b} share no question ids, so nothing can be compared"
        )
        return result

    if n_missing:
        result.reasons.append(
            f"{n_missing} of {len(shared)} shared questions lack a context_hash on "
            "one or both sides — those rows predate delivery recording and cannot "
            "show that the arms differed"
        )

    if result.n_comparable == 0:
        result.reasons.append(
            f"no question has a context_hash on both sides: whether {arm_a} and "
            f"{arm_b} delivered different context is unverified, not verified-equal"
        )
        return result

    if not result.delivered:
        result.reasons.append(
            f"{arm_a} and {arm_b} delivered identical context on "
            f"{result.n_comparable - result.n_different} of {result.n_comparable} "
            f"comparable questions (divergence "
            f"{result.divergence:.1%} < {DEFAULT_MIN_DIVERGENCE:.0%}) — the arms are "
            "the same experiment run twice, and any difference between their scores "
            "is nondeterminism, not an effect"
        )
    return result


def treatment_reasons(
    fingerprints: Sequence[ArmTreatment],
    divergences: Sequence[PairDivergence] = (),
) -> list[str]:
    """Every reason the delivered treatments make this run unsafe to quote.

    Returns an empty list when delivery is verified. Designed to be appended to
    :func:`governed_bi.eval.index.quotable`'s reasons, so a broken arm disqualifies
    the run in the ledger rather than in someone's memory.
    """
    reasons: list[str] = []

    for fp in fingerprints:
        if fp.n_rows and not fp.observed:
            reasons.append(
                f"arm {fp.arm} recorded no delivery fields on any of its {fp.n_rows} "
                "rows — what reached the model is unknown, so its numbers cannot be "
                "attributed to its corpus"
            )
            continue
        # A corpus that carries notes but injected none is the exact signature of
        # the two failures this module exists for.
        if fp.corpus_note_assets and not fp.n_notes_injected:
            reasons.append(
                f"arm {fp.arm} served a corpus holding {fp.corpus_note_assets} notes "
                "and injected zero into any prompt — the treatment was built but "
                "never delivered (check note scope prefixes and publication_status)"
            )

    for pair in divergences:
        if not pair.delivered:
            reasons.extend(pair.reasons)

    return reasons


def divergence_table(divergences: Iterable[PairDivergence | Mapping[str, Any]]) -> str:
    """Human-readable arm-pair divergence, for the run report.

    Accepts either the dataclass or the dict it serialises to, so the same renderer
    works in-process and over a ``summary.json`` read back from disk.
    """
    rows = [d.to_dict() if isinstance(d, PairDivergence) else dict(d) for d in divergences]
    if not rows:
        return "(no arm pairs compared)"
    labels = [f"{r.get('arm_a')} vs {r.get('arm_b')}" for r in rows]
    width = max(len(label) for label in labels)
    lines = [f"{'pair'.ljust(width)}  {'diverged':>18}  verdict"]
    for label, r in zip(labels, rows):
        divergence = r.get("divergence")
        if divergence is None:
            shown, verdict = "unverified", "CANNOT COMPARE"
        else:
            shown = f"{r.get('n_different')}/{r.get('n_comparable')} ({divergence:.1%})"
            if r.get("expected_identical"):
                # A replicate pair. Identical context is the design, so the usual
                # verdict is inverted: divergence here is the defect.
                verdict = (
                    "REPLICATE DRIFTED"
                    if r.get("replicate_drifted")
                    else "replicate (identical as designed)"
                )
            elif r.get("treatment_delivered"):
                verdict = "ok"
            else:
                verdict = "TREATMENT NOT DELIVERED"
        lines.append(f"{label.ljust(width)}  {shown:>18}  {verdict}")
    return "\n".join(lines)
