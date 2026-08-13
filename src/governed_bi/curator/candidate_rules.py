"""Two rules over an assembled candidate set: who may be asked, and what is already answered.

Split out of ``curator/elicitation.py`` at 960/1000 lines (ADR 0005 §6), and **not** along a
line count. That module's docstring says it "only decides WHAT to ask", and everything left in
it is a generator: a pure function of tables, columns and observed values that proposes
candidates from keyword gates. Neither function here generates anything. Both take a list of
:class:`~governed_bi.curator.clarifications.ClarificationRecord`\\ s that already exist — the
*combined* output of the keyword generator and ``curator/gaps.py``'s structural detectors — and
decide what reaches an admin. They lived in the keyword generator's file because one of the two
generators happened to be there, which is a fact about history rather than about the code.

The seam is visible in the call graph already: ``POST /elicitation/generate`` applies these two
after both generators have run and after ``gaps.apply_cluster_dependencies``, over one list.
That third rule stays in ``gaps.py`` because it consumes the gap scan's own ``gated_columns``
and cannot be stated without it; these two need only a record and the corpus.

``plain_name`` deliberately does **not** move. It is a phrasing helper the templates call while
building a question, not a rule applied to one afterwards — the line is "does this read a
finished record", and that function is on the far side of it.

The tests do **not** follow this module, which is the one place this split departs from
``e211f3f``'s precedent: both rules are only meaningful against a real candidate set, and both
existing suites already build one — ``tests/curator/test_elicitation.py`` has the generator
fixture the dedup tests need, and ``tests/curator/test_wizard_phrasing.py`` is where the language
guard's asymmetry is stated as a property over every generated question. Copying either fixture
into a third file to make the file names line up would be duplication bought with nothing.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Mapping, Sequence

from governed_bi.curator.clarifications import ClarificationRecord

__all__ = ["enforce_audience_language", "drop_already_answered"]


# ── who may be asked ────────────────────────────────────────────────────────────────────────


def _authored_labels(record: ClarificationRecord) -> list[str]:
    """The choice labels this module *wrote*, excluding the ones that are database values.

    A choice built as ``{"id": v, "label": v}`` is one distinct value read out of the column —
    B's whole payload, and the reason B exists: a domain owner must never *type* a value that
    can drift from the stored format, so what they pick has to be byte-exact. Every other choice
    on this surface has an id that names an option (``exclude``, ``different_fields``, a month
    number, a ``table.column``) and a label that is a sentence someone here composed.

    That distinction is the answer to :func:`~governed_bi.serve.schema_term_guard.find_schema_leak`'s
    own documented limitation. A camelCase proper noun is structurally indistinguishable from a
    leak — and measured live, the case is not hypothetical: ``app_store.playstore.Type`` holds
    ``NaN``, which the guard flags. But a proper noun can only honestly reach a business question
    as a *value*, never as prose, because prose is ours to phrase and a value is not ours to
    change. So the line is drawn there: authored text is guarded, stored values are exempt and
    shown verbatim.
    """
    return [
        str(choice.get("label") or "")
        for choice in (record.choices or ())
        if choice.get("id") != choice.get("label")
    ]


def enforce_audience_language(
    records: Sequence[ClarificationRecord],
) -> list[ClarificationRecord]:
    """Move a business-audience question that names a raw identifier onto the data tab.

    **Applied asymmetrically, and the asymmetry is the point** — a future reader who "fixes" the
    inconsistency breaks one of the two pilots. ``serve/schema_term_guard.find_schema_leak``
    already blocks dotted paths, snake_case and camelCase from reaching a business user in a live
    ``ask_user`` clarification, for Kindling: a restaurant owner cannot answer a question
    containing ``playstore.Type``. Running the same guard over **data**-audience questions would
    be the mirror-image defect — Power Kiosk's DBA is asked exactly which of two look-alike
    columns is authoritative, and stripping the identifiers leaves a question nobody can act on.
    Same text, opposite verdict, because the audience is different.

    **Reclassify, never drop and never rewrite.** The owner's standing decision is "list ALL
    gaps, don't truncate", so a question that reaches this branch is still asked — of the person
    who can read it. Rewriting it here instead would be a second, worse phrasing of a template
    that should be fixed where it is written; dropping it would delete a finding to hide a
    wording bug. The record's own text is returned unchanged so that what an admin sees is always
    what some template says.

    A backstop rather than the mechanism: every business template composes its object names
    through :func:`plain_name`, whose output cannot be identifier-shaped, so on all three
    measured schemas nothing takes this branch. That is the intended state. What this buys is
    that it stays true — a prompt instruction is not a control (``schema_term_guard``'s own
    docstring, ADR 0005 §1.5), and neither is a convention about how to write an f-string.

    Applied once, by ``POST /elicitation/generate``, over the assembled output of *both*
    generators — the same place and for the same reason ``gaps.apply_cluster_dependencies`` is
    applied there. Business-audience records come from both halves (B/C/E/S6 here, S1's
    describe-this-table question in ``gaps.py``), and a per-generator call would be two places to
    forget.
    """
    from governed_bi.serve.schema_term_guard import find_schema_leak

    out: list[ClarificationRecord] = []
    for record in records:
        if record.audience != "business":
            out.append(record)
            continue
        leak = find_schema_leak(record.question, *_authored_labels(record))
        out.append(replace(record, audience="data") if leak is not None else record)
    return out



# ── questions the corpus already answers ────────────────────────────────────────────────────


def _folded_question_ids(assets_by_id: Mapping[str, Any], schema: str | None) -> frozenset[str]:
    """Ids of the clarification-derived assets already in this schema's corpus.

    ``curator/clarification.py::draft_from_clarification`` mints
    ``clarification.<schema>.<sha256(question)[:16]>`` on **every** fold it performs, so the id
    of an answered question is a pure function of its text and one dict lookup decides whether
    the answer already exists. That prefix is the same discriminator
    ``api/curation_routes.py::_is_clarification_derived`` already relies on.
    """
    prefix = f"clarification.{schema}."
    return frozenset(asset_id for asset_id in assets_by_id if asset_id.startswith(prefix))


def _certified_terms(assets_by_id: Mapping[str, Any]) -> frozenset[str]:
    """Case-folded names and synonyms of every **certified** ``TermAsset``.

    ``certified`` and not ``proposed``, because that is the line ``corpus/analyst.py`` draws for
    what the engine may use: a draft nobody has approved is invisible to a served turn, so the
    engine still does not know what the term means and the question is still open.

    Clarification-derived terms are excluded. Their ``name`` is the question they came from
    (``draft_from_clarification``), so a certified one would enter this set as a whole sentence
    -- harmless, since no ambiguous term equals a sentence, but it would also be a second,
    accidental spelling of the check above.
    """
    from governed_bi.corpus.schema import ProvenanceStatus

    out: set[str] = set()
    for asset_id, asset in assets_by_id.items():
        if asset.asset_type.value != "term" or asset_id.startswith("clarification."):
            continue
        provenance = getattr(asset.audit, "provenance", None) if asset.audit is not None else None
        if provenance is None or provenance.status is not ProvenanceStatus.certified:
            continue
        out.add(str(asset.name).casefold())
        out.update(str(s).casefold() for s in getattr(asset, "synonyms", ()) or ())
    return frozenset(out)


def drop_already_answered(
    records: Sequence[ClarificationRecord],
    assets_by_id: Mapping[str, Any],
    *,
    schema: str | None,
) -> list[ClarificationRecord]:
    """Candidates minus the ones the corpus already answers.

    **Not the same rule as scope idempotency, and not in tension with "list ALL gaps".** The
    scope filter in :func:`generate_candidate_questions` stops one ledger proposing one candidate
    twice. This asks the broader question the ledger cannot: has this been *answered* somewhere
    else? A gap that is already filled is not a gap, and the owner's decision is about never
    truncating a finding to fit a quota -- it is not a licence to ask an admin something they
    have already told us.

    Two settlings, and each is exact rather than a similarity judgment:

    * **the question was answered and folded.** The corpus holds a
      ``clarification.<schema>.<hash of the question>`` asset. Broader than scope because the
      ledger is a single file at the corpus root while the fact lives as an asset beneath it --
      a rebuilt, relocated or hand-cleared ledger loses the record and keeps the fact.
    * **a curator already defined the term.** An A question asks which column a business term
      maps to; a certified ``TermAsset`` named that term is that answer, arrived at without the
      wizard. Only A is checked this way, because only A's subject *is* a term -- inferring that
      a certified term settles a value mapping or a join would be a similarity judgment, and
      this function makes none.

    Whether something is described, joined or contested is **not** re-checked here: those are the
    detectors' own gates (``gaps.py::_described``, ``join_edges``), evaluated against the same
    corpus, and a second copy of them would be a second answer to one question.

    **Dropping a record clears it from every ``blocked_by`` that named it**, and that is the
    whole reason this function returns rebuilt records rather than a filtered list. Found live
    through the admin UI the moment this landed on real ``app_store``: the near-duplicate
    question on ``playstore.Content Rating`` had been answered in an earlier session, the dedup
    suppressed it, and the two E questions ``apply_cluster_dependencies`` had already stamped as
    waiting on it were left pointing at an id in no ledger. ``unmet_prerequisites`` fails closed
    on a dangling id — correctly, and for a reason that does not apply here — so both cards
    rendered "Waiting" on "a question that is not in this ledger" and could never be answered.
    The dedup had turned two answerable questions into two dead ones.

    The edge is not dangling, it is **met**: the only reason its target was dropped is that its
    answer already exists in the corpus, which is exactly the state the dependency was waiting
    for. Ids this function did not drop are left alone — a prerequisite that is merely
    *unanswered* still blocks, and one already in the ledger resolves there.
    """
    folded = _folded_question_ids(assets_by_id, schema)
    terms = _certified_terms(assets_by_id)
    kept: list[ClarificationRecord] = []
    settled_ids: set[str] = set()
    for record in records:
        if _record_id_for_question(record.question, schema) in folded:
            settled_ids.add(record.id)
            continue
        if record.scope.startswith("elicitation:term:"):
            if record.scope.rsplit(":", 1)[-1].casefold() in terms:
                settled_ids.add(record.id)
                continue
        kept.append(record)
    if not settled_ids:
        return kept
    return [
        replace(record, blocked_by=tuple(b for b in record.blocked_by if b not in settled_ids))
        if settled_ids & set(record.blocked_by)
        else record
        for record in kept
    ]


def _record_id_for_question(question: str, schema: str | None) -> str:
    """The asset id ``draft_from_clarification`` would mint for this question.

    Built by calling that function rather than re-hashing here: the id format is its decision,
    and a second implementation of it would silently stop matching the day it changes.
    """
    from governed_bi.curator.clarification import draft_from_clarification

    return draft_from_clarification(question, "", schema=schema).id

