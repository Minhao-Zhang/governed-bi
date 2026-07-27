"""The single-schema picker: candidate summary content and reply parsing.

The two halves only work together. The sibling-aware prompt asks the model to
reason before answering, which makes replies *prose*; prose is exactly where a
first-match substring scan mis-resolves prefix siblings. Shipping the prompt
without the parser would therefore be a regression, so the parser is pinned here
independently of any model.
"""

from __future__ import annotations

import pytest

from governed_bi.corpus import Corpus
from governed_bi.corpus.schemas import Column, Governance, LogicalType, TableAsset
from governed_bi.retrieval.schema_router import (
    SchemaPick,
    _parse_schema_reply,
    _schema_pick_summary,
    pick_schema,
)

SIBLINGS = ["food_inspection", "food_inspection_2"]


def _col(name: str, *, excluded: bool = False) -> Column:
    extra = {"governance": Governance(excluded=True)} if excluded else {}
    return Column(
        physical_name=name,
        physical_type="TEXT",
        logical_type=LogicalType.string,
        nullable=True,
        is_unique=False,
        **extra,
    )


def _corpus() -> Corpus:
    return Corpus(
        assets=[
            TableAsset(
                id="tbl_food_inspection_betriebe",
                schema="food_inspection",
                physical_name="betriebe",
                description="Food establishments.",
                columns=[_col("betrieb_id"), _col("stadt"), _col("secret", excluded=True)],
            ),
            TableAsset(
                id="tbl_food_inspection_2_ji_gou",
                schema="food_inspection_2",
                physical_name="ji_gou",
                description="Food facilities.",
                columns=[_col("xu_ke_zheng_hao"), _col("cheng_shi")],
            ),
        ]
    )


# --------------------------------------------------------------------------- #
# Reply parsing
# --------------------------------------------------------------------------- #


def _schema(reply: str, candidates: list[str] = SIBLINGS) -> "str | None":
    """Which schema a reply resolves to, ignoring *how* it resolved.

    ``_parse_schema_reply`` also reports whether the pick came off the final line
    the prompt designates (``SchemaPick.fallback``); the tests below that only
    care about sibling disambiguation go through here, and the ones about the flag
    assert on the whole :class:`SchemaPick`.
    """
    got = _parse_schema_reply(reply, candidates)
    return None if got is None else got.schema


@pytest.mark.parametrize(
    "reply",
    [
        "food_inspection_2",
        "The answer is food_inspection_2.",
        "FOOD_INSPECTION_2",
        "  food_inspection_2  ",
        "Both look similar, but food_inspection lacks the licence table.\n"
        "food_inspection_2",
        "Reasoning: ...\nFinal answer: food_inspection_2",
    ],
)
def test_prefix_sibling_never_steals_its_suffix_twin(reply):
    """``food_inspection`` is a substring of ``food_inspection_2`` and sorts first,
    so a first-match scan resolved every one of these to the wrong schema."""
    assert _schema(reply) == "food_inspection_2"


@pytest.mark.parametrize(
    "reply", ["food_inspection", "The answer is food_inspection.", "FOOD_INSPECTION"]
)
def test_prefix_sibling_still_resolves_to_itself(reply):
    """Longest-match must not overshoot in the other direction."""
    assert _schema(reply) == "food_inspection"


def test_last_line_wins_over_earlier_mentions():
    """The prompt asks for the bare name on the final line; reasoning above it
    routinely names the candidate that was ruled out."""
    reply = "food_inspection_2 has no inspector table, so it is out.\nfood_inspection"
    assert _parse_schema_reply(reply, SIBLINGS) == SchemaPick("food_inspection", None)


def test_labelled_final_answer_beats_a_bare_reasoning_heading():
    """The prompt asks the model to walk candidate by candidate, and a bare
    candidate name is a natural heading for that. When the model then labels its
    real answer, the label wins — otherwise the heading did, silently, and the
    row scored as a genuine pick of the schema the model had just rejected."""
    reply = (
        "food_inspection\n"
        "- has establishments but no licence table\n"
        "food_inspection_2\n"
        "- has both\n"
        "Final answer: food_inspection_2"
    )
    assert _parse_schema_reply(reply, SIBLINGS) == SchemaPick("food_inspection_2", None)


@pytest.mark.parametrize(
    "final_line",
    [
        "Final answer: food_inspection_2",
        "Final: food_inspection_2",
        "Answer: food_inspection_2",
        "answer = food_inspection_2",
        "Chosen schema: food_inspection_2",
        "Pick - food_inspection_2",
        "**Final answer:** `food_inspection_2`",
    ],
)
def test_label_shapes_the_prompt_did_not_ask_for_still_resolve(final_line):
    """The prompt asks for a bare name, so any label means the model deviated —
    but a labelled answer is still an answer, and treating it as unparseable
    would substitute rank-1 for a decision the model actually made."""
    reply = f"food_inspection\n- ruled out\n{final_line}"
    assert _parse_schema_reply(reply, SIBLINGS) == SchemaPick("food_inspection_2", None)


def test_echoed_candidate_heading_is_not_read_as_a_label():
    """``_schema_pick_summary`` heads every candidate block ``schema: <name>``, so
    if ``schema:`` counted as an answer label, a reply that quotes the summaries
    back would resolve — cleanly and wrongly — to whichever candidate was listed
    last, which is the failure this parser exists to prevent."""
    reply = "Answer: food_inspection\nschema: food_inspection_2"
    assert _parse_schema_reply(reply, SIBLINGS) == SchemaPick("food_inspection", None)


def test_unresolvable_reply_returns_none():
    assert _parse_schema_reply("neither of these", SIBLINGS) is None
    assert _parse_schema_reply("", SIBLINGS) is None
    assert _parse_schema_reply("   \n  ", SIBLINGS) is None


def test_truncated_name_is_not_guessed():
    """Completing a partial name is a guess, and a cheap one: a stray ``w`` would
    resolve to ``world``. The logged rank-1 fallback is the honest answer."""
    assert _parse_schema_reply("food_inspec", SIBLINGS) is None
    assert _parse_schema_reply("food_inspec", ["food_inspection", "movies"]) is None
    assert _parse_schema_reply("w", ["world", "movies"]) is None


@pytest.mark.parametrize(
    "reply,candidates",
    [
        # Sibling-name shapes the lake actually contains. Each reply names two
        # candidates, so longest-match (or first-match) would answer confidently
        # and wrongly.
        ("Not ice_hockey_draft; the answer is hockey.", ["hockey", "ice_hockey_draft"]),
        ("regional_sales lacks the store dimension, so sales.", ["sales", "regional_sales"]),
        (
            "The correct schema is food_inspection (not food_inspection_2).",
            SIBLINGS,
        ),
    ],
)
def test_reply_naming_several_candidates_is_ambiguous(reply, candidates):
    assert _parse_schema_reply(reply, candidates) is None


@pytest.mark.parametrize(
    "reply,candidates",
    [
        # A bare substring test matches inside unrelated words. With short schema
        # names almost any prose "names" a candidate, turning a non-answer into a
        # confident pick instead of a logged fallback.
        ("I have no idea.", ["a", "b"]),
        ("Wholesales are not covered here.", ["sales", "movies"]),
        ("This is worldwide data.", ["world", "movies"]),
    ],
)
def test_a_name_inside_another_word_is_not_a_mention(reply, candidates):
    assert _parse_schema_reply(reply, candidates) is None


def test_bare_name_on_an_earlier_line_beats_prose_on_the_last_but_is_flagged():
    """The prompt makes the model reason about rejected candidates, so the last
    line is often a parenthetical: reading it as the answer would pick the
    rejected sibling, so the bare name above still wins (unchanged).

    What changed is honesty about it. A heading match looks exactly like this from
    inside the parser — the designated final line did not resolve — and the two
    cannot be separated here, so the pick is returned flagged. It is still used
    for routing; it just no longer counts toward
    ``schema_pick_accuracy_excl_fallback``, which is the number that is supposed
    to mean "the model decided, and was right"."""
    reply = "hockey\n(Note: ice_hockey_draft was considered and rejected.)"
    assert _parse_schema_reply(reply, ["hockey", "ice_hockey_draft"]) == SchemaPick(
        "hockey", "parsed_nonfinal_line"
    )


def test_the_v2_prompts_strict_final_line_resolves_cleanly():
    """``schema_pick@v2`` ends the reply with ``FINAL: <name>`` instead of a bare
    name. That is a *labelled* answer, so it resolves through the label pass with
    no fallback flag — the strict line makes the pick unambiguous rather than
    needing a new parse rule. Pinned because the variant's whole output contract
    rests on it: if this stopped resolving, every v2 row would silently score as
    the rank-1 fallback and the variant would look like it had no effect."""
    assert _parse_schema_reply("FINAL: food_inspection_2", SIBLINGS) == SchemaPick(
        "food_inspection_2", None
    )


def test_the_v2_per_candidate_reasoning_lines_do_not_steal_the_answer():
    """v2 asks for one line per candidate naming its tables/columns, so the reply
    mentions the rejected sibling by name several times above the answer."""
    reply = (
        "Parts needed: establishment, city, licence number.\n"
        "food_inspection: betriebe.stadt covers city, but no licence table.\n"
        "food_inspection_2: ji_gou.cheng_shi covers city and holds licences.\n"
        "\n"
        "FINAL: food_inspection_2"
    )
    assert _parse_schema_reply(reply, SIBLINGS) == SchemaPick("food_inspection_2", None)


def test_prose_mention_above_the_final_line_is_flagged_too():
    """Same reasoning one tier down: nothing on the final line resolved, so the
    mention we fell back to is not evidence the model answered."""
    reply = "I would use food_inspection_2 here.\nHope that helps!"
    assert _parse_schema_reply(reply, SIBLINGS) == SchemaPick(
        "food_inspection_2", "parsed_nonfinal_line"
    )


# --------------------------------------------------------------------------- #
# Candidate summary
# --------------------------------------------------------------------------- #


def test_summary_omits_columns_by_default():
    out = _schema_pick_summary(_corpus(), "food_inspection")
    assert "betriebe" in out
    assert "cols:" not in out


def test_summary_includes_column_vocabulary_when_asked():
    """Column names are what separate same-topic siblings whose table
    descriptions read alike."""
    out = _schema_pick_summary(_corpus(), "food_inspection", max_columns=12)
    assert "betrieb_id" in out and "stadt" in out


def test_summary_excludes_governance_excluded_columns():
    """D6: an excluded column must not reach the Analyst by any path, including
    the picker prompt."""
    out = _schema_pick_summary(_corpus(), "food_inspection", max_columns=12)
    assert "secret" not in out


def test_summary_caps_columns_per_table():
    out = _schema_pick_summary(_corpus(), "food_inspection", max_columns=1)
    assert "betrieb_id" in out
    assert "stadt" not in out
    assert "…" in out


# --------------------------------------------------------------------------- #
# pick_schema wiring
# --------------------------------------------------------------------------- #


class _Chat:
    def __init__(self, reply: "str | Exception"):
        self.reply = reply
        self.calls: list[tuple[str, str]] = []

    def complete(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def test_pick_schema_parses_prose_reply():
    chat = _Chat("Only food_inspection_2 has the licence table.\nfood_inspection_2")
    assert pick_schema(_corpus(), "q", SIBLINGS, chat=chat) == SchemaPick(
        "food_inspection_2", None
    )


def test_pick_schema_passes_columns_into_the_prompt():
    chat = _Chat("food_inspection")
    pick_schema(_corpus(), "q", SIBLINGS, chat=chat, max_columns=12)
    _system, user = chat.calls[0]
    assert "betrieb_id" in user
    assert "most relevant first" in user  # rank is a stated prior, not an accident


def test_pick_schema_flags_an_unparseable_reply_as_a_fallback(caplog):
    """The rank-1 substitute must be distinguishable from a real pick, or a proxy
    outage is scored as that share of rank-1 picks the model never made."""
    chat = _Chat("I cannot tell.")
    with caplog.at_level("WARNING", logger="governed_bi.retrieval"):
        got = pick_schema(_corpus(), "q", SIBLINGS, chat=chat)
    assert got == SchemaPick("food_inspection", "unparseable_reply")
    assert "matched no candidate" in caplog.text


def test_pick_schema_flags_a_failed_call_as_a_fallback(caplog):
    chat = _Chat(RuntimeError("proxy 504"))
    with caplog.at_level("WARNING", logger="governed_bi.retrieval"):
        got = pick_schema(_corpus(), "q", SIBLINGS, chat=chat)
    assert got == SchemaPick("food_inspection", "call_failed")
    assert "schema-pick LLM call failed" in caplog.text


def test_pick_schema_short_circuits_without_calling_the_model():
    chat = _Chat("unused")
    assert pick_schema(_corpus(), "q", [], chat=chat) == SchemaPick("", None)
    assert pick_schema(_corpus(), "q", ["only_one"], chat=chat) == SchemaPick(
        "only_one", None
    )
    assert chat.calls == []
