"""What changed since the last scan: the account a re-run owes an admin, in words.

The owner's third standing decision (2026-08-12, ``utku-ai-setup-wizard-gap-model.md`` § "Three
owner decisions"): *re-runnable, with honest reporting — a re-run diffs against already-confirmed
content and, when nothing is new, says so explicitly.* The suppression half of that has existed
since ``b587358``; this module is the missing half. ``POST /elicitation/generate`` returned
``n_generated`` and nothing else, so an admin who re-ran the scan read a **number** where the
decision asks for an **account** — and "0" is exactly the answer a structurally blind detector
gives, which is the failure ``curator/gaps.DetectorCoverage`` already exists to close for the
other half of the same sentence.

**What "confirmed" resolves to here, checked rather than assumed.** The doc says a re-run diffs
against already-*confirmed* content, and the obvious reading — ``ProvenanceStatus.certified`` —
would mean "nothing, ever":

* ``corpus/drafts.py::submit_draft`` stamps every write through
  ``corpus/provenance.py::restamp_model_authored``, which **raises** on ``certified``. So no fold
  can produce one.
* ``corpus/drafts.py::approve_draft`` is the only writer of ``certified`` in ``src/`` (the other
  four occurrences are two guards and two readers), and it is an admin clicking Approve on the
  drafts queue — a separate surface the wizard never touches.
* So every Setup Wizard answer lands ``proposed``, or ``draft`` when it was given without its
  prerequisite (``f718365``).

Confirmed therefore means **an answer exists**, and it has exactly two exact spellings, both of
which already existed as *suppression* rules and are reused rather than re-derived:

1. **the ledger says so** — a ``source="elicitation_wizard"`` record with
   ``status=answered``; and
2. **the corpus says so** — ``candidate_rules.drop_already_answered`` found either the folded
   ``clarification.<schema>.<hash>`` asset or a certified ``TermAsset`` naming an A-biz term.
   That second rule is the only place ``certified`` is load-bearing, and correctly: a curator who
   defined the term by hand did not go through the fold path.

A provenance status is deliberately **not** consulted beyond that: a ``proposed`` fold and a
``draft`` fold are both answers that exist, and treating ``draft`` as unanswered would make the
re-run re-ask a question whose answer is already in the corpus.

**The diff keys on ``scope``, not on question text.** The two keys are both live in this pipeline
and each is exact for its own job:

* ``scope`` is a candidate's identity — ``_record_id`` is a hash **of** it,
  ``append_if_new_scope`` dedups on it, and both generators derive it from schema objects
  (``elicitation:valuemap:<table>.<column>``). It survives a rewording.
* the ``clarification.<schema>.<sha256(question)>`` asset id is a hash of the *text*, because the
  corpus has no scope to hash — that is what ``drop_already_answered`` must match on, and it is
  not this module's to change.

Keying the report on scope is what keeps a phrasing pass from inflating "new": the phrasing
commits immediately before this one rewrote most question text, and a text-keyed diff would have
reported every previously-answered question as a fresh finding. The residual is stated rather
than hidden — with the ledger **gone** (cleared, rebuilt, a second deployment) a reworded
question is invisible to both keys and does report as new. See :func:`diff_scan_against_ledger`.

**Nothing here counts "no longer applicable", and the omission is measured.** The doc's Part 4
asks for a fourth bucket: a previously-proposed gap whose target table or column is gone. The set
arithmetic is trivial (in the ledger, not in this scan) and the claim it would make is false: the
scan does not derive every wizard record. ``elicitation:join:<a>:<b>`` is minted by
``curator/elicitation.py::maybe_generate_join_followup`` when an A answer lands on an unexpected
table, never by a detector, so on ``app_store``'s real ledger a live D follow-up would be reported
as "no longer applicable" on the very next scan. A bucket that is right about excluded tables and
wrong about every join follow-up is worse than no bucket.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, Sequence

from governed_bi.curator.clarifications import ClarificationRecord, ClarificationRecordStatus
from governed_bi.curator.elicitation import ELICITATION_SOURCE
from governed_bi.curator.gaps import SEVERITY_ORDER

__all__ = ["ScanReport", "diff_scan_against_ledger", "scan_report_payload"]


@dataclass(frozen=True, slots=True)
class ScanReport:
    """One scan's diff against what the ledger and the corpus already knew.

    Four buckets, disjoint by construction, and the fourth is the reason the third can be
    trusted — see :func:`diff_scan_against_ledger`.
    """

    #: Derived this run and not previously proposed. The records the caller appends.
    new: tuple[ClarificationRecord, ...]
    #: Previously proposed, still unanswered. Ledger records, not this scan's copies of them.
    still_open: tuple[ClarificationRecord, ...]
    #: Answered — so correctly absent from :attr:`new` rather than missing.
    settled: tuple[ClarificationRecord, ...]
    #: Answered **without** the prerequisite that would have warranted it, so the corpus fact
    #: landed ``draft`` and ``approve_draft`` refuses it. Not settled, and not re-askable either.
    stranded: tuple[ClarificationRecord, ...]

    @property
    def nothing_new(self) -> bool:
        """The state a client must not have to infer from an empty array."""
        return not self.new


def diff_scan_against_ledger(
    presented: Sequence[ClarificationRecord],
    settled_by_corpus: Sequence[ClarificationRecord],
    ledger: Sequence[ClarificationRecord],
) -> ScanReport:
    """Sort one scan's output into what is new, what is still waiting, and what got answered.

    ``presented`` is every candidate that survived ``candidate_rules.drop_already_answered`` and
    ``enforce_audience_language`` — the assembled output of both generators, **before** any
    scope-idempotency filter, because that filter is now this function's own first line rather
    than a rule applied twice upstream. ``settled_by_corpus`` is what the dedup removed;
    ``ledger`` is the ledger as it stood when the scan started.

    Only ``source="elicitation_wizard"`` ledger records are considered. A ``live_chat`` or
    ``curator`` row shares the file and is not this scan's to account for — and counting one as
    "still open" would put a mid-turn clarification into an onboarding report.

    The four buckets:

    * **new** — a ``presented`` scope the ledger has never held. These are what gets appended,
      and the count is what makes :attr:`ScanReport.nothing_new` true or false.
    * **still open** — a wizard record on the ledger with ``status=open``. Whether this scan
      re-derived it is deliberately not asked: see the module docstring on why "in the ledger,
      not in this scan" cannot be read as "no longer applicable".
    * **settled** — answered, by either of the two spellings the module docstring gives: the
      ledger record says ``answered``, or the corpus already held the answer and the dedup
      dropped the candidate before it reached the ledger at all. The second case is the design
      doc's ``beer_factory`` observation — facts settled by a live clarification or by hand
      curation, which the wizard's own ledger cannot see. Deduplicated by scope, since an
      ordinary wizard answer produces both.
    * **stranded** — answered with ``unmet_prerequisites_at_answer`` non-empty. **Subtracted from
      settled**, which is the whole reason this bucket exists: that answer folded at ``draft``
      (``curator/clarification.py::fold_ledger_answer_into_corpus``), ``approve_draft`` accepts
      ``proposed`` only, and nothing promotes a ``draft`` back — so calling it settled would tell
      an admin a question is closed when what actually happened is that it is stuck. It is also
      not re-proposed, because its scope is on the ledger; without this bucket a re-run would
      account for it nowhere at all, which is ``utku-ai-design-gaps`` #4 ("no clear edit path")
      arriving as silence.
    """
    wizard = [r for r in ledger if r.source == ELICITATION_SOURCE]
    known_scopes = {r.scope for r in wizard}

    still_open = tuple(r for r in wizard if r.status is ClarificationRecordStatus.open)
    answered = [r for r in wizard if r.status is ClarificationRecordStatus.answered]
    stranded = tuple(r for r in answered if r.unmet_prerequisites_at_answer)
    stranded_scopes = {r.scope for r in stranded}

    settled: list[ClarificationRecord] = [r for r in answered if r.scope not in stranded_scopes]
    settled_scopes = {r.scope for r in settled}
    # A candidate the corpus already answers and the ledger has never heard of: answered by some
    # other route entirely. Appended rather than merged, because the ledger has no row to prefer.
    settled += [
        r
        for r in settled_by_corpus
        if r.scope not in settled_scopes and r.scope not in stranded_scopes
    ]

    return ScanReport(
        new=tuple(r for r in presented if r.scope not in known_scopes),
        still_open=still_open,
        settled=tuple(settled),
        stranded=stranded,
    )


# ── the sentence an admin reads ─────────────────────────────────────────────────────────────
#
# Composed here and not in the client, for ``curator/elicitation_answers.py``'s reason: the
# wording is the deliverable, and a second copy of it in TypeScript is a second thing to keep
# true. Whoever reads the route with ``curl`` reads the same words the wizard prints.


def _tiers(records: Sequence[ClarificationRecord]) -> str:
    """``"2 T1, 3 T2"`` — worst tier first, empty tiers omitted, ``""`` for nothing.

    Unclassified records are counted and named rather than dropped: the wizard renders them in
    a trailing bucket for the same reason (an unclassified question is still a question).
    """
    counts = Counter(r.severity or "" for r in records)
    parts = [f"{counts[tier]} {tier}" for tier in SEVERITY_ORDER if counts.get(tier)]
    if counts.get(""):
        parts.append(f"{counts['']} unclassified")
    return ", ".join(parts)


def _new_clause(report: ScanReport) -> str:
    if not report.new:
        return "No new gaps found."
    noun = "gap" if len(report.new) == 1 else "gaps"
    return f"Found {len(report.new)} new {noun} — {_tiers(report.new)}."


def _still_open_clause(report: ScanReport) -> str:
    """What is carried forward, with its T1 count, because that is the tier an admin triages on.

    ``""`` when nothing is open — "0 still open" on a first scan reads as a finding, and there
    was nothing to be open.
    """
    count = len(report.still_open)
    if not count:
        return ""
    subject = "question from an earlier scan is" if count == 1 else (
        "questions from an earlier scan are"
    )
    t1 = sum(1 for r in report.still_open if r.severity == "T1")
    tail = f" ({t1} of them T1)." if t1 else "."
    return f"{count} {subject} still unanswered{tail}"


def _settled_clause(report: ScanReport) -> str:
    count = len(report.settled)
    if not count:
        return ""
    subject = "question was" if count == 1 else "questions were"
    return f"{count} {subject} already answered and {'was' if count == 1 else 'were'} not asked again."


def _stranded_clause(report: ScanReport) -> str:
    """The ``draft``-stranded answers, named rather than counted as settled.

    Says what the state actually is and stops there. It does not promise that re-running or
    answering the prerequisite fixes it, because neither does: ``approve_draft`` accepts
    ``proposed`` only, nothing promotes a ``draft``, and this wizard has no re-answer path
    (``utku-ai-design-gaps`` #4). Telling an admin a stuck answer will resolve itself would be
    worse than the silence this replaces — the point of the clause is that they can see it is
    stuck at all.
    """
    count = len(report.stranded)
    if not count:
        return ""
    if count == 1:
        return (
            "1 answer landed before the question it depends on: recorded as a draft nobody can "
            "certify, and this wizard cannot yet reopen it."
        )
    return (
        f"{count} answers landed before the questions they depend on: recorded as drafts nobody "
        "can certify, and this wizard cannot yet reopen them."
    )


def _nothing_on_file_clause(report: ScanReport) -> str:
    """The first-scan case, said out loud.

    Without it a first scan prints only "Found 18 new gaps", which is silent about the thing the
    owner's decision is actually about — whether anything was diffed against at all.
    """
    if report.still_open or report.settled or report.stranded:
        return ""
    return "Nothing was on file before this scan to compare against."


def scan_summary(report: ScanReport) -> str:
    """The whole account as one short paragraph an admin can read at a glance."""
    clauses = (
        _new_clause(report),
        _still_open_clause(report),
        _settled_clause(report),
        _stranded_clause(report),
        _nothing_on_file_clause(report),
    )
    return " ".join(c for c in clauses if c)


def _bucket(records: Sequence[ClarificationRecord]) -> dict[str, Any]:
    """One bucket on the wire: how many, of what tier, and **which** — by scope.

    Scopes rather than ids, because scope is the diff key (module docstring) and an id is a hash
    of it: a client that wants to point at a bucket's members should resolve them the same way
    this function separated them. Sorted, so two runs that found the same thing print the same
    thing.
    """
    counts = Counter(r.severity for r in records if r.severity)
    return {
        "count": len(records),
        "by_severity": {tier: counts[tier] for tier in SEVERITY_ORDER if counts.get(tier)},
        "scopes": sorted(r.scope for r in records),
    }


def scan_report_payload(report: ScanReport) -> dict[str, Any]:
    """:class:`ScanReport` as the JSON ``POST /elicitation/generate`` returns.

    ``nothing_new`` is a boolean and not something a client derives from ``len(new) == 0``: the
    owner's decision is that the state is *stated*, and a client that has to interpret an empty
    array is a client that can interpret it wrong (the wizard's own toast did exactly that —
    "the schema is already covered", which is a claim nothing measured).
    """
    return {
        "nothing_new": report.nothing_new,
        "summary": scan_summary(report),
        "new": _bucket(report.new),
        "still_open": _bucket(report.still_open),
        "settled": _bucket(report.settled),
        "stranded": _bucket(report.stranded),
    }
