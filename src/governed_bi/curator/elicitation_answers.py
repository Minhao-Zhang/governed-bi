"""What an answered Setup Wizard question becomes: one composed, self-contained sentence.

Split out of ``curator/elicitation.py`` at 912/1000 lines (ADR 0005 §6), along the seam that
module's own docstring already declares — *"this module only decides WHAT to ask"*. Everything
here is the other half, and it has no overlap with generation: it reads a
:class:`~governed_bi.curator.clarifications.ClarificationRecord` that already exists and the
admin's input, and produces the text
``curator/clarification.py::draft_from_clarification`` writes into the corpus.

**The discriminator is the record's scope, not its category letter.** Five letters describe a
*knowledge type* and the wizard's UI groups by them; they do not say what an answer means.
Category A carries four unrelated questions — "which column is this term", "what is this table",
"what do these columns hold", "why is this column flagged" — and composing all four through A's
``'{term}' maps to {answer}`` frame produced, measured live on real ``app_store``:

    "'app_store.playstore' maps to One row per app listing.."

Wrong framing (nobody asked what a term maps to), wrong subject (the scope tail is a table id,
not a term) and a doubled full stop. The same frame on a ``suspect``-column question would have
read "'beer_factory.kunden.email' maps to backfilled before 2019".

The scope, by contrast, is minted by exactly one producer per question shape and already names
it: ``elicitation:describetable:…``, ``elicitation:duplicate:…``, ``elicitation:valuemap:…``. So
:data:`_COMPOSERS` is a table from scope kind to composer, and adding a question shape without
adding its composer fails a test rather than silently composing the empty string — which is the
failure mode this file exists because of (``d17d6e0``: category D was a column picker whose
branch read only freeform, so the highest-severity question in the wizard discarded its answer).

**The question is in the audience's language; the fact is in the schema's.** A business-audience
question says ``'content rating'`` because a restaurant owner is reading it
(``curator/elicitation.py::plain_name``). The sentence composed here says
``playstore.content_rating``, because its reader is the retrieval layer and a later DBA, and a
corpus fact that cannot be tied back to a column is not a fact. The two are deliberately not the
same string.
"""

from __future__ import annotations

from typing import Callable, Sequence

from governed_bi.curator.clarifications import ClarificationRecord

__all__ = ["compose_elicitation_answer_text"]

#: What a composed sentence may already end with. ``…`` is here because
#: ``curator/clarification.py::_truncated`` appends it.
_TERMINATORS: tuple[str, ...] = (".", "!", "?", "…")


def _sentence(text: str) -> str:
    """``text`` trimmed and terminated exactly once.

    One helper rather than a full stop inside each f-string, because the input is a human's
    free text and half of them end in a full stop already. That is where the doubled terminator
    in the measured defect came from, and it is a property of every branch, not of the one that
    happened to be looked at.
    """
    text = text.strip()
    if not text:
        return ""
    return text if text.endswith(_TERMINATORS) else f"{text}."


def _joined(items: Sequence[str]) -> str:
    """``a``, ``a and b``, ``a, b and c`` — a list a human reads, not ``', '.join``."""
    if len(items) <= 1:
        return items[0] if items else ""
    return f"{', '.join(items[:-1])} and {items[-1]}"


def _qualified(record: ClarificationRecord) -> str:
    """``table.column``, ``table``, or ``""`` — how a composed fact names its subject."""
    if record.target_table and record.target_column:
        return f"{record.target_table}.{record.target_column}"
    return record.target_table or ""


def _scope_kind(scope: str) -> str:
    """The middle segment of ``elicitation:<kind>:<rest>``."""
    parts = scope.split(":", 2)
    return parts[1] if len(parts) >= 3 else ""


def _scope_rest(scope: str) -> str:
    parts = scope.split(":", 2)
    return parts[2] if len(parts) >= 3 else ""


# ── one composer per question shape ─────────────────────────────────────────────────────────
#
# Each takes the record, the picked choice's label (``""`` when none), the picked ids (for a
# checklist), and the freeform text (``""`` when none), and returns an unterminated sentence.
# :func:`compose_elicitation_answer_text` terminates it, so no branch can forget to.


def _compose_term(rec: ClarificationRecord, label: str, _picks: list[str], freeform: str) -> str:
    """A: which table/column an ambiguous business term maps to."""
    term = _scope_rest(rec.scope)
    target = freeform or label
    return f"'{term}' maps to {target}" if target else ""


def _compose_rule(rec: ClarificationRecord, label: str, _picks: list[str], freeform: str) -> str:
    """C: a business-rule constant. Freeform first — the widget is a number field."""
    value = freeform or label
    return f"Fiscal year starts in month {value}" if value else ""


def _compose_exclusion(
    rec: ClarificationRecord, label: str, _picks: list[str], freeform: str
) -> str:
    """E and S6: whether a sentinel value is excluded by default.

    The label carries the column and the value in business words
    (``curator/elicitation.py::_exclusion_choices``); the qualified column is prefixed here, so
    the fact says which physical column it is about. Freeform is appended rather than replacing
    the pick: "leave them out, except for the 2019 backfill" is two facts and both matter.
    """
    parts = [p for p in (label, freeform) if p]
    return f"{_qualified(rec)} — {'; '.join(parts)}" if parts else ""


def _compose_valuemap(
    rec: ClarificationRecord, _label: str, picks: list[str], freeform: str
) -> str:
    """B: which stored values a business term covers.

    Both halves are the answer, and the question now asks for both: the picks are the values and
    the freeform is what the admin calls them. Either alone is still a fact — a name with no
    values is a definition to bind later, values with no name is a grouping someone recognised.
    """
    column = _qualified(rec)
    if picks and freeform:
        return f"In {column}, {freeform!r} means {_joined(picks)}"
    if picks:
        return f"In {column}, these values count as one group: {_joined(picks)}"
    return f"For {column}: {freeform}" if freeform else ""


def _compose_table_description(
    rec: ClarificationRecord, _label: str, _picks: list[str], freeform: str
) -> str:
    """S1, table half: what one row of a table is — the ``grain``, in the owner's words.

    A legitimate corpus fact, and it needs no new asset type to carry it: every fold produces a
    ``TermAsset`` whose ``body`` is free text and whose ``summary`` is what retrieval sees
    (``curator/clarification.py::draft_from_clarification``; this repo has no ``NoteAsset`` —
    the eight types are schema/table/column/join/metric/term/few-shot/negative-example). What it
    needed was a frame that states the subject, because "One row per app listing" on its own is
    unattached to any table once the question is truncated away.
    """
    return f"What one row of {rec.target_table} represents: {freeform}" if freeform else ""


def _compose_column_descriptions(
    rec: ClarificationRecord, _label: str, picks: list[str], freeform: str
) -> str:
    """S1, column half: what a table's cryptic columns hold.

    Batched one question per table (93 individual ones would push every T1 off the first
    screens), so the checklist ids are the columns and the freeform is what they hold. Three
    arrivals, three facts:

    * **picks + freeform** — the answer this question is for.
    * **freeform alone** — prose about the table's columns, with no shortlist.
    * **picks alone** — the one that used to compose ``""``. It is still a fact and a modest
      one: the admin has read the list and said which names do not speak for themselves. That
      is worth recording *and* it is worth being honest that it is not a description — hence
      the wording. The alternative on the table was to drop it, and dropping an admin's input
      because it is weak evidence is the exact defect ``d17d6e0`` fixed.
    """
    table = rec.target_table
    if picks and freeform:
        return f"In {table}, {_joined(picks)}: {freeform}"
    if freeform:
        return f"About the columns of {table}: {freeform}"
    if picks:
        verb = "needs" if len(picks) == 1 else "need"
        return (
            f"In {table}, {_joined(picks)} {verb} a description: "
            f"{'its' if len(picks) == 1 else 'their'} "
            f"{'name does' if len(picks) == 1 else 'names do'} not say what "
            f"{'it holds' if len(picks) == 1 else 'they hold'}"
        )
    return ""


def _compose_reliability(
    rec: ClarificationRecord, _label: str, _picks: list[str], freeform: str
) -> str:
    """S4: what is wrong with a column something already flagged unreliable."""
    return f"{_qualified(rec)}: {freeform}" if freeform else ""


def _compose_duplicate(
    rec: ClarificationRecord, label: str, _picks: list[str], freeform: str
) -> str:
    """S3: which of two look-alike columns of one table is authoritative.

    The picked label already reads as a sentence (``curator/gaps.py::_duplicate_wording``), so
    it is used verbatim — except for "They are different fields, both correct", which names
    neither field. The two columns are recoverable from the record's own other choices, which is
    where they were put.
    """
    if freeform:
        return f"{rec.target_table}: {freeform}" if rec.target_table else freeform
    if not label:
        return ""
    named = [str(c["id"]) for c in (rec.choices or ()) if "." in str(c.get("id") or "")]
    if len(named) == 2 and "." not in label.split(" ")[0]:
        return f"{_joined(named)}: {label[0].lower()}{label[1:]}"
    return label


def _compose_join_keys(
    rec: ClarificationRecord, label: str, _picks: list[str], freeform: str
) -> str:
    """S2 T1: which of two competing candidate keys joins a table pair.

    The choices are bare qualified identifiers — the only shape a grounded column picker can
    offer — so the composed fact has to supply the verb and the other table. Both come off the
    scope, which is ``elicitation:joinkeys:<left table id>|<right table id>``.
    """
    tables = [part.split(".")[-1] for part in _scope_rest(rec.scope).split("|")]
    if freeform:
        return f"{_joined(tables)} join: {freeform}"
    return f"{_joined(tables)} join on {label}" if label else ""


def _compose_join_path(
    rec: ClarificationRecord, label: str, _picks: list[str], freeform: str
) -> str:
    """S2 T3 and the A-triggered follow-up: how two tables join, in free text."""
    answer = freeform or label
    if not answer:
        return ""
    subject = _qualified(rec)
    return f"{subject}: {answer}" if subject else answer


#: Scope kind -> composer. Exhaustive over what the two generators and
#: ``maybe_generate_join_followup`` mint; ``tests/curator/test_elicitation_answers.py`` pins that
#: it stays exhaustive, so a new question shape cannot reach the fallback unnoticed.
_COMPOSERS: dict[str, Callable[[ClarificationRecord, str, list[str], str], str]] = {
    "term": _compose_term,
    "rule": _compose_rule,
    "exclusion": _compose_exclusion,
    "sentinel": _compose_exclusion,
    "valuemap": _compose_valuemap,
    "describetable": _compose_table_description,
    "describecolumns": _compose_column_descriptions,
    "reliability": _compose_reliability,
    "duplicate": _compose_duplicate,
    "joinkeys": _compose_join_keys,
    "joinkey": _compose_join_path,
    "join": _compose_join_path,
}


def compose_elicitation_answer_text(
    rec: ClarificationRecord,
    *,
    choice_id: str | None = None,
    choice_ids: list[str] | None = None,
    freeform: str | None = None,
) -> str:
    """Build the self-contained sentence a category-tagged answer folds as.

    A bare picked label (``"sales.total_amount"``, ``"exclude"``) loses the context that made it
    meaningful the moment it is written into a corpus note — this reconstructs that context from
    the record. Written into ``ClarificationRecord.answer`` at answer time
    (``api/curation_routes.py::answer_clarification_route``); from then on
    ``curator/clarifications.py::resolve_answer_text`` returns it verbatim for a category-tagged
    record (its own "category is not None" bypass).

    **Every shape accepts every input, and none of them silently drops one.** A user may click,
    type, or do both — the checklist form submits picks and freeform in one payload — so each
    composer handles whichever arrived rather than only the modality its question was designed
    around. That is the whole defect class this function exists to close, and it has now been hit
    from three directions: freeform lost on a picker (``d17d6e0``), picks lost on a checklist
    (S1's column question, which composed ``""`` for 9 of ``beer_factory``'s cards), and both
    composed through the wrong frame (S1's table question).

    The fallback is for a category-tagged record from some future source with a scope this table
    does not know: freeform, else the picked label. It never returns the picked *id*, because an
    id is not language.
    """
    choices_by_id = {str(c["id"]): str(c["label"]) for c in (rec.choices or ())}
    label = choices_by_id.get(choice_id or "", "")
    # Checklist picks are carried as **ids**, not labels: for B the id *is* the stored value
    # (which is the point — a value must reach the corpus byte-exact), and for S1's column
    # checklist the id is the column name while the label carries a type annotation for the
    # reader ("bezeichnung (text)") that has no business in a corpus fact.
    picks = [str(cid) for cid in (choice_ids or [])]
    freeform = (freeform or "").strip()

    composer = _COMPOSERS.get(_scope_kind(rec.scope))
    if composer is None:
        return _sentence(freeform or label)
    return _sentence(composer(rec, label, picks, freeform))
