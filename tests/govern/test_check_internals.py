"""Drive the real check. Pair every refusal with a statement that must pass."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


CUSTOMERS = frozenset({"customers"})


@pytest.fixture
def layer():
    from governed_bi.govern.layers import Layer

    return Layer


# ── the result's own invariants ──────────────────────────────────────────────


@pytest.mark.parametrize(
    "sql",
    [
        "DROP TABLE customers",
        "SELECT 1; SELECT 2",
        "SELECT id FROM customers FOR UPDATE",
        "SELECT id INTO other FROM customers",
        "WITH d AS (DELETE FROM customers RETURNING *) SELECT * FROM d",
        "SELECT 'unterminated",
        "SELECT pg_read_file('/etc/passwd')",
        "SELECT * FROM customers",
        "SELECT other.secrets.token FROM customers",
        "SELECT id FROM customers, orders",
        "SELECT id FROM generate_series(1, 3)",
        "SELECT id FROM customers",
    ],
)
def test_every_refusal_reports_a_declared_rule_and_its_own_layer(check, sql) -> None:
    """The rule id derives the layer, so the pair is one lookup rather than two literals."""
    from governed_bi.govern.layers import GUARDRAIL_ERROR, PASSED, RULES

    result = check(sql, retrieved_tables=CUSTOMERS)
    assert result["passed"] is False, result
    assert result["reason_code"] not in (PASSED, GUARDRAIL_ERROR), result
    assert result["reason_code"] in RULES, result["reason_code"]
    assert result["failed_layer"] is RULES[result["reason_code"]]
    assert result["layers_evaluated"][-1] is result["failed_layer"], result


def test_a_passing_result_has_no_layer_and_the_control_still_binds(check) -> None:
    """The complement of every refusal above. Also the ``bound`` map's only positive
    assertion: a reference that resolved has to be *recorded* as resolved, or the
    column layer's input is silently empty."""
    result = check(
        "SELECT c.id FROM customers c",
        retrieved_tables=CUSTOMERS,
        allowed_columns=frozenset({"customers.id"}),
    )
    assert result["passed"] is True and result["failed_layer"] is None
    assert result["bound"]["c.id"] == "customers"


def test_first_failure_wins_and_the_order_is_the_enum_order(check, layer) -> None:
    """One statement that violates two layers reports the earlier one, then the later one once that is repaired."""
    both = check("SELECT pg_sleep(nope.x) FROM nope", retrieved_tables=CUSTOMERS)
    assert both["failed_layer"] is layer.FUNCTIONS

    table_only = check(
        "SELECT nope.x FROM nope", retrieved_tables=CUSTOMERS, allowed_columns=frozenset({"nope.x"})
    )
    assert table_only["failed_layer"] is layer.TABLES


def test_a_layer_that_did_not_run_has_no_entry(check, layer) -> None:
    """Absence is not agreement. The cost layer ships disabled, so a passing statement
    must **not** claim it ran."""
    result = check(
        "SELECT c.id FROM customers c",
        retrieved_tables=CUSTOMERS,
        allowed_columns=frozenset({"customers.id"}),
    )
    assert "COST" not in layer.__members__
    assert result["layers_evaluated"] == [
        layer.PARSE, layer.NO_WRITE, layer.FUNCTIONS, layer.BINDING, layer.COLUMNS, layer.TABLES
    ]


def test_an_exception_inside_check_blocks_and_is_countable(check, monkeypatch) -> None:
    """A crash must block and stay distinguishable from a governance decision. See ADR 0006."""
    from importlib import import_module

    from governed_bi.govern.layers import GUARDRAIL_ERROR
    from governed_bi.govern.ledger import attempt_record, guardrail_errors

    def boom(*_args, **_kwargs):
        raise NameError("simulated typo in the function-layer walk")

    # import_module, not `from governed_bi.govern import check`: the package re-exports
    # the *function* under that name, so the attribute lookup returns the callable and
    # the patch would land on nothing.
    check_module = import_module("governed_bi.govern.check")
    monkeypatch.setattr(check_module, "iter_scopes", boom)
    result = check("SELECT c.id FROM customers c", retrieved_tables=CUSTOMERS)
    assert result["passed"] is False
    assert result["reason_code"] == GUARDRAIL_ERROR
    assert result["failed_layer"] is not None, "a swallowed exception must still name a layer"
    assert guardrail_errors([attempt_record(result, "agent")]) == 1

    monkeypatch.undo()
    ordinary = check("DROP TABLE customers", retrieved_tables=CUSTOMERS)
    assert guardrail_errors([attempt_record(ordinary, "agent")]) == 0


# ── binding: the six shapes, each with a passing counterpart ──────────────────


def test_a_correlated_qualified_reference_binds_through_the_ancestor_scope(check) -> None:
    """The positive control for the whole binding rule. If this refused, every
    refusal below would be satisfied by a function that refuses everything."""
    result = check(
        "SELECT c.id FROM customers c WHERE c.id IN (SELECT o.cid FROM orders o WHERE o.cid = c.id)",
        retrieved_tables=frozenset({"customers", "orders"}),
        allowed_columns=frozenset({"customers.id", "orders.cid"}),
    )
    assert result["passed"] is True, result


def test_an_unknown_two_part_qualifier_refuses(check, layer) -> None:
    """``unknown.col`` — the shape ADR 0006 §4 records as missed by the first draft's
    list of six."""
    result = check(
        "SELECT unknown.token FROM customers",
        retrieved_tables=CUSTOMERS,
        allowed_columns=frozenset({"unknown.token"}),
    )
    assert result["failed_layer"] is layer.BINDING
    assert result["reason_code"] == "r_unbound_reference"


def test_a_bare_name_in_a_mixed_base_and_derived_scope_refuses(check, layer) -> None:
    """v1's leftmost-table resolution would bind it to whichever source came first,
    and in an obfuscated corpus that can be the decoy."""
    result = check(
        "SELECT id FROM customers, (SELECT 1 AS n) s",
        retrieved_tables=CUSTOMERS,
        allowed_columns=frozenset({"customers.id"}),
    )
    assert result["failed_layer"] is layer.BINDING
    assert result["reason_code"] == "r_ambiguous_reference"


def test_a_cte_named_after_a_base_table_shadows_it(check) -> None:
    """The CTE shadows the base table. A query-wide name map would refuse a column this statement does not read."""
    result = check(
        "WITH customers AS (SELECT 1 AS id) SELECT id FROM customers",
        retrieved_tables=frozenset(),
        allowed_columns=frozenset(),
        excluded_columns=frozenset({"customers.id"}),
    )
    assert result["passed"] is True, result
    assert result["bound"]["id"] == "derived:customers"


def test_a_bare_having_reference_is_column_checked(check, layer) -> None:
    """``scope.columns`` omits bare ``HAVING`` references, so a column allowlist built
    from it never sees them. This drives the walk that replaced it."""
    result = check(
        "SELECT c.id FROM customers c GROUP BY c.id HAVING count(ssn) > 1",
        retrieved_tables=CUSTOMERS,
        allowed_columns=frozenset({"customers.id"}),
    )
    assert result["failed_layer"] is layer.COLUMNS
    assert "ssn" in result["detail"]


def test_using_keys_are_column_checked_on_both_sides(check, layer) -> None:
    """``USING`` keys are not ``Column`` nodes. The same statement passes once both sides are allowed."""
    both = frozenset({"customers.cid", "orders.cid"})
    result = check(
        "SELECT c.cid FROM customers c JOIN orders o USING (cid)",
        retrieved_tables=frozenset({"customers", "orders"}),
        allowed_columns=both,
    )
    assert result["passed"] is True, result

    half = check(
        "SELECT c.cid FROM customers c JOIN orders o USING (cid)",
        retrieved_tables=frozenset({"customers", "orders"}),
        allowed_columns=frozenset({"customers.cid"}),
    )
    assert half["failed_layer"] is layer.COLUMNS


def test_a_sibling_cte_is_not_a_source_this_scope_can_bind_to(check, layer) -> None:
    """A CTE is visible everywhere; a bare name binds only to this scope's ``FROM``."""
    result = check(
        "WITH pm AS (SELECT c.id AS pid FROM customers c), "
        "cm AS (SELECT c.id AS cid FROM customers c), "
        "j AS (SELECT pm.pid AS jid FROM pm) "
        "SELECT jid FROM j",
        retrieved_tables=CUSTOMERS,
        allowed_columns=frozenset({"customers.id"}),
    )
    assert result["passed"] is True, result
    assert result["bound"]["jid"] == "derived:j"


def test_a_cte_outside_this_scopes_from_cannot_even_be_qualified_against(check, layer) -> None:
    """Naming a CTE this select does not read still refuses."""
    result = check(
        "WITH pm AS (SELECT c.id AS pid FROM customers c) SELECT pm.pid FROM customers",
        retrieved_tables=CUSTOMERS,
        allowed_columns=frozenset({"customers.id"}),
    )
    assert result["failed_layer"] is layer.BINDING
    assert result["reason_code"] == "r_unbound_reference"


def test_two_sources_answering_to_one_name_refuse(check, layer) -> None:
    """Postgres rejects two sources that answer to one name. Binding one of them would invent a name the alias hides."""
    result = check(
        "SELECT a.id FROM customers AS a, orders AS a",
        retrieved_tables=frozenset({"customers", "orders"}),
        allowed_columns=frozenset({"customers.id", "orders.id"}),
    )
    assert result["failed_layer"] is layer.BINDING
    assert result["reason_code"] == "r_ambiguous_reference"

    distinct = check(
        "SELECT a.id, b.id FROM customers AS a JOIN orders AS b ON a.id = b.id",
        retrieved_tables=frozenset({"customers", "orders"}),
        allowed_columns=frozenset({"customers.id", "orders.id"}),
    )
    assert distinct["passed"] is True, distinct


def test_count_star_is_the_carve_out_and_every_other_star_argument_is_not(check) -> None:
    """§2's exception, stated as a pair so the carve-out cannot swallow the rule."""
    ok = check(
        "SELECT count(*) AS n FROM customers",
        retrieved_tables=CUSTOMERS,
        allowed_columns=frozenset(),
    )
    assert ok["passed"] is True, ok

    blocked = check("SELECT max(c.*) FROM customers c", retrieved_tables=CUSTOMERS)
    assert blocked["reason_code"] == "r_whole_row_argument"


def test_a_permitted_aggregate_over_a_bare_alias_still_refuses(check) -> None:
    """The function name is permitted and the argument is a whole row. Empty authorization and a corpus with no such
    column refuse for different reasons."""
    assert check("SELECT max(t) FROM customers t", retrieved_tables=CUSTOMERS)["passed"] is False
    informed = check(
        "SELECT max(t) FROM customers t",
        retrieved_tables=CUSTOMERS,
        allowed_columns=frozenset({"customers.id"}),
    )
    assert informed["reason_code"] == "r_whole_row_reference"


# ── the column and table layers over the binding ──────────────────────────────


def test_excluded_beats_allowed(check, layer) -> None:
    """Two definitions of "excluded" drifting apart was B10. One column, both sets,
    must refuse — the allowlist is not a licence over the exclusion."""
    result = check(
        "SELECT c.ssn FROM customers c",
        retrieved_tables=CUSTOMERS,
        allowed_columns=frozenset({"customers.ssn"}),
        excluded_columns=frozenset({"customers.ssn"}),
    )
    assert result["failed_layer"] is layer.COLUMNS
    assert result["reason_code"] == "r_column_excluded"


def test_hard_block_suspect_is_a_setting_with_both_settings_live(check) -> None:
    """v1 had this setting and ADR 0006's first draft dropped it. Dev and the benchmark
    hard-block; production soft-warns. Both directions asserted, because a setting that
    only ever takes its default is a setting nobody would notice was ignored."""
    from governed_bi.govern.policy import GovernancePolicy

    sql = "SELECT c.alias FROM customers c"
    args = dict(
        retrieved_tables=CUSTOMERS,
        allowed_columns=frozenset({"customers.alias"}),
        suspect_columns=frozenset({"customers.alias"}),
    )
    assert check(sql, policy=GovernancePolicy(hard_block_suspect=True), **args)["passed"] is False
    assert check(sql, policy=GovernancePolicy(hard_block_suspect=False), **args)["passed"] is True


def test_absent_column_authorization_refuses_and_present_authorization_passes(check, layer) -> None:
    """An empty corpus refuses the reference. The same statement passes once the authorization exists."""
    sql = "SELECT c.id FROM customers c"
    absent = check(sql, retrieved_tables=CUSTOMERS)
    assert absent["failed_layer"] is layer.COLUMNS
    assert absent["reason_code"] == "r_column_not_allowed"

    present = check(sql, retrieved_tables=CUSTOMERS, allowed_columns=frozenset({"customers.id"}))
    assert present["passed"] is True


def test_keys_fold_on_both_sides_for_tables_too(check) -> None:
    """B5 is not only about columns: a ``Customers`` licence and a ``customers``
    reference are the same table, and quoting to compensate would send the engine a
    relation that does not exist."""
    result = check(
        "SELECT c.id FROM CUSTOMERS c",
        retrieved_tables=frozenset({"Customers"}),
        allowed_columns=frozenset({"customers.ID"}),
    )
    assert result["passed"] is True, result


def test_default_schema_qualifies_unqualified_references_only(check, layer) -> None:
    """``default_schema`` supplies the qualification the datasource pins — and does not
    silently qualify a reference that already names a different schema."""
    result = check(
        "SELECT c.id FROM customers c",
        retrieved_tables=frozenset({"public.customers"}),
        allowed_columns=frozenset({"customers.id"}),
        default_schema="public",
    )
    assert result["passed"] is True, result

    elsewhere = check(
        "SELECT c.id FROM other.customers c",
        retrieved_tables=frozenset({"public.customers"}),
        allowed_columns=frozenset({"other.customers.id"}),
        default_schema="public",
    )
    assert elsewhere["failed_layer"] is layer.TABLES


def test_a_column_key_without_a_table_is_a_caller_error(check) -> None:
    """A bare name in a column allowlist is not a key — it is a name that matches in
    every table, which is the lake-wide allowlist that made B4 exploitable."""
    with pytest.raises(ValueError):
        check("SELECT c.id FROM customers c", retrieved_tables=CUSTOMERS, allowed_columns=frozenset({"id"}))


def test_a_bare_source_name_in_a_projection_is_a_whole_row_read(check, layer) -> None:
    """``SELECT c FROM customers c`` is the whole row. If a real column shares that name, both Postgres and this code
    read the column."""
    composite = check(
        "SELECT c FROM customers c", retrieved_tables=CUSTOMERS, allowed_columns=frozenset({"customers.id"})
    )
    assert composite["failed_layer"] is layer.BINDING
    assert composite["reason_code"] == "r_whole_row_reference"

    real_column = check(
        "SELECT c FROM customers c", retrieved_tables=CUSTOMERS, allowed_columns=frozenset({"customers.c"})
    )
    assert real_column["passed"] is True, real_column
    assert real_column["bound"]["c"] == "customers", "it bound as a column, not as the row"
