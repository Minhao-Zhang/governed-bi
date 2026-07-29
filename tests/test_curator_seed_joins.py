"""Join edges seeded from train SQL must survive `validate_corpus`.

Both defects here were found the same way: wiring `corpus_validation`'s finding count
into the quotability gate, then running an offline smoke over four real BIRD
databases. Every non-baseline arm came back with five `join-on-unparseable` /
`join-on-unresolved` findings — meaning five join edges the planner never received,
on every build, silently, since the seeder was written.

A rejected join asset is not a cosmetic problem. `graph.build_graph` builds the join
graph from these edges, `detect_missing_join_path` refuses a question whose tables
have no path, and `plan_joins` writes the ON clauses. A malformed edge is a question
the arm cannot answer for a reason unrelated to the intervention being measured — and
it hits `seeded`, `curated` and `curated_sme` while leaving `baseline` untouched,
which biases the exact deltas the ladder exists to produce.
"""

from __future__ import annotations

from governed_bi.corpus.validate import validate_corpus
from governed_bi.curator.seed import extract_joins_from_sql, extract_metrics_from_sql

# --------------------------------------------------------------------------- #
# join-on-unparseable: physical names containing a space
# --------------------------------------------------------------------------- #


def test_a_table_name_with_a_space_produces_parseable_on_sql():
    """BIRD ships `Air Carriers`. Interpolated raw, the ON clause does not parse."""
    joins = extract_joins_from_sql(
        'SELECT 1 FROM "Air Carriers" AS T1 '
        'JOIN "Airlines" AS T2 ON T1.Code = T2.OP_CARRIER_AIRLINE_ID'
    )
    assert len(joins) == 1
    on = joins[0].on
    assert '"Air Carriers"' in on, f"unquoted identifier with a space: {on!r}"

    import sqlglot

    sqlglot.parse_one(on, read="postgres")  # raises if the fix regressed


def test_a_column_name_with_a_space_is_quoted_too():
    joins = extract_joins_from_sql(
        'SELECT 1 FROM a AS T1 JOIN b AS T2 ON T1."order id" = T2.oid'
    )
    assert len(joins) == 1
    import sqlglot

    sqlglot.parse_one(joins[0].on, read="postgres")


def test_ordinary_identifiers_are_left_unquoted():
    """Quoting everything would churn every existing edge for no benefit."""
    joins = extract_joins_from_sql(
        "SELECT 1 FROM zip_congress AS T1 JOIN congress AS T2 "
        "ON T1.district = T2.cognress_rep_id"
    )
    assert joins[0].on == "zip_congress.district = congress.cognress_rep_id"


# --------------------------------------------------------------------------- #
# The same defect in the metric extractor, which had no test of its own
# --------------------------------------------------------------------------- #


def test_a_metric_expression_over_a_spaced_table_name_parses():
    """The join half of `seed.py` was fixed for `Air Carriers`; the metric half was not.

    `extract_metrics_from_sql` rewrote alias-qualified columns by assigning the
    physical name as a raw `str`, which sqlglot renders unquoted —
    `COUNT(Air Carriers."Code")`. Nothing re-parsed the expression, so it reached the
    metric block of every seeded / curated / SME prompt as SQL the model could copy
    and the syntax layer would then refuse.
    """
    metrics = extract_metrics_from_sql(
        'SELECT COUNT(T1."Code") FROM "Air Carriers" AS T1 '
        'JOIN "Airlines" AS T2 ON T1."Code" = T2.OP_CARRIER_AIRLINE_ID'
    )
    assert len(metrics) == 1
    expr = metrics[0].expression
    assert '"Air Carriers"' in expr, f"unquoted identifier with a space: {expr!r}"

    import sqlglot

    sqlglot.parse_one(expr, read="postgres")  # raises if the fix regressed


def test_a_metric_expression_over_an_ordinary_table_name_is_left_unquoted():
    """Same restraint as the ON clause: quote what must be quoted, nothing else."""
    metrics = extract_metrics_from_sql(
        "SELECT SUM(T1.amount) FROM payments AS T1"
    )
    assert metrics[0].expression == "SUM(payments.amount)"


# --------------------------------------------------------------------------- #
# join-on-unresolved: endpoints must match the ON clause
# --------------------------------------------------------------------------- #


def test_an_on_clause_written_opposite_to_the_join_keeps_both_endpoints():
    """The overwrite bug.

    `... FROM zip_congress JOIN congress ON congress.x = zip_congress.district`
    resolves to (left=congress, right=zip_congress) from the ON. The seeder then
    replaced `right` with the JOIN's own table — `congress` — producing
    `left=congress, right=congress` with an ON naming a table that is neither.
    """
    joins = extract_joins_from_sql(
        "SELECT 1 FROM zip_congress AS T1 JOIN congress AS T2 "
        "ON T2.cognress_rep_id = T1.district"
    )
    assert len(joins) == 1
    j = joins[0]
    assert j.left_table != j.right_table, "both endpoints collapsed onto one table"
    assert {j.left_table, j.right_table} == {"zip_congress", "congress"}


def test_the_joined_table_still_orders_the_pair():
    """The joined table is used to orient the edge, never to replace an endpoint."""
    same_order = extract_joins_from_sql(
        "SELECT 1 FROM a AS T1 JOIN b AS T2 ON T1.k = T2.k"
    )[0]
    flipped = extract_joins_from_sql(
        "SELECT 1 FROM a AS T1 JOIN b AS T2 ON T2.k = T1.k"
    )[0]
    assert (same_order.left_table, same_order.right_table) == ("a", "b")
    assert (flipped.left_table, flipped.right_table) == ("a", "b")


# --------------------------------------------------------------------------- #
# The property that matters: the validator accepts what the seeder emits
# --------------------------------------------------------------------------- #


def test_seeded_joins_pass_the_validator_that_used_to_reject_them():
    from governed_bi.corpus.schemas import Column, JoinAsset, LogicalType, TableAsset

    def _col(name: str) -> Column:
        return Column(
            physical_name=name,
            physical_type="TEXT",
            logical_type=LogicalType.string,
            nullable=True,
            is_unique=False,
        )

    tables = [
        TableAsset(
            id="tbl_s_air_carriers",
            schema="s",
            physical_name="Air Carriers",
            columns=[_col("Code")],
        ),
        TableAsset(
            id="tbl_s_airlines",
            schema="s",
            physical_name="Airlines",
            columns=[_col("OP_CARRIER_AIRLINE_ID")],
        ),
    ]
    seeded = extract_joins_from_sql(
        'SELECT 1 FROM "Air Carriers" AS T1 '
        'JOIN "Airlines" AS T2 ON T1.Code = T2.OP_CARRIER_AIRLINE_ID'
    )[0]
    join = JoinAsset(
        id="join_s_air_carriers_airlines",
        left_table="tbl_s_air_carriers",
        right_table="tbl_s_airlines",
        on=seeded.on,
    )

    codes = {f.code for f in validate_corpus([*tables, join])}
    assert "join-on-unparseable" not in codes
    assert "join-on-unresolved" not in codes


# --------------------------------------------------------------------------- #
# Parse once, not twice. Found by profiling: sqlglot was 30% of an offline build.
# --------------------------------------------------------------------------- #


def test_the_seeder_parses_each_statement_once(monkeypatch):
    """`extract_joins_from_sql` and `extract_metrics_from_sql` both read the AST and
    were each parsing the same statement independently."""
    import sqlglot

    from governed_bi.curator import seed as seed_mod

    calls = {"n": 0}
    real = sqlglot.parse_one

    def _counting(*a, **k):
        calls["n"] += 1
        return real(*a, **k)

    monkeypatch.setattr(seed_mod.sqlglot, "parse_one", _counting)

    sqls = [
        "SELECT SUM(a.amt) FROM a JOIN b ON a.k = b.k",
        "SELECT COUNT(*) FROM c JOIN d ON c.k = d.k",
    ]
    seed_mod.seed_from_train_sql(sqls)
    assert calls["n"] == len(sqls), f"expected one parse per statement, got {calls['n']}"


def test_a_shared_tree_produces_the_same_seed_as_two_separate_parses():
    """The optimisation must not change what is extracted."""
    import sqlglot

    from governed_bi.curator.seed import extract_joins_from_sql, extract_metrics_from_sql

    sql = "SELECT SUM(a.amt) FROM a JOIN b ON a.k = b.k"
    tree = sqlglot.parse_one(sql, read="postgres")

    assert extract_joins_from_sql(sql) == extract_joins_from_sql(sql, tree=tree)
    assert extract_metrics_from_sql(sql) == extract_metrics_from_sql(sql, tree=tree)


def test_sharing_a_tree_does_not_mutate_it():
    """`extract_metrics_from_sql` rewrites alias-qualified columns; it must do that on
    a copy, or the second consumer of a shared tree sees rewritten SQL."""
    import sqlglot

    from governed_bi.curator.seed import extract_joins_from_sql, extract_metrics_from_sql

    sql = "SELECT SUM(T1.amt) FROM a AS T1 JOIN b AS T2 ON T1.k = T2.k"
    tree = sqlglot.parse_one(sql, read="postgres")
    before = tree.sql(dialect="postgres")

    extract_metrics_from_sql(sql, tree=tree)
    extract_joins_from_sql(sql, tree=tree)

    assert tree.sql(dialect="postgres") == before


# --------------------------------------------------------------------------- #
# Which rung adds few-shots, pinned against the code rather than the prose.
#
# The ladder tables in the runbook, measurement.md and `arms.Arm`'s own docstring all
# said `seeded` adds few-shots. It cannot: `SeedBundle` carries joins and metrics only,
# and the sole producer of a `FewShotAsset` is the curator agent's `upsert_few_shot`
# tool. So the highest-leakage asset in the corpus — verbatim train question/SQL pairs,
# up to 3 injected per turn — was attributed to the free deterministic rung when it
# actually arrives with the LLM rung, bundled with everything else the curator does.
#
# That matters for reading the ladder, not just for tidiness: it moves where a
# train-derived-template win would show up.
# --------------------------------------------------------------------------- #


def test_the_deterministic_seed_carries_no_few_shots():
    from governed_bi.curator.seed import SeedBundle, seed_from_train_sql

    bundle = seed_from_train_sql(
        [
            "SELECT a.x FROM t1 AS a JOIN t2 AS b ON a.id = b.id",
            "SELECT COUNT(*) / SUM(y) FROM t3",
        ]
    )
    fields = set(SeedBundle.__dataclass_fields__)
    assert fields == {"joins", "metrics"}, (
        f"SeedBundle grew a field; if it now carries few-shots the ladder tables in "
        f"the runbook and measurement.md need updating too. Got {sorted(fields)}"
    )
    assert not hasattr(bundle, "few_shots")


def test_only_the_curator_agent_can_produce_a_few_shot():
    """`upsert_few_shot` is an agent tool. If a deterministic path ever calls it, the
    `seeded` rung starts carrying few-shots and the ladder's attribution changes."""
    import inspect

    from governed_bi.curator import pipeline

    src = inspect.getsource(pipeline.build_curated_corpus)
    # The mechanical portion runs before `if run_agent and model is not None:`.
    mechanical = src.split("if run_agent and model is not None:")[0]
    assert "upsert_few_shot" not in mechanical, (
        "the non-agent build path now writes few-shots; the ladder tables say it does "
        "not"
    )
    assert "seed_from_train_sql" in mechanical
    # The mechanical portion used to also run `_mark_columns_absent_from_gold`. That
    # mask is deleted (B6) and the `seeded` rung now carries joins and metrics only;
    # the guard that it authors no reliability lives in
    # `tests/test_eval_ladder.py::test_the_mechanical_build_authors_no_suspect_columns`.
