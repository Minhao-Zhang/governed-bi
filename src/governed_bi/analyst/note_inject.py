"""Note selection + budget for Analyst prompt injection (ADR 0003 / M4).

Phase 1 only injected ``activation=always`` notes whose scope intersected
licensed *table* ids. This module implements the full resolver: five scope
kinds, always vs on_match, H1 precedence, and must_honour vs advisory split.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Iterable

from ..corpus.ids import derive_column_id
from ..corpus.schemas import (
    JoinAsset,
    MetricAsset,
    NormativeForce,
    NoteActivation,
    NoteAsset,
    ProvenanceStatus,
    TableAsset,
)

if TYPE_CHECKING:
    from ..corpus import Corpus
    from ..retrieval import RetrievalResult

# H1 defaults (config knobs mirror these).
DEFAULT_ALWAYS_NOTE_GLOBAL_MAX = 8
DEFAULT_ALWAYS_NOTE_CHAR_MAX = 2000

_PUB_RANK = {
    ProvenanceStatus.certified: 0,
    ProvenanceStatus.draft: 1,
    ProvenanceStatus.proposed: 2,
}
_FORCE_RANK = {
    NormativeForce.must_honour: 0,
    NormativeForce.advisory: 1,
}


@dataclass(frozen=True)
class LicensedScope:
    """Ids the current turn is allowed to "see" for note-scope matching."""

    table_ids: frozenset[str]
    column_ids: frozenset[str]
    metric_ids: frozenset[str]
    join_ids: frozenset[str]
    schemas: frozenset[str]
    db_name: str = "main"


@dataclass(frozen=True)
class InjectedNote:
    id: str
    kind: str
    summary: str
    body: str | None
    normative_force: str
    activation: str


def licensed_scope_from_tables(
    corpus: "Corpus",
    licensed_table_ids: frozenset[str] | set[str],
    *,
    db_name: str = "main",
) -> LicensedScope:
    """Derive column/metric/join/schema sets from the L4-licensed tables."""
    table_ids = frozenset(licensed_table_ids)
    column_ids: set[str] = set()
    schemas: set[str] = set()
    for tid in table_ids:
        table = corpus.by_id(tid)
        if isinstance(table, TableAsset):
            schemas.add(table.schema)
            for col in table.columns:
                column_ids.add(derive_column_id(table.id, col.physical_name))
    metric_ids = {
        a.id
        for a in corpus.assets
        if isinstance(a, MetricAsset) and a.base_table in table_ids
    }
    join_ids = {
        a.id
        for a in corpus.assets
        if isinstance(a, JoinAsset)
        and a.left_table in table_ids
        and a.right_table in table_ids
    }
    return LicensedScope(
        table_ids=table_ids,
        column_ids=frozenset(column_ids),
        metric_ids=frozenset(metric_ids),
        join_ids=frozenset(join_ids),
        schemas=frozenset(schemas),
        db_name=db_name,
    )


def _scope_specificity(scope: list[str]) -> int:
    """Lower is more specific (precedence tuple item 4)."""
    if not scope:
        return 3  # global
    best = 3
    for sid in scope:
        if sid.startswith("db:"):
            best = min(best, 2)
        elif sid.startswith("schema:"):
            best = min(best, 1)
        else:
            best = min(best, 0)  # asset id
    return best


def _pub_value(note: NoteAsset) -> ProvenanceStatus:
    v = note.publication_status
    return v if isinstance(v, ProvenanceStatus) else ProvenanceStatus(v)


def _force_value(note: NoteAsset) -> NormativeForce:
    v = note.normative_force
    if v is None:
        return NormativeForce.advisory
    return v if isinstance(v, NormativeForce) else NormativeForce(v)


def _act_value(note: NoteAsset) -> NoteActivation:
    v = note.activation
    if v is None:
        return NoteActivation.always
    return v if isinstance(v, NoteActivation) else NoteActivation(v)


def scope_matches(note: NoteAsset, licensed: LicensedScope) -> bool:
    """True if ``note.scope`` applies under the licensed turn scope."""
    if not note.scope:
        return True
    for sid in note.scope:
        if sid.startswith("schema:"):
            if sid.removeprefix("schema:") in licensed.schemas:
                return True
        elif sid.startswith("db:"):
            if sid.removeprefix("db:") == licensed.db_name:
                return True
        elif (
            sid in licensed.table_ids
            or sid in licensed.column_ids
            or sid in licensed.metric_ids
            or sid in licensed.join_ids
        ):
            return True
    return False


def _precedence_key(note: NoteAsset) -> tuple:
    """Order notes for the always-inject budget: normative force first.

    Publication status used to sort first, which meant a ``certified`` + ``advisory``
    note outranked a ``draft`` + ``must_honour`` one — so under a tight char budget
    the rules the engine promises to honour were the ones dropped, silently (AUDIT
    R8). Force leads now: a must-honour note is a constraint on the answer, while a
    certified advisory one is a suggestion, and dropping a constraint to keep a
    suggestion is the wrong trade at every budget. Status still breaks ties within a
    force level, so a certified must-honour note beats a draft must-honour note.
    """
    conf = note.confidence if isinstance(note.confidence, (int, float)) else 0.0
    return (
        _FORCE_RANK.get(_force_value(note), 9),
        _PUB_RANK.get(_pub_value(note), 9),
        -float(conf),
        _scope_specificity(note.scope),
        note.id,
    )


def apply_always_budget(
    notes: list[NoteAsset],
    *,
    global_max: int = DEFAULT_ALWAYS_NOTE_GLOBAL_MAX,
    char_max: int = DEFAULT_ALWAYS_NOTE_CHAR_MAX,
) -> list[NoteAsset]:
    """Keep notes under H1 caps using the 5-tuple precedence order.

    Global (``scope=[]``) always-notes are capped at ``global_max``. All
    injected always-note summaries together are capped at ``char_max``.
    Contradictory must_honour notes on the same scope are both kept when both
    fit; overflow drops lower-precedence notes.
    """
    ordered = sorted(notes, key=_precedence_key)
    kept: list[NoteAsset] = []
    n_global = 0
    chars = 0
    for note in ordered:
        is_global = not note.scope
        if is_global and n_global >= global_max:
            continue
        add = len(note.summary)
        if chars + add > char_max and kept:
            continue
        if chars + add > char_max and not kept:
            # Single note longer than the cap: still keep it (surface the content).
            pass
        kept.append(note)
        chars += add
        if is_global:
            n_global += 1
    return kept


def select_notes_for_injection(
    corpus: "Corpus",
    retrieval: "RetrievalResult",
    licensed: LicensedScope,
    *,
    global_max: int = DEFAULT_ALWAYS_NOTE_GLOBAL_MAX,
    char_max: int = DEFAULT_ALWAYS_NOTE_CHAR_MAX,
) -> list[InjectedNote]:
    """Choose notes that land in the prompt for this turn."""
    matched_ids = set(getattr(retrieval, "note_ids", ()) or ())
    matched_ids.update(getattr(retrieval, "triggered_note_ids", ()) or ())

    always_candidates: list[NoteAsset] = []
    on_match: list[NoteAsset] = []
    for asset in corpus.assets:
        if not isinstance(asset, NoteAsset):
            continue
        if getattr(asset.governance, "excluded", False):
            continue
        act = _act_value(asset)
        if act == NoteActivation.always:
            if scope_matches(asset, licensed):
                always_candidates.append(asset)
        elif act == NoteActivation.on_match:
            if asset.id in matched_ids and scope_matches(asset, licensed):
                on_match.append(asset)

    always_kept = apply_always_budget(
        always_candidates, global_max=global_max, char_max=char_max
    )
    # on_match notes are not subject to the global always budget, but share the
    # char budget remaining after always notes.
    chars = sum(len(n.summary) for n in always_kept)
    on_match_kept: list[NoteAsset] = []
    for note in sorted(on_match, key=_precedence_key):
        add = len(note.summary) + (len(note.body) if note.body else 0)
        if chars + add > char_max and on_match_kept:
            continue
        on_match_kept.append(note)
        chars += add

    out: list[InjectedNote] = []
    for note in [*always_kept, *on_match_kept]:
        act = _act_value(note)
        force = _force_value(note)
        kind = note.kind.value if hasattr(note.kind, "value") else str(note.kind)
        # body only for on_match (progressive disclosure); always uses summary.
        body = note.body if act == NoteActivation.on_match else None
        out.append(
            InjectedNote(
                id=note.id,
                kind=kind,
                summary=note.summary,
                body=body,
                normative_force=force.value,
                activation=act.value,
            )
        )
    return out


NOTE_TEXT_MAX_CHARS = 4000

# Line shapes that only make sense as an attempt to address the model rather than to
# describe the data. Matched at the START of a stripped line, so ordinary prose that
# happens to contain these words is untouched.
_INSTRUCTION_PREFIXES = (
    "ignore previous",
    "ignore all previous",
    "ignore the above",
    "disregard previous",
    "disregard the above",
    "system:",
    "assistant:",
    "user:",
    "new instructions",
    "you are now",
    "forget everything",
    "override:",
)

_REDACTED = "[redacted: instruction-shaped line in corpus content]"
_FENCE = chr(96) * 3


def sanitize_note_text(text: str, *, max_chars: int = NOTE_TEXT_MAX_CHARS) -> str:
    """Make one note safe to paste into an authoritative prompt section.

    The corpus is curator-authored, but it is also writable (``POST /corpus/edit``)
    and partly LLM-authored, and the system prompt tells the model this content is
    authoritative guidance to prefer over its own reasoning. Rendering it verbatim
    with no bound made it the primary poisoning vector (AUDIT S5).

    Three narrow measures, none of which touch a legitimate note's meaning:

    1. Drop lines that open like an instruction to the model rather than a statement
       about the data. A governance note describes a rule; nobody needs to begin a
       line with "ignore previous instructions".
    2. Neutralise fence and heading markers, so note text cannot close the block it
       sits in and open a section the renderer never intended.
    3. Truncate at ``max_chars``, visibly - otherwise one asset can push the
       question, the schema and the rules out of the context window.

    Defence in depth, not a claim to have solved prompt injection: a well-written
    note is still text the model reads and believes, which is inherent to
    corpus-as-context. This removes the cheap mechanical version and the
    unbounded-length one.
    """
    kept: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if line.lower().startswith(_INSTRUCTION_PREFIXES):
            kept.append(_REDACTED)
            continue
        if line.startswith(_FENCE):
            line = line.replace(_FENCE, "'''")
        if line.startswith("#"):
            line = line.lstrip("#").lstrip()
        kept.append(line)
    out = "\n".join(kept)
    if len(out) > max_chars:
        out = out[:max_chars] + "\n" + "[truncated at " + str(max_chars) + " chars]"
    return out


def format_note_lines(notes: Iterable[InjectedNote]) -> tuple[list[str], list[str]]:
    """Split into (must_honour_lines, advisory_lines) for rendering.

    Note text is sanitized here - the one place both lists are built - so no
    renderer can emit raw corpus prose into an authoritative section by accident.
    """
    must: list[str] = []
    advisory: list[str] = []
    for n in notes:
        line = "(" + n.kind + ") " + sanitize_note_text(n.summary)
        if n.body:
            line = line + "\n" + sanitize_note_text(n.body)
        if n.normative_force == NormativeForce.must_honour.value:
            must.append(line)
        else:
            advisory.append(line)
    return must, advisory
