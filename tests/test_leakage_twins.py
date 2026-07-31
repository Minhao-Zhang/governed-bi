"""Structural train/test gold twins: the leakage the id-level check cannot see.

`_assert_train_test_disjoint` proves no question *id* appears in both splits. It says
nothing about a test question whose gold SQL is the same statement as some train
question's, and on the obfuscated BIRD test split 246 of 2030 questions (12.1%) are
exactly that — up to 46% in one schema. `seeded` derives its seed from train gold SQL
and `curated` runs an agent over train, so on those questions an EX gain is consistent
with recall as well as with generalisation, and EX alone cannot separate them.
"""

from __future__ import annotations

import pytest

from governed_bi.eval.leakage import canonical_sql
from governed_bi.eval.run_datalake import _summarise_rows


def test_canonical_form_ignores_literals_but_not_structure():
    """Two statements are twins when they differ only in the constants."""
    a = "SELECT name FROM t WHERE age > 30 AND city = 'Paris'"
    b = "select  name from t where age > 65 and city = 'Berlin' ;"
    assert canonical_sql(a) == canonical_sql(b)

    # A different column, table, clause or aggregate is a different question.
    for different in (
        "SELECT email FROM t WHERE age > 30 AND city = 'Paris'",
        "SELECT name FROM other WHERE age > 30 AND city = 'Paris'",
        "SELECT name FROM t WHERE age > 30",
        "SELECT count(name) FROM t WHERE age > 30 AND city = 'Paris'",
    ):
        assert canonical_sql(a) != canonical_sql(different), different


@pytest.mark.parametrize("blank", [None, "", "   "])
def test_canonical_form_of_nothing_is_empty(blank):
    """An empty form must never be added to the train set, or every gold-less test
    question would match every gold-less train one and read as leaked."""
    assert canonical_sql(blank) == ""


def _row(qid, *, correct, twin, frozen=False):
    return {
        "question_id": qid,
        "db_id": "db_a",
        "correct": correct,
        "correct_strict": correct,
        "generated_sql": "SELECT 1",
        "gold_twin_in_train": twin,
        "gold_frozen": frozen,
        "routed_hit": True,
        "error": None,
    }


def test_ex_is_reported_separately_for_the_stratum_the_curator_could_recall():
    """The whole point: `ex_no_twin` is the defensible headline.

    An arm can post a healthy pooled EX while every win sits on questions whose
    statement already existed in train. Without the split that reads as generalisation.
    """
    rows = [
        _row("t1", correct=True, twin=True),
        _row("t2", correct=True, twin=True),
        _row("n1", correct=False, twin=False),
        _row("n2", correct=False, twin=False),
    ]
    s = _summarise_rows("curated", rows)
    assert s["ex_lenient"] == pytest.approx(0.5)
    assert s["n_gold_twin_in_train"] == 2
    assert s["ex_twin"] == pytest.approx(1.0)
    assert s["ex_no_twin"] == pytest.approx(0.0), (
        "every win was on a question the curator could have recalled, and the pooled "
        "EX of 0.5 does not say so"
    )
    assert s["n_no_twin_gradeable"] == 2
    assert s["n_twin_gradeable"] == 2


def test_an_empty_stratum_reads_as_unmeasured_not_as_zero():
    """A run predating the flag stamps nothing, and that is not an EX of 0.0."""
    rows = [_row("q1", correct=True, twin=False), _row("q2", correct=False, twin=False)]
    s = _summarise_rows("curated", rows)
    assert s["ex_no_twin"] == pytest.approx(0.5)
    assert s["ex_twin"] is None, "no twin rows is not a twin EX of zero"
    assert s["n_twin_gradeable"] == 0


def test_the_headline_delta_is_tested_on_the_twin_free_stratum_too():
    """A rate is not enough — dropping 12% of the split widens the interval, so a
    delta that survives pooled can stop being resolvable on the subset."""
    from governed_bi.eval.run_datalake import _compare_arms

    lo = [_row(f"t{i}", correct=False, twin=True) for i in range(6)] + [
        _row(f"n{i}", correct=False, twin=False) for i in range(6)
    ]
    hi = [_row(f"t{i}", correct=True, twin=True) for i in range(6)] + [
        _row(f"n{i}", correct=False, twin=False) for i in range(6)
    ]
    comparisons, _div = _compare_arms({"baseline": lo, "curated": hi})
    pair = comparisons[0]
    assert pair["net_questions"] == 6, "pooled, the arm looks like a clean win"

    no_twin = pair["no_twin"]
    assert no_twin is not None
    assert no_twin["net_questions"] == 0, (
        "and on the questions it could not have recalled, it won nothing"
    )


# --------------------------------------------------------------------------- #
# The dataset's own EX exclusions
# --------------------------------------------------------------------------- #


def test_the_datasets_own_exclusion_list_is_read(tmp_path):
    """`order_sensitive_qids.json` ships with the data and nothing read it.

    Its own note says "Exclude both from cross-variant EX": gold with
    LIMIT-without-total-order or a float aggregate returns a different-but-VALID result,
    which hashes differently, so each such question was scored wrong for every arm. 25
    of the 2030 test questions qualify. Uniform across arms, so deltas were unaffected —
    but every absolute EX was depressed, including the one read against the `oracle_sql`
    ceiling, which is the comparison the whole ladder exists to support.
    """
    import json as _json

    from governed_bi.eval.leakage import ungradeable_question_ids

    (tmp_path / "order_sensitive_qids.json").write_text(
        _json.dumps(
            {
                "note": "prose, not a list",
                "order_sensitive": ["1024", 1117],
                "exec_failed": ["384"],
            }
        ),
        encoding="utf-8",
    )
    got = ungradeable_question_ids(tmp_path)
    assert got == {"order_sensitive": {"1024", "1117"}, "exec_failed": {"384"}}, got
    assert "note" not in got, "the prose key is not an id list"


def test_a_missing_exclusion_file_is_not_an_empty_exclusion_set(tmp_path):
    """Absent means "this data checkout says nothing", which the driver warns about —
    silently treating it as "nothing to exclude" is the same coercion this repo keeps
    finding."""
    from governed_bi.eval.leakage import ungradeable_question_ids

    assert ungradeable_question_ids(tmp_path) == {}
    (tmp_path / "order_sensitive_qids.json").write_text("{not json", encoding="utf-8")
    assert ungradeable_question_ids(tmp_path) == {}


def test_order_sensitive_gold_leaves_the_gradeable_denominator():
    """Same treatment frozen gold already gets, and counted where a reader can see it."""
    rows = [
        _row("ok", correct=True, twin=False),
        _row("frozen", correct=False, twin=False, frozen=True),
        {**_row("ordered", correct=False, twin=False), "gold_order_sensitive": True},
    ]
    s = _summarise_rows("curated", rows)
    assert s["n"] == 3
    assert s["n_frozen_gold"] == 1
    assert s["n_order_sensitive_gold"] == 1
    assert s["n_gradeable"] == 1
    assert s["ex_gradeable"] == pytest.approx(1.0), (
        "both ungradeable shapes must leave the denominator, or a question whose gold "
        "returns a different-but-valid result reads as a miss"
    )
    # ...and EX over every row still reports the un-excluded truth beside it.
    assert s["ex_lenient"] == pytest.approx(1 / 3)


# --------------------------------------------------------------------------- #
# The stratum must not silently become the pooled number
# --------------------------------------------------------------------------- #


def test_rows_predating_the_flag_do_not_land_in_the_twin_free_stratum():
    """The safeguard inverted into the exact error it exists to catch.

    Both filters used `not r.get("gold_twin_in_train")`, so a row from before the flag
    existed — key absent, `None`, falsy — counted as twin-free. On a resumed run
    `ex_no_twin` therefore became the pooled EX, under the field the runbook calls the
    defensible headline, with nothing saying so.
    """
    stamped = [
        _row("t1", correct=True, twin=True),
        _row("t2", correct=True, twin=True),
        _row("n1", correct=False, twin=False),
        _row("n2", correct=False, twin=False),
    ]
    s = _summarise_rows("curated", stamped)
    assert s["ex_no_twin"] == pytest.approx(0.0)
    assert s["n_twin_unstamped"] == 0

    # Same rows, replayed from a directory that predates the flag.
    unstamped = [{k: v for k, v in r.items() if k != "gold_twin_in_train"} for r in stamped]
    u = _summarise_rows("curated", unstamped)
    assert u["ex_lenient"] == pytest.approx(0.5)
    assert u["n_twin_unstamped"] == 4
    assert u["ex_no_twin"] is None, (
        "unstamped rows are not known to be twin-free; reporting the pooled EX under "
        "ex_no_twin is the misreading this whole flag exists to prevent"
    )
    assert u["ex_twin"] is None
    assert u["n_gold_twin_in_train"] == 0


def test_a_partial_resume_does_not_get_a_twin_free_comparison():
    """Mixed stamping is the partial-resume case: unstamped twins would count as
    twin-free and their wins would read as surviving the stratum."""
    from governed_bi.eval.run_datalake import _compare_arms

    lo = [_row(f"q{i}", correct=False, twin=False) for i in range(6)]
    hi = [_row(f"q{i}", correct=True, twin=False) for i in range(6)]
    unstamped_hi = [
        {k: v for k, v in r.items() if k != "gold_twin_in_train"} for r in hi
    ]
    comparisons, _ = _compare_arms({"baseline": lo, "curated": unstamped_hi})
    assert comparisons[0]["no_twin"] is None, (
        "one arm stamped nothing, so there is no twin-free stratum to compare on"
    )

    both, _ = _compare_arms({"baseline": lo, "curated": hi})
    assert both[0]["no_twin"] is not None
    # The stratum's p is raw and its floor came from the full split; both are labelled
    # so nobody compares it against the pooled Holm-adjusted p.
    assert both[0]["no_twin"]["p_value_is_raw"] is True
    assert both[0]["no_twin"]["floor_from_full_split"] is True
    assert "p_value_holm" not in both[0]["no_twin"]


# --------------------------------------------------------------------------- #
# Ungradeable gold cannot be a twin
# --------------------------------------------------------------------------- #


def test_frozen_gold_is_not_counted_as_a_twin():
    """Blank the constant and every `VALUES(...)` gold collapses onto ONE shape, so
    they all "twin" each other — a quarter of the unfiltered total. They are already
    outside `ex_gradeable`, so they can never reach `ex_no_twin`/`ex_twin`: counting
    them made the quoted rate and the stratified metric describe different populations,
    and put schemas in `worst_dbs` that carry no risk at all.
    """
    from governed_bi.eval.leakage import is_gradeable_gold

    frozen_a = "SELECT \"v\".\"c0\" FROM (VALUES (5.0)) AS \"v\"(\"c0\")"
    frozen_b = "SELECT \"v\".\"c0\" FROM (VALUES (27.17)) AS \"v\"(\"c0\")"
    assert canonical_sql(frozen_a) == canonical_sql(frozen_b), (
        "they DO collapse onto one shape — which is why they must be filtered out "
        "before twinning, not relied on to differ"
    )
    assert not is_gradeable_gold(frozen_a)
    assert is_gradeable_gold("SELECT a FROM t WHERE b = 1")


def test_is_gradeable_gold_excludes_frozen_and_empty():
    """Gradeability = non-empty and not frozen; detector is only ``is_frozen_constant``.

    After N6 there is one regex (in ``sql_diff``). This test pins the wrapper's
    empty/None edge and a few frozen vs live examples — not a second definition.
    """
    from governed_bi.eval.leakage import is_gradeable_gold
    from governed_bi.eval.sql_diff import is_frozen_constant

    frozen = 'SELECT "v"."c0" FROM (VALUES (5.0)) AS "v"("c0")'
    frozen_lower = "select x from (values ('a'),('b')) as v"
    live = "SELECT a FROM t"
    not_values_token = "SELECT count(*) FROM valuesomething"

    assert is_frozen_constant(frozen) and not is_gradeable_gold(frozen)
    assert is_frozen_constant(frozen_lower) and not is_gradeable_gold(frozen_lower)
    assert not is_frozen_constant(live) and is_gradeable_gold(live)
    assert not is_frozen_constant(not_values_token) and is_gradeable_gold(not_values_token)
    assert not is_frozen_constant(None) and not is_gradeable_gold(None)
    assert not is_frozen_constant("") and not is_gradeable_gold("")


def test_a_quoted_apostrophe_does_not_split_one_literal_into_two():
    """`'[^']*'` cannot span SQL's `''` escape, so `'O''Gallagher'` became two literals
    and stopped matching the same statement with an unescaped name. 203 golds across
    the two splits contain `''`, and the error understates the rate."""
    with_escape = "SELECT a FROM t WHERE n = 'O''Gallagher'"
    plain = "SELECT a FROM t WHERE n = 'Ramsey'"
    assert canonical_sql(with_escape) == canonical_sql(plain)
