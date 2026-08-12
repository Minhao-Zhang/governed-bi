"""Nine declared record fields reached ``stamp`` and stopped there.

Same defect as ``reflect_verdict`` (see the test beside this one): ``stamp`` projects the field
into the turn record, ``eval/harness.project_turn`` builds the measurement row from a fixed key
list, and the field is not on it — so the arm that would have used the evidence produced an
artifact without it. The nine were ``schema_ranking``, ``facet_hits``, ``lexical_coverage``,
``guard``, ``crossings``, ``pulled_in``, ``delivery_hash``, ``tool_delivered`` and
``latency_sec``; the last means none of the 1351-row arms in ``runs/eval/`` records wall clock
at all.

**Every test asserts a value the test supplied**, never that the key is present: `None` is
present, and a row carrying a constant `None` satisfies `"latency_sec" in row` forever. Eight
tests in this repository were found asserting a constant against itself in one sweep.

Three of the nine are carried as summaries rather than copies, because the full values run
59 KB / 7.0 KB / 2.0 KB per turn against a 6.4 KB row. Those tests assert the summary was
computed from the supplied value — a rank located in a list, a count taken before truncation —
which no constant can satisfy either.
"""

from __future__ import annotations

from typing import Any

from governed_bi.eval.harness import project_turn
from governed_bi.register.quantity import Measured


def _record(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "outcome": "answered",
        "terminal_reason": None,
        "execution": {"attempts": []},
        "usage": [],
        "corpus_content_hash": "corpus-x",
        "prompt_set_hash": "prompt-x",
    }
    base.update(over)
    return base


def _row(record: dict[str, Any], *, state: dict[str, Any] | None = None) -> dict[str, Any]:
    """Project one turn whose record is under test."""
    turn: dict[str, Any] = {
        "answer": {"answer_text": "42", "outcome": "answered", "record": record},
        "licensed": ["s.t"],
        "schemas": ["s"],
    }
    turn.update(state or {})
    return project_turn(
        turn,
        question={"question_id": "q1", "db_id": "beer_factory", "question": "how many?"},
        arm="test",
    )


# ── schema_ranking ───────────────────────────────────────────────────────────


def _ranking(n: int = 25) -> list[list[Any]]:
    """25 scored schemas with the gold at rank 4, scores strictly descending."""
    names = [f"other_{i}" for i in range(n)]
    names[3] = "beer_factory"
    return [[name, round(10.0 - i * 0.1, 4)] for i, name in enumerate(names)]


def test_the_row_says_where_the_gold_schema_ranked():
    """Rank 4 of 25, not "absent from the shortlist" — the distinction the field exists for."""
    row = _row(_record(schema_ranking=_ranking()))
    assert row["schema_ranking"]["gold_rank"] == 4
    assert row["schema_ranking"]["n_scored"] == 25
    assert row["schema_ranking"]["gold_score"] == 9.7


def test_a_gold_schema_the_router_never_scored_ranks_nowhere_rather_than_last():
    """`None` and 25 scored, which is a different finding from ranking 25th of 25."""
    ranking = [[f"other_{i}", 1.0] for i in range(25)]
    row = _row(_record(schema_ranking=ranking))
    assert row["schema_ranking"]["gold_rank"] is None
    assert row["schema_ranking"]["gold_score"] is None
    assert row["schema_ranking"]["n_scored"] == 25


def test_the_ranking_head_keeps_its_scores_so_a_near_miss_is_visible():
    row = _row(_record(schema_ranking=_ranking()))
    top = row["schema_ranking"]["top"]
    assert len(top) == 10, "the head of the ranking is what says whether the gold lost by 0.01"
    assert top[0] == ["other_0", 10.0]
    assert top[3] == ["beer_factory", 9.7]


def test_a_turn_that_never_routed_carries_no_ranking_and_an_empty_one_carries_zero():
    """Absent and empty are different: `route` writes the full ranking even on a decline."""
    assert _row(_record())["schema_ranking"] is None
    assert _row(_record(schema_ranking=[]))["schema_ranking"] == {
        "n_scored": 0,
        "gold_rank": None,
        "gold_score": None,
        "top": [],
    }


# ── facet_hits ───────────────────────────────────────────────────────────────


def test_the_row_attributes_a_facets_hits_to_named_assets():
    """The register's reason for the field is attribution, so the asset ids must survive."""
    hits = [{"asset_id": f"beer_factory.t{i}", "score": 1.0 - i / 100} for i in range(50)]
    row = _row(_record(facet_hits={"facet_term": {"queries": ["beer"], "hits": hits}}))
    facet = row["facet_hits"]["facet_term"]
    assert facet["queries"] == ["beer"]
    assert facet["top"][0] == "beer_factory.t0"
    assert facet["top"][9] == "beer_factory.t9"


def test_the_hit_count_is_taken_before_the_summary_truncates():
    """`n_hits` says the fan-out returned fifty; `top` is the ten kept. A count of the kept
    list would report every facet as returning ten and hide a facet that returned two."""
    hits = [{"asset_id": f"a{i}"} for i in range(50)]
    row = _row(_record(facet_hits={"facet_term": {"queries": ["q"], "hits": hits}}))
    assert row["facet_hits"]["facet_term"]["n_hits"] == 50
    assert len(row["facet_hits"]["facet_term"]["top"]) == 10

    thin = _row(_record(facet_hits={"facet_term": {"queries": ["q"], "hits": hits[:2]}}))
    assert thin["facet_hits"]["facet_term"]["n_hits"] == 2


def test_a_turn_whose_fan_out_did_not_run_carries_no_facet_hits():
    assert _row(_record())["facet_hits"] is None


# ── pulled_in ────────────────────────────────────────────────────────────────


def test_the_row_separates_what_the_closure_pulled_in_from_what_the_join_walk_did():
    """The `connect` half is what `expand_hops` would be judged on, so it is kept in full."""
    pulled = {f"s.t.c{i}": "resolve" for i in range(140)}
    pulled.update({"s.join_a_b": "connect", "s.bridge": "connect"})
    row = _row(_record(pulled_in=pulled))
    assert row["pulled_in"]["n_resolve"] == 140
    assert row["pulled_in"]["n_connect"] == 2
    assert row["pulled_in"]["connect_ids"] == ["s.bridge", "s.join_a_b"]


def test_a_turn_that_never_reached_connect_carries_no_pulled_in():
    assert _row(_record())["pulled_in"] is None


# ── lexical_coverage ─────────────────────────────────────────────────────────


def test_a_measured_zero_coverage_is_not_the_same_as_no_measurement():
    """0.0 is the out-of-corpus signal itself. Collapsing it to `None` deletes the finding."""
    assert _row(_record(lexical_coverage=0.0))["lexical_coverage"] == 0.0
    assert _row(_record(lexical_coverage=0.375))["lexical_coverage"] == 0.375
    assert _row(_record())["lexical_coverage"] is None


# ── crossings ────────────────────────────────────────────────────────────────


def test_the_row_names_the_schemas_a_turn_crossed():
    crossings = [{"from": "beer_factory", "to": "retails"}]
    assert _row(_record(crossings=crossings))["crossings"] == crossings
    assert _row(_record(crossings=[]))["crossings"] == [], "a turn that crossed nothing is a zero"
    assert _row(_record())["crossings"] is None


# ── guard ────────────────────────────────────────────────────────────────────


def test_a_cleared_guard_leaves_a_trace_and_a_blocked_one_names_its_rule():
    """"A gate that leaves a trace only when it fires cannot afterwards be told from one never
    wired up" — the register's own words, and the reason `clear` is carried."""
    cleared = _row(_record(guard={"outcome": "clear", "rule_id": None, "detail": None}))
    assert cleared["guard"] == {"outcome": "clear", "rule_id": None}

    blocked = _row(
        _record(guard={"outcome": "blocked", "rule_id": "g_pii", "detail": "ssn in question"})
    )
    assert blocked["guard"]["outcome"] == "blocked"
    assert blocked["guard"]["rule_id"] == "g_pii"
    assert "detail" not in blocked["guard"], "free text; the register says it is dropped"


# ── delivery_hash and tool_delivered ─────────────────────────────────────────


def test_the_row_carries_the_digest_that_audits_what_reached_the_model():
    digest = "a" * 64
    row = _row(_record(delivery_hash=digest, context_hash="c" * 64))
    assert row["delivery_hash"] == digest
    assert row["context_hash"] == "c" * 64, "the two are different fields and one gates"


def test_an_empty_delivery_map_is_a_loop_that_delivered_nothing_not_a_loop_that_did_not_run():
    ran = _row(_record(tool_delivered={"call_1": "0123456789abcdef"}))
    assert ran["tool_delivered"] == {"call_1": "0123456789abcdef"}
    assert _row(_record(tool_delivered={}))["tool_delivered"] == {}
    assert _row(_record())["tool_delivered"] is None


# ── latency_sec ──────────────────────────────────────────────────────────────


def test_the_row_records_wall_clock():
    assert _row(_record(latency_sec=12.5))["latency_sec"] == 12.5


def test_an_unmeasured_latency_becomes_null_and_never_the_string_of_its_reason():
    """``json.dumps(..., default=str)`` in the drivers would serialise a `Measured` as its
    repr, and a string sorts and compares like a value. `eval/datalake._stage` says the same.
    """
    row = _row(_record(latency_sec=Measured.unmeasured("no wrapped node ran")))
    assert row["latency_sec"] is None

    measured = _row(_record(latency_sec=Measured.of(3.25)))
    assert measured["latency_sec"] == 3.25
