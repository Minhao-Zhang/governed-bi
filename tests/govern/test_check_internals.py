"""Internals the bypass contract does not reach: the rule table, layer order, binding.

Authoring rules applied throughout (``docs/lessons-from-v1.md`` §7):

* **Every test drives the real gate.** None re-derives a rule's arithmetic. v1's
  gold-gate tests re-implemented ``share > THRESHOLD``, so deleting the gate, flipping
  the comparison and reversing the denominator all passed.
* **Assert the effect, not the presence of a constant**, and **never assert a module
  against its own constant** — that passes for an empty tuple.
* **Test the negative case.** A guard that only leaves a trace when it fires cannot
  afterwards be told from one that was never wired up, so every refusal here has a
  paired statement that must pass.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from contracts import needs  # noqa: E402

pytestmark = needs("B")

CUSTOMERS = frozenset({"customers"})


@pytest.fixture
def check():
    from governed_bi.govern.check import check

    return check


@pytest.fixture
def layer():
    from governed_bi.govern.layers import Layer

    return Layer


# ── the verdict's own invariants ──────────────────────────────────────────────


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
    """The rule id **derives** the layer, so this asserts the pair, not one constant.

    ``failed_layer`` is looked up from ``RULES`` inside the constructor. A caller
    cannot name a rule and a layer separately, which is the property that makes two
    tables unable to drift — and this is the test that the lookup is real rather than
    a second literal beside it.
    """
    from governed_bi.govern.layers import GUARDRAIL_ERROR, PASSED, RULES

    verdict = check(sql, licensed=CUSTOMERS)
    assert verdict["passed"] is False, verdict
    assert verdict["reason_code"] not in (PASSED, GUARDRAIL_ERROR), verdict
    assert verdict["reason_code"] in RULES, verdict["reason_code"]
    assert verdict["failed_layer"] is RULES[verdict["reason_code"]]
    assert verdict["layers_evaluated"][-1] is verdict["failed_layer"], verdict


def test_a_passing_verdict_has_no_layer_and_the_control_still_binds(check) -> None:
    """The complement of every refusal above. Also the ``bound`` map's only positive
    assertion: a reference that resolved has to be *recorded* as resolved, or the
    column layer's input is silently empty."""
    verdict = check(
        "SELECT c.id FROM customers c",
        licensed=CUSTOMERS,
        allowed_columns=frozenset({"customers.id"}),
    )
    assert verdict["passed"] is True and verdict["failed_layer"] is None
    assert verdict["bound"]["c.id"] == "customers"


def test_first_failure_wins_and_the_order_is_the_enum_order(check, layer) -> None:
    """Reaching layer N proves 1..N-1 passed — the property graded delivery rests on.

    One statement that violates the function layer **and** the table layer reports
    ``FUNCTIONS``; repair only the function and the same statement reports ``TABLES``.
    Two verdicts from one pair of statements, so a stack that evaluated in a different
    order could not produce both.
    """
    both = check("SELECT pg_sleep(nope.x) FROM nope", licensed=CUSTOMERS)
    assert both["failed_layer"] is layer.FUNCTIONS

    table_only = check(
        "SELECT nope.x FROM nope", licensed=CUSTOMERS, allowed_columns=frozenset({"nope.x"})
    )
    assert table_only["failed_layer"] is layer.TABLES


def test_a_layer_that_did_not_run_has_no_entry(check, layer) -> None:
    """Absence is not agreement. The cost layer ships disabled, so a passing statement
    must **not** claim it ran."""
    verdict = check(
        "SELECT c.id FROM customers c",
        licensed=CUSTOMERS,
        allowed_columns=frozenset({"customers.id"}),
    )
    assert layer.COST not in verdict["layers_evaluated"]
    assert verdict["layers_evaluated"] == [
        layer.PARSE, layer.NO_WRITE, layer.FUNCTIONS, layer.BINDING, layer.COLUMNS, layer.TABLES
    ]


def test_an_exception_inside_check_blocks_and_is_countable(check, monkeypatch) -> None:
    """ADR 0006 §12's chain: a ``NameError`` in a layer walk turns every turn into a
    refusal, ``crash_rate == 0``, every register key present, run declared quotable.

    So the verdict must block **and** be distinguishable from a governance decision.
    The negative half is the second assertion: an ordinary refusal must not count.
    """
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
    verdict = check("SELECT c.id FROM customers c", licensed=CUSTOMERS)
    assert verdict["passed"] is False
    assert verdict["reason_code"] == GUARDRAIL_ERROR
    assert verdict["failed_layer"] is not None, "a swallowed exception must still name a layer"
    assert guardrail_errors([attempt_record(verdict, "agent")]) == 1

    monkeypatch.undo()
    ordinary = check("DROP TABLE customers", licensed=CUSTOMERS)
    assert guardrail_errors([attempt_record(ordinary, "agent")]) == 0


# ── binding: the six shapes, each with a passing counterpart ──────────────────


def test_a_correlated_qualified_reference_binds_through_the_ancestor_scope(check) -> None:
    """The positive control for the whole binding rule. If this refused, every
    refusal below would be satisfied by a function that refuses everything."""
    verdict = check(
        "SELECT c.id FROM customers c WHERE c.id IN (SELECT o.cid FROM orders o WHERE o.cid = c.id)",
        licensed=frozenset({"customers", "orders"}),
        allowed_columns=frozenset({"customers.id", "orders.cid"}),
    )
    assert verdict["passed"] is True, verdict


def test_an_unknown_two_part_qualifier_refuses(check, layer) -> None:
    """``unknown.col`` — the shape ADR 0006 §4 records as missed by the first draft's
    list of six."""
    verdict = check(
        "SELECT unknown.token FROM customers",
        licensed=CUSTOMERS,
        allowed_columns=frozenset({"unknown.token"}),
    )
    assert verdict["failed_layer"] is layer.BINDING
    assert verdict["reason_code"] == "r_unbound_reference"


def test_a_bare_name_in_a_mixed_base_and_derived_scope_refuses(check, layer) -> None:
    """v1's leftmost-table resolution would bind it to whichever source came first,
    and in an obfuscated corpus that can be the decoy."""
    verdict = check(
        "SELECT id FROM customers, (SELECT 1 AS n) s",
        licensed=CUSTOMERS,
        allowed_columns=frozenset({"customers.id"}),
    )
    assert verdict["failed_layer"] is layer.BINDING
    assert verdict["reason_code"] == "r_ambiguous_reference"


def test_a_cte_named_after_a_base_table_shadows_it(check) -> None:
    """Per-scope resolution, not a query-wide name map.

    The table ``customers`` has an **excluded** ``id``. The statement never reads it:
    ``customers`` here is the CTE. A flat name map said otherwise, and that is how v1
    deferred a real table's excluded column onto a reference that never touched it —
    the same map would refuse this statement for a column it does not read.
    """
    verdict = check(
        "WITH customers AS (SELECT 1 AS id) SELECT id FROM customers",
        licensed=frozenset(),
        allowed_columns=frozenset(),
        excluded_columns=frozenset({"customers.id"}),
    )
    assert verdict["passed"] is True, verdict
    assert verdict["bound"]["id"] == "derived:customers"


def test_a_bare_having_reference_is_column_checked(check, layer) -> None:
    """``scope.columns`` omits bare ``HAVING`` references, so a column allowlist built
    from it never sees them. This drives the walk that replaced it."""
    verdict = check(
        "SELECT c.id FROM customers c GROUP BY c.id HAVING count(ssn) > 1",
        licensed=CUSTOMERS,
        allowed_columns=frozenset({"customers.id"}),
    )
    assert verdict["failed_layer"] is layer.COLUMNS
    assert "ssn" in verdict["detail"]


def test_using_keys_are_column_checked_on_both_sides(check, layer) -> None:
    """``USING (col)`` keys are not ``Column`` nodes, so a ``find_all(exp.Column)``
    sweep never sees them — an excluded column was usable as a join key.

    The pair matters: the same statement passes once **both** sides' keys are allowed,
    which is what distinguishes "the key is checked" from "USING always refuses".
    """
    both = frozenset({"customers.cid", "orders.cid"})
    verdict = check(
        "SELECT c.cid FROM customers c JOIN orders o USING (cid)",
        licensed=frozenset({"customers", "orders"}),
        allowed_columns=both,
    )
    assert verdict["passed"] is True, verdict

    half = check(
        "SELECT c.cid FROM customers c JOIN orders o USING (cid)",
        licensed=frozenset({"customers", "orders"}),
        allowed_columns=frozenset({"customers.cid"}),
    )
    assert half["failed_layer"] is layer.COLUMNS


def test_count_star_is_the_carve_out_and_every_other_star_argument_is_not(check) -> None:
    """§2's exception, stated as a pair so the carve-out cannot swallow the rule."""
    ok = check(
        "SELECT count(*) AS n FROM customers",
        licensed=CUSTOMERS,
        allowed_columns=frozenset(),
    )
    assert ok["passed"] is True, ok

    blocked = check("SELECT max(c.*) FROM customers c", licensed=CUSTOMERS)
    assert blocked["reason_code"] == "r_whole_row_argument"


def test_a_permitted_aggregate_over_a_bare_alias_still_refuses(check) -> None:
    """B2 through a function any analytic allowlist would permit: the *name* is fine
    and the argument is a whole row.

    Two paths reach the same answer, and both are asserted because they refuse for
    different reasons. With no column authorization the reference has nothing to be
    allowed against; with a corpus that declares no column called ``t``, it is
    positively identified as a whole-row reference and refuses one layer earlier.
    """
    assert check("SELECT max(t) FROM customers t", licensed=CUSTOMERS)["passed"] is False
    informed = check(
        "SELECT max(t) FROM customers t",
        licensed=CUSTOMERS,
        allowed_columns=frozenset({"customers.id"}),
    )
    assert informed["reason_code"] == "r_whole_row_reference"


# ── the column and table layers over the binding ──────────────────────────────


def test_excluded_beats_allowed(check, layer) -> None:
    """Two definitions of "excluded" drifting apart was B10. One column, both sets,
    must refuse — the allowlist is not a licence over the exclusion."""
    verdict = check(
        "SELECT c.ssn FROM customers c",
        licensed=CUSTOMERS,
        allowed_columns=frozenset({"customers.ssn"}),
        excluded_columns=frozenset({"customers.ssn"}),
    )
    assert verdict["failed_layer"] is layer.COLUMNS
    assert verdict["reason_code"] == "r_column_excluded"


def test_hard_block_suspect_is_a_knob_with_both_settings_live(check) -> None:
    """v1 had this knob and ADR 0006's first draft dropped it. Dev and the benchmark
    hard-block; production soft-warns. Both directions asserted, because a knob that
    only ever takes its default is a knob nobody would notice was ignored."""
    from governed_bi.govern.policy import GovernancePolicy

    sql = "SELECT c.alias FROM customers c"
    args = dict(
        licensed=CUSTOMERS,
        allowed_columns=frozenset({"customers.alias"}),
        suspect_columns=frozenset({"customers.alias"}),
    )
    assert check(sql, policy=GovernancePolicy(hard_block_suspect=True), **args)["passed"] is False
    assert check(sql, policy=GovernancePolicy(hard_block_suspect=False), **args)["passed"] is True


def test_absent_column_authorization_refuses_and_present_authorization_passes(check, layer) -> None:
    """G1 at the column layer, with the negative half.

    Left ``UNSET``, the layer cannot evaluate its own precondition and blocks. The
    second assertion is what stops that from being read as "this statement is bad": the
    identical statement passes once the authorization exists.
    """
    sql = "SELECT c.id FROM customers c"
    absent = check(sql, licensed=CUSTOMERS)
    assert absent["failed_layer"] is layer.COLUMNS
    assert absent["reason_code"] == "r_column_authorization_unavailable"

    present = check(sql, licensed=CUSTOMERS, allowed_columns=frozenset({"customers.id"}))
    assert present["passed"] is True


def test_keys_fold_on_both_sides_for_tables_too(check) -> None:
    """B5 is not only about columns: a ``Customers`` licence and a ``customers``
    reference are the same table, and quoting to compensate would send the engine a
    relation that does not exist."""
    verdict = check(
        "SELECT c.id FROM CUSTOMERS c",
        licensed=frozenset({"Customers"}),
        allowed_columns=frozenset({"customers.ID"}),
    )
    assert verdict["passed"] is True, verdict


def test_default_schema_qualifies_unqualified_references_only(check, layer) -> None:
    """``default_schema`` supplies the qualification the datasource pins — and does not
    silently qualify a reference that already names a different schema."""
    verdict = check(
        "SELECT c.id FROM customers c",
        licensed=frozenset({"public.customers"}),
        allowed_columns=frozenset({"customers.id"}),
        default_schema="public",
    )
    assert verdict["passed"] is True, verdict

    elsewhere = check(
        "SELECT c.id FROM other.customers c",
        licensed=frozenset({"public.customers"}),
        allowed_columns=frozenset({"other.customers.id"}),
        default_schema="public",
    )
    assert elsewhere["failed_layer"] is layer.TABLES


def test_a_column_key_without_a_table_is_a_caller_error(check) -> None:
    """A bare name in a column allowlist is not a key — it is a name that matches in
    every table, which is the lake-wide allowlist that made B4 exploitable."""
    with pytest.raises(ValueError):
        check("SELECT c.id FROM customers c", licensed=CUSTOMERS, allowed_columns=frozenset({"id"}))


def test_a_bare_source_name_in_a_projection_is_a_whole_row_read(check, layer) -> None:
    """B2 without a function at all: in Postgres ``SELECT c FROM customers c`` returns
    the whole row as a composite value.

    No function layer to catch it, and zero ``Column`` nodes for the columns it emits —
    so the only thing standing between it and the analyst is that the bare name binds to
    a *column* key that the allowlist does not contain. The pair is the case where the
    engine's own resolution differs: if a real column shares the alias's name, Postgres
    reads the column, and so do we.
    """
    composite = check(
        "SELECT c FROM customers c", licensed=CUSTOMERS, allowed_columns=frozenset({"customers.id"})
    )
    assert composite["failed_layer"] is layer.BINDING
    assert composite["reason_code"] == "r_whole_row_reference"

    real_column = check(
        "SELECT c FROM customers c", licensed=CUSTOMERS, allowed_columns=frozenset({"customers.c"})
    )
    assert real_column["passed"] is True, real_column
    assert real_column["bound"]["c"] == "customers", "it bound as a column, not as the row"
