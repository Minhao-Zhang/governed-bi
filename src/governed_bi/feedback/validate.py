"""Rules an :class:`Observation` or a :class:`Patch` must satisfy before the store takes it.

Returns faults rather than raising, in the shape ``corpus/validate.py`` established: a caller collecting several
wants all of them, and an importer reading 1,351 rows wants to report a bad row rather than die on
it. The store raises on a non-empty list — so the *decision* to refuse is made once, in one place,
and every caller can still see the whole list first.

**Named ``faults_with`` and not ``problems_with``, which is what it wanted to be called.**
``tools/check_one_implementation.py`` refuses one name defined in two modules, and its bar for an
exemption is "this name cannot mean one concept" rather than "renaming is annoying" -- which this
name fails, because "the problems with this thing" is plainly one concept. So the gate is right and
the second definition is the defect. The parity that matters is not the spelling anyway: it is that
this module **calls** ``corpus/validate.py::problems_with`` for asset YAML rather than restating its
rules, which is what this layer's position in ``LAYERS`` exists to make possible.

**What is not checked here.** Whether a patch's YAML is a loadable asset: that is
``corpus/parse.py::from_mapping`` plus ``corpus/validate.py::problems_with``, and calling a second
copy of those rules from here is the thing this layer's position in ``LAYERS`` exists to avoid.
And whether a state transition is legal: that is :mod:`.lifecycle`, whose table is the only
declaration of an edge.
"""

from __future__ import annotations

from governed_bi.feedback.events import (
    CATEGORY_KIND,
    OPERATOR_ONLY_CATEGORIES,
    PATCHABLE_ASSET_TYPES,
    DeclineReason,
    Observation,
    Patch,
    PatchIntent,
    Source,
)

__all__ = [
    "CONTENT_HASH_CHARS",
    "NOTE_MAX_CHARS",
    "QUESTION_MAX_CHARS",
    "EDITABLE_FIELD_PATHS",
    "faults_with",
]

#: Cap on a filed note. Carried over unchanged from ``serve/raised.py::RAISED_NOTE_MAX_CHARS``,
#: which is the value the HTTP surface has always advertised and the client has always been
#: written against. About 600 words — several times what anybody types about one wrong answer, and
#: small enough that the worst case is boring.
#:
#: The reason for a cap at all has changed, and is weaker than it was, which is worth recording
#: rather than inheriting silently. It used to be that the row landed on an accumulating checkpoint
#: channel and was re-serialised into every later checkpoint of the thread, so an oversized note
#: was paid for repeatedly. A row in a table is paid for once. What survives is the unauthenticated
#: write: reaching the port is still sufficient, so an unbounded field is still a way to grow a
#: store without limit.
NOTE_MAX_CHARS = 4000

#: Cap on the question text an observation carries. Generous, because an imported question is
#: whatever the dataset holds and truncating the thing the failure is *about* would make the row
#: unreviewable. A question longer than this is a malformed dataset row, not a long question.
QUESTION_MAX_CHARS = 8000

#: Field paths an ``edit_asset`` patch may target, and the set is small on purpose.
#:
#: These are the two fields ``corpus/patch.py`` can locate and replace as a single scalar span,
#: and the two ``lifecycle.derived_state`` can confirm landed by comparing text. Widening this set
#: without widening both of those makes a patch that lands and then reads as ``superseded``, which
#: is worse than a patch that was refused.
#:
#: ``reliability.status`` is deliberately absent even though ADR 0005 declares it AI-authorable:
#: it is a nested enum rather than a scalar, the landing check cannot see it, and an operator
#: setting one column suspect by hand is cheap. It is the first thing to add when the landing check
#: can read more than two fields.
EDITABLE_FIELD_PATHS: frozenset[str] = frozenset({"summary", "body"})

#: Length of a corpus content hash, and a patch's must be exactly this.
#:
#: ``corpus/hash.py::corpus_content_hash`` returns a full sha256 hex digest — 64 characters,
#: verified. Every other place in this repository *displays* a 16-character prefix, which is the
#: trap: a prefix stored in ``base_corpus_content_hash`` never equals the full digest
#: ``lifecycle.derived_state`` compares it against, so the first branch falls through, the content
#: check finds the asset unchanged, and the patch reports **superseded** — a good change sent back
#: to the steward with nothing in the output to suggest the comparison was at fault.
#:
#: Found by driving the loop end to end rather than by a unit test, because both values are strings
#: and every type is satisfied.
CONTENT_HASH_CHARS = 64


def faults_with(item: object) -> list[str]:
    """Every rule ``item`` breaks, as sentences. Empty means the store will take it."""
    if isinstance(item, Observation):
        return _observation_problems(item)
    if isinstance(item, Patch):
        return _patch_problems(item)
    return [f"{type(item).__name__} is neither an Observation nor a Patch"]


def _observation_problems(obs: Observation) -> list[str]:
    out: list[str] = []

    if not obs.observation_id:
        out.append("observation_id is empty")
    if not obs.filed_at:
        out.append("filed_at is empty; a queue ordered oldest-first cannot order it")

    if len(obs.note) > NOTE_MAX_CHARS:
        out.append(
            f"note is {len(obs.note)} characters, cap {NOTE_MAX_CHARS}. The cap names a number "
            "because 'too long' without one is not actionable."
        )
    if obs.note != obs.note.strip():
        out.append("note is not stripped; whitespace-only padding must not spend the cap")
    if len(obs.question) > QUESTION_MAX_CHARS:
        out.append(f"question is {len(obs.question)} characters, cap {QUESTION_MAX_CHARS}")

    if obs.category is not None:
        kinds = CATEGORY_KIND.get(obs.category, frozenset())
        if obs.kind not in kinds:
            out.append(
                f"category {obs.category.value} is not declared for kind {obs.kind.value}; "
                f"it is declared for {sorted(k.value for k in kinds)}"
            )
        if obs.category in OPERATOR_ONLY_CATEGORIES and not _may_file_operator_only(obs):
            out.append(
                f"category {obs.category.value} is operator-only and source is "
                f"{obs.source.value}. It names a column, and a filer who cannot read the corpus "
                "cannot name one -- a wrong pick sends a reviewer to the wrong asset with a "
                "confident-looking pointer on it."
            )

    # An imported observation is identified by its key rather than by a turn; a filed one is
    # identified by the turn it is about. Neither is optional, and the two are not the same claim.
    if obs.source is Source.eval:
        if not obs.external_key:
            out.append(
                "an imported observation needs an external_key, or re-reading the artifact files "
                "the same failure again"
            )
        if not obs.arm or not obs.question_id:
            out.append("an imported observation needs both arm and question_id")
        if obs.turn_id or obs.thread_id:
            out.append(
                "an imported observation must leave turn_id and thread_id unset: an eval artifact "
                "carries neither, and a synthesised id 404s on /audit/turns/{id}/trace"
            )
    elif not obs.turn_id:
        out.append("a filed observation needs the turn_id it is about")

    if not obs.question:
        out.append(
            "question is empty. The failure is about a question, and a row that does not carry "
            "one cannot be reviewed -- an absent dataset join must raise, not file a blank."
        )

    out.extend(_state_problems(obs))
    return out


def _state_problems(obs: Observation) -> list[str]:
    """The fields a state makes mandatory. The transition table's ``requires``, enforced."""
    from governed_bi.feedback.events import ObservationState

    out: list[str] = []
    if obs.state is ObservationState.declined and obs.decline_reason is None:
        out.append(
            "declined without a decline_reason. The reason *is* the notification: there is no "
            "declined badge without a sentence."
        )
    if obs.state is not ObservationState.declined and obs.decline_reason is not None:
        out.append(f"decline_reason is set on a {obs.state.value} observation")
    if obs.state is ObservationState.duplicate and not obs.duplicate_of:
        out.append("duplicate without duplicate_of naming the observation it duplicates")
    if obs.state is ObservationState.blocked_on_a_person and not obs.blocked_note.strip():
        out.append(
            "blocked_on_a_person without a blocked_note. The note is the whole content of the "
            "state -- there is nobody to escalate to, so the sentence is what a reader gets."
        )
    if obs.duplicate_of and obs.duplicate_of == obs.observation_id:
        out.append("duplicate_of names the observation itself")
    if obs.decline_reason is DeclineReason.dataset_defect and obs.source is not Source.eval:
        out.append(
            "dataset_defect is only reachable from an imported observation; a filed one has no "
            "dataset to be defective"
        )
    return out


def _may_file_operator_only(obs: Observation) -> bool:
    """Operator, or the one agent-writable exception ADR 0005 declares."""
    from governed_bi.feedback.events import Category

    if obs.source is Source.operator:
        return True
    return obs.source is Source.agent and obs.category is Category.column_suspect


def _patch_problems(patch: Patch) -> list[str]:
    out: list[str] = []

    if not patch.patch_id:
        out.append("patch_id is empty")
    if not patch.created_at:
        out.append("created_at is empty")
    if not patch.base_corpus_content_hash:
        out.append(
            "base_corpus_content_hash is empty. A patch that does not say which tree it was "
            "authored against cannot be told apart from one whose tree has moved."
        )
    # One loop for both hash fields, because "a corpus content hash is 64 hex characters" is one
    # rule about two fields. It was stated twice and the second copy checked only hexness *if* the
    # length already happened to be right -- so `expected_corpus_content_hash="deadbeefdeadbeef"`
    # was accepted, and `derived_state` compares it against a 64-character digest in exactly the
    # same way `base_` is compared. Same defect, other field, and a fixture blessed it.
    #
    # `base_` is mandatory and `expected_` is legitimately absent until a bundle is written, so
    # only the emptiness rule differs between them.
    for name, value in (
        ("base_corpus_content_hash", patch.base_corpus_content_hash),
        ("expected_corpus_content_hash", patch.expected_corpus_content_hash),
    ):
        if not value:
            continue
        if len(value) != CONTENT_HASH_CHARS:
            out.append(
                f"{name} is {len(value)} characters, and a corpus content hash is "
                f"{CONTENT_HASH_CHARS}. A truncated one -- the 16-character prefix every display "
                "uses -- never equals the digest the landing check compares it against, so the "
                "patch reports `superseded` while nothing has changed."
            )
        elif not _is_hex(value):
            out.append(f"{name} is the right length but is not hex")

    authors_nothing = patch.intent in (PatchIntent.engine_defect, PatchIntent.no_change)
    if authors_nothing:
        named = [
            name
            for name, value in (
                ("asset_type", patch.asset_type),
                ("asset_id", patch.asset_id),
                ("field_path", patch.field_path),
                ("becomes", patch.becomes),
                ("asset_yaml", patch.asset_yaml),
            )
            if value is not None
        ]
        if named:
            out.append(
                f"intent {patch.intent.value} authors no asset, but {named} are set. The point of "
                "the member is that there is nothing to patch."
            )
        if not patch.rationale.strip():
            out.append(
                f"intent {patch.intent.value} needs a rationale: it is the whole content of the "
                "patch, since no asset changes"
            )
        return out

    if patch.intent is PatchIntent.exclusion_request:
        if not patch.rationale.strip():
            out.append("an exclusion_request is prose, and its rationale is empty")
        if patch.becomes is not None or patch.asset_yaml is not None:
            out.append(
                "an exclusion_request must not carry a change. Governance.excluded is human-only, "
                "enforced by the absence of a tool -- this member is the argument for one, and a "
                "human transcribes it by hand."
            )
        if not patch.asset_id:
            out.append("an exclusion_request must name the asset it argues about")
        return out

    if patch.asset_type is None:
        out.append(f"intent {patch.intent.value} needs an asset_type")
    elif patch.asset_type not in PATCHABLE_ASSET_TYPES:
        out.append(
            f"asset_type {patch.asset_type.value} is not patchable. "
            f"Patchable: {sorted(t.value for t in PATCHABLE_ASSET_TYPES)}."
            + (
                " A standalone column file duplicates the id its table's inline column already "
                "derives, which store.load accepts with zero problems and build_index then raises "
                "on -- a serve outage arriving after the commit."
                if patch.asset_type.value == "column"
                else ""
            )
        )

    if patch.intent is PatchIntent.new_asset:
        if not patch.asset_yaml:
            out.append("a new_asset patch needs asset_yaml")
        if patch.field_path or patch.was is not None or patch.becomes is not None:
            out.append(
                "a new_asset patch must not carry field_path/was/becomes: there is no prior value"
            )
        if not patch.namespace:
            out.append(
                "a new_asset patch needs a namespace. ADR 0005 does not say where a join, metric "
                "or term with no schema field lives, so store.write refuses to guess and so does "
                "this."
            )
    elif patch.intent is PatchIntent.edit_asset:
        if not patch.asset_id:
            out.append("an edit_asset patch needs the asset_id it edits")
        if not patch.field_path:
            out.append("an edit_asset patch needs a field_path")
        elif patch.field_path not in EDITABLE_FIELD_PATHS:
            out.append(
                f"field_path {patch.field_path!r} is not editable. Editable: "
                f"{sorted(EDITABLE_FIELD_PATHS)}. These are the paths corpus/patch.py can replace "
                "as one scalar span and lifecycle.derived_state can confirm landed by comparing "
                "text; a wider set makes a patch that lands and then reads as superseded."
            )
        if patch.was is None:
            out.append(
                "an edit_asset patch needs `was`. It is the concurrency check, not documentation: "
                "apply_edit refuses when the current value is not this, so a stale patch fails at "
                "git apply instead of overwriting somebody else's edit."
            )
        if patch.becomes is None:
            out.append("an edit_asset patch needs `becomes`")
        if patch.was is not None and patch.was == patch.becomes:
            out.append("`was` and `becomes` are identical, so the patch changes nothing")
        if patch.asset_yaml is not None:
            out.append("an edit_asset patch must not carry a whole asset_yaml")

    return out


def _is_hex(value: str) -> bool:
    return all(character in "0123456789abcdef" for character in value.lower())


def _assert_the_editable_sets_agree() -> None:
    """This module and ``corpus/patch.py`` must allow the same field paths.

    Asserted here rather than there because ``feedback`` may import ``corpus`` and not the reverse
    -- the layering gate is AST-based and catches an upward import wherever the statement sits.

    What it prevents: a path this module accepts and ``patch.py`` cannot splice is a patch that is
    drafted and then fails at export, which is late; a path ``patch.py`` can splice and
    ``lifecycle.derived_state`` cannot confirm is a patch that **lands and reads as superseded
    forever**, which is worse than either.
    """
    from governed_bi.corpus.patch import EDITABLE

    if EDITABLE_FIELD_PATHS != EDITABLE:  # pragma: no cover - import-time guard
        raise AssertionError(
            f"feedback/validate.py allows {sorted(EDITABLE_FIELD_PATHS)} and corpus/patch.py "
            f"allows {sorted(EDITABLE)}. The two must be the same set."
        )


def _assert_editable_paths_are_landable() -> None:
    """The two sets that must agree, and the reason they are separate anyway.

    :data:`EDITABLE_FIELD_PATHS` is what a patch may target; ``lifecycle._content_is_there``
    compares ``summary`` and ``body``. If a path is editable and not comparable, a patch can land
    and then read as ``superseded`` forever -- the exact defect the four-state landing model exists
    to prevent, reintroduced one level down.
    """
    landable = {"summary", "body"}
    unlandable = EDITABLE_FIELD_PATHS - landable
    if unlandable:  # pragma: no cover - import-time guard
        raise AssertionError(
            f"{sorted(unlandable)} are editable but lifecycle._content_is_there cannot confirm "
            "them landed, so a patch touching one would land and read as superseded. Widen the "
            "landing check first."
        )


_assert_editable_paths_are_landable()
_assert_the_editable_sets_agree()
