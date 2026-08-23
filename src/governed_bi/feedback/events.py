"""The return path's vocabulary and its two event shapes (ADR 0015 §1).

Text, enums and frozen dataclasses. No I/O, no settings, nothing outside stdlib plus
:mod:`governed_bi.register.assets` for :class:`AssetType`. The store owns persistence and
:mod:`.lifecycle` owns the transitions; this module owns the words.

**Two layers, and the split is the design's load-bearing decision.** An :class:`Observation` is
what somebody or something *saw* — one failure, attributed to one turn, in the language of whoever
saw it. A :class:`Patch` is a typed corpus change an operator or an agent *authors*. One
observation has zero or more patches, and **zero is a common and honest outcome**: an observation
can be triaged to "the engine was right", to "the warehouse is wrong", or to "this is an engine
defect, not a corpus gap", none of which is a corpus edit.

Collapsed into one row they would be worse in three ways at once — ``asset_id`` / ``field_path`` /
``was`` / ``becomes`` null on every filed row, one observation unable to carry two changes (a
missing synonym *and* a wrong join is one failure), and one pair of author/timestamp columns
answering two questions.

**What this module deliberately does not have.** No ``Attribution`` type: the fields a turn is
identified by are flat on :class:`Observation`, because they are **copied and not joined**. The
turn's own record is the natural foreign key and it is the wrong one — ``MAX_TURNS_RETAINED``
elides older records off ``ServeState.turns`` and the thread index is a pickle whose loader deletes
the file on a bare ``Exception`` (``serve/checkpointer.py``). A join into a store that removes rows
returns nothing six months later, which is when a reviewer wants to read the queue.

No ``confidence`` and no score of any kind on either shape. ``corpus/validate.py`` already warns in
prose that ``confidence`` "is a curation-time belief and never an outcome score -- the first thing a
feedback loop will want is to write a hit rate here". This is that feedback loop, and it does not.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping

from governed_bi.register.assets import AssetType

__all__ = [
    "Source",
    "Kind",
    "Category",
    "ObservationState",
    "DeclineReason",
    "PatchState",
    "PatchIntent",
    "DerivedState",
    "TERMINAL_OBSERVATION_STATES",
    "OPERATOR_ONLY_CATEGORIES",
    "CATEGORY_KIND",
    "Observation",
    "Patch",
    "PATCHABLE_ASSET_TYPES",
]


# ── who or what filed it ──────────────────────────────────────────────────────


class Source(str, Enum):
    """The population an observation came from. A separate axis from :class:`Category`.

    The same category arrives from more than one population and the queue treats them
    differently, so folding this into the category would give a dozen values for nine questions.
    It also gates behaviour: :data:`OPERATOR_ONLY_CATEGORIES` is refused from any other source.
    """

    #: A person who asked a question and read the answer.
    reader = "reader"
    #: A person operating the engine, who can read the corpus and name an asset.
    operator = "operator"
    #: A model-driven process. Present so a future pipeline has a name; nothing writes it yet.
    agent = "agent"
    #: An evaluation artifact, imported from ``runs/eval/*.jsonl``. **The only producer in the
    #: first cut.** Distinct from ``agent`` because nothing judged anything: a grader compared a
    #: fingerprint, so the evidence is a gold statement rather than an opinion. It also makes the
    #: fields that mean "a human clicked" — ``filed_at``, and any per-turn rate limit — stop
    #: pretending to.
    eval = "eval"


class Kind(str, Enum):
    """Whether the turn delivered something or did not. **These two strings are the wire.**

    Sent by ``ui/components/answer/raise-note.tsx``, validated at the HTTP edge, and read by the
    pending queue's narrowing. Renaming or widening them breaks four call sites for no gain, so
    the refinement lives in :class:`Category` instead.
    """

    #: The turn refused, ran no statement, or ended in an abandoned clarification. Also a capped
    #: turn: five attempts spent with nothing passing is not a delivered answer.
    from_refusal = "from_refusal"
    #: The turn delivered an answer and the answer is wrong.
    wrong_answer = "wrong_answer"


class Category(str, Enum):
    """What was wrong, in the words of whoever or whatever saw it. Never names an asset.

    A reader picks one of the first nine in one tap; an importer derives one from a failure
    bucket. The last three are operator-only (:data:`OPERATOR_ONLY_CATEGORIES`) because they name
    a column, which is a thing a reader is never asked for: a wrong pick sends a reviewer to the
    wrong asset with a confident-looking pointer on it.
    """

    # ── on a delivered answer ──
    #: The number is wrong.
    wrong_value = "wrong_value"
    #: Wrong table, wrong filter, wrong dates — the wrong data was used.
    wrong_scope = "wrong_scope"
    #: Records were counted or combined wrongly. Fan-out and inner-vs-left in business language.
    wrong_rows = "wrong_rows"
    #: A different question was answered. Usually not a corpus gap at all.
    misread_question = "misread_question"
    #: A word in the question means something else in this warehouse.
    term_mismatch = "term_mismatch"
    #: The reader cannot tell whether it is right. Unknown until triage.
    unverifiable = "unverifiable"

    # ── on a refusal, a statement-less turn, or a clarification ──
    #: The data exists and the engine should have been able to answer.
    false_refusal = "false_refusal"
    #: The question the engine asked back did not make sense.
    bad_clarification = "bad_clarification"
    #: Correct to decline, but it should have said why.
    unclear_refusal = "unclear_refusal"
    #: The attempt budget was spent with nothing passing. **Its own member and not
    #: ``unverifiable``**: "I cannot tell" is a statement about the reader, and this is a
    #: statement about the engine, which is a different thing to triage.
    attempt_capped = "attempt_capped"

    # ── operator-only ──
    #: A column's values argue against trusting it. ``Reliability.status`` is AI-authorable, so
    #: an agent may file this one too.
    column_suspect = "column_suspect"
    #: A column should be hidden from everything the analyst sees. Filing it is *not* setting it:
    #: ``Governance.excluded`` is human-only, "enforced by the absence of a tool".
    column_excluded = "column_excluded"
    #: A fact worth keeping — typically an operator's answer to a clarification nobody else can
    #: answer again.
    reusable_fact = "reusable_fact"


#: Categories the store refuses from a source that is not an operator, with one exception noted
#: on :attr:`Category.column_suspect`.
OPERATOR_ONLY_CATEGORIES: frozenset[Category] = frozenset(
    {Category.column_suspect, Category.column_excluded, Category.reusable_fact}
)

#: Which :class:`Kind` each category may appear under. A category that could appear under either
#: maps to both. Enumerated rather than inferred, so a new member forces the decision here instead
#: of being silently accepted on a card it makes no sense on.
CATEGORY_KIND: Mapping[Category, frozenset[Kind]] = {
    Category.wrong_value: frozenset({Kind.wrong_answer}),
    Category.wrong_scope: frozenset({Kind.wrong_answer}),
    Category.wrong_rows: frozenset({Kind.wrong_answer}),
    Category.misread_question: frozenset({Kind.wrong_answer}),
    Category.term_mismatch: frozenset({Kind.wrong_answer}),
    Category.unverifiable: frozenset({Kind.wrong_answer}),
    Category.false_refusal: frozenset({Kind.from_refusal}),
    Category.bad_clarification: frozenset({Kind.from_refusal}),
    Category.unclear_refusal: frozenset({Kind.from_refusal}),
    Category.attempt_capped: frozenset({Kind.from_refusal}),
    # Operator-filed categories are about an asset rather than about one turn's delivery, so they
    # are legal on either card.
    Category.column_suspect: frozenset({Kind.wrong_answer, Kind.from_refusal}),
    Category.column_excluded: frozenset({Kind.wrong_answer, Kind.from_refusal}),
    Category.reusable_fact: frozenset({Kind.wrong_answer, Kind.from_refusal}),
}


# ── the lifecycle's stored half ───────────────────────────────────────────────


class ObservationState(str, Enum):
    """**Stored** states only — one per state a named actor moves (ADR 0015 §3).

    There is no ``closed``: nothing would branch on it, and ``open`` is computed as "not
    terminal" rather than stored, so the unclosable ``open: true`` row this design replaces
    stops being expressible. The landing states are :class:`DerivedState` and are recomputed on
    every read.
    """

    #: Filed, nobody has looked. The queue's whole value is telling this apart from ``triaged``.
    open = "open"
    #: Somebody has looked and is still deciding.
    triaged = "triaged"
    #: Reviewed and closed with a reason. The reason **is** the notification.
    declined = "declined"
    #: The same failure as another observation, and it joins that one's patch set.
    duplicate = "duplicate"
    #: At least one patch exists for it. **Not ``resolved``** — see :class:`DerivedState`.
    addressed = "addressed"
    #: Waiting on a person, with a note saying which question. Not a routing action: there is
    #: nobody to escalate to, so this is a state with a name rather than an assignee.
    blocked_on_a_person = "blocked_on_a_person"


#: States from which nothing moves on. ``is_open`` is the complement, computed and never stored.
TERMINAL_OBSERVATION_STATES: frozenset[ObservationState] = frozenset(
    {ObservationState.declined, ObservationState.duplicate}
)


class DeclineReason(str, Enum):
    """Why an observation was closed without a corpus change.

    Closed and mandatory on a decline, because a badge without a sentence is what teaches an
    operator to ignore a queue. The user-facing sentence for each lives in one place on the
    client; this enum is the key it is looked up by.
    """

    #: The engine was right. The answer matches what is in the data.
    working_as_intended = "working_as_intended"
    #: The data itself is wrong or missing, and the semantic layer cannot fix that.
    not_a_corpus_problem = "not_a_corpus_problem"
    #: Answering needs a table or column the warehouse does not have.
    needs_a_schema_change = "needs_a_schema_change"
    #: A defect in the engine, not in the semantic layer.
    engine_defect = "engine_defect"
    #: Not a question this engine is meant to answer.
    out_of_scope = "out_of_scope"
    #: Asked again against the corpus running now, it answered correctly.
    cannot_reproduce = "cannot_reproduce"
    #: Not enough here to act on, and nobody could be asked for more.
    insufficient_detail = "insufficient_detail"
    #: A real problem that is not worth what fixing it properly would cost. Says so without
    #: hedging: a ``deferred`` state that never moves is the same lie as an unclosable row.
    wont_fix_cost = "wont_fix_cost"
    #: The dataset's own defect rather than the engine's or the corpus's — a frozen-literal gold,
    #: an unparseable reference statement. Only reachable from an imported observation, which is
    #: why the eight above did not cover it.
    dataset_defect = "dataset_defect"


class PatchState(str, Enum):
    """A patch's stored state. Landing is :class:`DerivedState` and is not stored."""

    #: Authored, not yet handed to anybody.
    draft = "draft"
    #: A bundle has been produced. From the store's point of view this is terminal — what
    #: happens next happens in a git repository this process cannot write to.
    exported = "exported"
    #: Abandoned, with a reason.
    withdrawn = "withdrawn"


class PatchIntent(str, Enum):
    """What a patch is for. Two of the members author no asset at all, deliberately."""

    #: A corpus asset that does not exist yet.
    new_asset = "new_asset"
    #: One field of an existing asset, replaced in place.
    edit_asset = "edit_asset"
    #: An argument that a column should be ``governance.excluded``. **Prose, not a change** — a
    #: human transcribes it by hand, because exclusion is enforced by the absence of a tool.
    exclusion_request = "exclusion_request"
    #: The failure is the engine's, and the record says so instead of patching a corpus that is
    #: not at fault. A loop that cannot conclude this will patch anyway.
    engine_defect = "engine_defect"
    #: Looked at, nothing to change. Kept as a patch rather than only as a decline so the
    #: *reasoning* has somewhere to live.
    no_change = "no_change"


class DerivedState(str, Enum):
    """**Never stored.** Recomputed on every read from the loaded corpus (ADR 0015 §3).

    A stored copy would be a second answer to "did this land", able to disagree with the first.
    The four-way split exists because a two-state model silently mislabels the common case:
    two bundles landing in one week make exact-hash matching fail for a change that did ship.
    """

    #: The loaded corpus still hashes to the patch's base. Nobody has committed it.
    handed_off = "handed_off"
    #: The loaded corpus hashes to the patch's expected post-hash.
    landed_verified = "landed_verified"
    #: The hash differs, but every asset the bundle touched is present with the text the bundle
    #: expected. The common real case, and the one a two-state model gets wrong.
    landed_matched = "landed_matched"
    #: Landed, **and** the observation's retrieval fixture passes again. The narrowest claim the
    #: free ladder licenses: the tables needed to answer are reachable. **Not** "the answer is
    #: right" — nothing in this design licenses that.
    retrieval_verified = "retrieval_verified"
    #: The corpus moved and the change is not in it — a conflict, a CI reformat, or an edit
    #: before the commit. All three are normal, and all three read as "handed off, forever" to a
    #: model that does not have this state.
    superseded = "superseded"


# ── the two shapes ────────────────────────────────────────────────────────────

#: Asset types a patch may author. ``column`` is absent and its absence is a control: columns are
#: authored inline under their table and their ids are derived, so a standalone column file gives
#: the loader one asset id twice — which ``store.load`` accepts with zero problems and
#: ``build_index`` then dies on. ``negative_example`` is absent because the shipped corpus contains
#: none, so a patch authoring one would be the only instance of a rail nothing has ever exercised.
PATCHABLE_ASSET_TYPES: frozenset[AssetType] = frozenset(
    {
        AssetType.schema,
        AssetType.table,
        AssetType.join,
        AssetType.metric,
        AssetType.term,
        AssetType.few_shot,
    }
)


@dataclass(frozen=True, slots=True)
class Observation:
    """One failure, attributed to one turn. Constructed by keyword; validated in :mod:`.validate`.

    ``turn_id`` and ``thread_id`` are **nullable, and an imported observation leaves them unset**.
    An eval artifact carries no turn or thread id — measured on all 1,351 rows of the v4 arm — and
    synthesising one would put a value in a field whose only consumer, ``/audit/turns/{id}/trace``,
    would 404 on it. An absent id reads as "there is no trace to fetch", which is true.
    """

    observation_id: str
    filed_at: str
    source: Source
    kind: Kind
    state: ObservationState

    #: The reader's refinement, or the importer's derivation. ``None`` is legal: the first tap
    #: may be all there is, and a category nobody chose is better than one somebody guessed.
    category: Category | None = None

    #: Free text, whatever the filer typed. Bounded by :mod:`.validate`, empty on an import.
    note: str = ""

    # ── the turn this is about ──
    turn_id: str | None = None
    thread_id: str | None = None
    question: str = ""
    outcome: str | None = None
    refused_by: str | None = None
    generated_sql: str | None = None
    licensed: tuple[str, ...] = ()
    schemas: tuple[str, ...] = ()

    # ── the grader's half, present only on an imported observation ──
    #: The reference statement. The falsifiable claim on an imported row, and stronger than any
    #: sentence a reader could type: it is compared by fingerprint, not read.
    gold_sql: str | None = None
    gold_fingerprint: str | None = None
    pred_fingerprint: str | None = None
    quality_flags: tuple[str, ...] = ()

    # ── treatment identity, copied so the row is self-describing ──
    corpus_content_hash: str | None = None
    prompt_set_hash: str | None = None
    git_sha: str | None = None
    arm: str | None = None
    question_id: str | None = None
    db_id: str | None = None

    #: Idempotency key. Present on an imported observation and ``None`` on a filed one, because a
    #: person filing the same complaint twice is two complaints and an importer reading the same
    #: artifact twice is one row. See ``eval/feedback_import.py`` for what it digests.
    external_key: str | None = None

    # ── set when the state moves ──
    decline_reason: DeclineReason | None = None
    duplicate_of: str | None = None
    blocked_note: str = ""
    triaged_at: str | None = None


@dataclass(frozen=True, slots=True)
class Patch:
    """A candidate corpus change, or a recorded decision not to make one.

    ``was`` is read from the live corpus when the patch is drafted and is **the concurrency
    check**, not documentation: ``corpus/patch.py::apply_edit`` refuses when the current value is
    not this, so a patch authored against a tree that has since moved fails loudly instead of
    overwriting somebody else's edit.
    """

    patch_id: str
    created_at: str
    author: Source
    intent: PatchIntent
    state: PatchState
    namespace: str

    rationale: str = ""

    # ── what changes. All absent on `engine_defect` and `no_change`. ──
    asset_type: AssetType | None = None
    #: ``None`` on a ``new_asset`` until the id is derived — identity is derived, never taken from
    #: whoever asked (ADR 0008 §1.2).
    asset_id: str | None = None
    #: Dotted path into the asset, e.g. ``summary``, ``reliability.status``.
    field_path: str | None = None
    was: str | None = None
    becomes: str | None = None
    #: A whole document, ``new_asset`` only.
    asset_yaml: str | None = None

    # ── what it was verified against ──
    base_corpus_content_hash: str = ""
    #: ``None`` until a bundle is built, because it is the hash of a tree nobody has written yet.
    expected_corpus_content_hash: str | None = None
    #: Tier name to that tier's result. Written by the ladder, read by the review surface.
    ladder: Mapping[str, object] = field(default_factory=dict)

    withdrawn_reason: str = ""


# ── import-time closure ───────────────────────────────────────────────────────


def _assert_the_vocabularies_are_closed() -> None:
    """Every category maps to a kind, and every operator-only member is a real category.

    Both directions, for the reason ``register/stages.py`` gives about its own tables: a member
    added to one and forgotten in the other is a value that parses, reaches a queue, and is
    sorted into a bucket nobody meant.
    """
    missing = set(Category) - set(CATEGORY_KIND)
    if missing:  # pragma: no cover - import-time guard
        raise AssertionError(
            f"CATEGORY_KIND is missing rows for: {sorted(c.value for c in missing)}. "
            "A category with no declared kind is legal on every card, which is not a decision."
        )
    invented = set(CATEGORY_KIND) - set(Category)
    if invented:  # pragma: no cover - import-time guard
        raise AssertionError(
            f"CATEGORY_KIND declares {sorted(str(c) for c in invented)}, which are not Category "
            "members."
        )
    empty = sorted(c.value for c, kinds in CATEGORY_KIND.items() if not kinds)
    if empty:  # pragma: no cover - import-time guard
        raise AssertionError(
            f"CATEGORY_KIND maps {empty} to no kind at all, so nothing can ever file them."
        )
    stray = OPERATOR_ONLY_CATEGORIES - set(Category)
    if stray:  # pragma: no cover - import-time guard
        raise AssertionError(f"OPERATOR_ONLY_CATEGORIES names non-members: {sorted(map(str, stray))}")
    if not TERMINAL_OBSERVATION_STATES < set(ObservationState):  # pragma: no cover
        raise AssertionError(
            "TERMINAL_OBSERVATION_STATES must be a strict subset of ObservationState -- if every "
            "state were terminal nothing could move."
        )
    if AssetType.column in PATCHABLE_ASSET_TYPES:  # pragma: no cover - import-time guard
        raise AssertionError(
            "column is in PATCHABLE_ASSET_TYPES. A standalone column file duplicates the asset id "
            "its table's inline column already derives, which store.load accepts with zero "
            "problems and build_index then raises on -- a serve outage arriving after the commit."
        )


_assert_the_vocabularies_are_closed()
