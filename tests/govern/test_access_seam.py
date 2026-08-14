"""ADR 0012: the access seam, and the claim that its default adapter changes nothing.

Two halves, and the first is the one that had to be proved rather than asserted.

**Behaviour identity.** The v4 arm is the measurement control, so an authorization layer
that moved one verdict would silently retire every number in ``runs/``. The proof here is
not "the tests still pass": it is that under the open grant all three of
:class:`~governed_bi.govern.access.ResolvedGrant`'s predicates are *constant functions*, so
the three branches added to ``check()`` are unreachable — plus a run of the whole adversarial
suite through all three spellings of "open" with byte-equal verdicts, plus a positive control
that a restrictive grant does move them. Without the last of those the equality is vacuous.

**The seam itself.** ``r_table_not_authorized`` must fire *and be attributed correctly*:
refusing a licensed-but-unauthorized table as ``r_table_not_licensed`` would be a bypass with
a green tick on it, which is the distinction ``adversarial_run`` measures separately and the
reason open-work.md §4.2 asked for the split at all.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from contracts import needs  # noqa: E402

pytestmark = needs("B")


# ── the world these tests read against ────────────────────────────────────────


@pytest.fixture(scope="module")
def world():
    """The shipped adversarial world, resolved. One fixture, not a per-test lake."""
    from governed_bi.govern.adversarial import build_world_fixture, load_adversarial_suite

    suite = load_adversarial_suite()
    return suite, build_world_fixture(suite.world)


def _check(fixture, sql, grant):
    from dataclasses import replace

    from governed_bi.govern.check import check
    from governed_bi.govern.policy import DEFAULT_DIALECT, GovernancePolicy

    return check(
        sql,
        licensed=fixture.licensed,
        corpus=fixture.corpus,
        default_schema=fixture.default_schema,
        dialect=DEFAULT_DIALECT,
        policy=replace(GovernancePolicy(), access_grant=grant),
    )


def _prepare(fixture, sql, grant):
    from dataclasses import replace

    from governed_bi.govern.pipeline import prepare
    from governed_bi.govern.policy import DEFAULT_DIALECT, GovernancePolicy

    return prepare(
        sql,
        licensed=fixture.licensed,
        corpus=fixture.corpus,
        spellings=fixture.spellings,
        ambiguous_folds=fixture.ambiguous,
        spellings_by_table=fixture.by_table,
        default_schema=fixture.default_schema,
        dialect=DEFAULT_DIALECT,
        policy=replace(GovernancePolicy(), access_grant=grant),
    )


# ── half one: the default adapter changes nothing ─────────────────────────────


def test_the_shipped_default_is_the_open_grant() -> None:
    """``GovernancePolicy()`` authorizes everything, and the guard that says so runs."""
    from governed_bi.govern.policy import GovernancePolicy
    from governed_bi.ports import OPEN_GRANT

    assert GovernancePolicy().access_grant == OPEN_GRANT
    assert GovernancePolicy().access_grant.is_open


def test_every_predicate_of_the_open_grant_is_a_constant_function() -> None:
    """The mechanical half of behaviour identity.

    ``check()`` gained exactly three branches, one per predicate. If each predicate answers the
    same for every input then no branch can fire, so ``check()`` is pointwise the function it was
    before the seam existed.

    **The docstring used to claim this was "stronger than any number of green tests, because it
    does not depend on which inputs the tests happen to use". It is seven inputs.** Seven strings
    cannot establish a property of every string; what makes the claim hold is that
    :meth:`ResolvedGrant.authorizes_table` *returns* ``True`` unconditionally under
    ``Reach.every_table``, which is a fact about the source and not about this list. The probes
    are a cheap tripwire on that source changing — nothing more, and the sentence claiming more
    was the same overreach the seam's other claims were reviewed for on 2026-08-12.
    ``govern/access.py::_assert_the_default_adapter_is_inert`` runs four of them at import, which
    is where a tripwire belongs.
    """
    from governed_bi.govern.access import OPEN_RESOLVED

    probes = [
        "",
        "sales.orders",
        "SALES.ORDERS",
        "sales.orders.amount",
        "a.b.c.d",
        "public.anything",
        "'; DROP TABLE t --",
    ]
    assert all(OPEN_RESOLVED.authorizes_table(p) for p in probes)
    assert not any(OPEN_RESOLVED.denies_column(p) for p in probes)
    assert not any(OPEN_RESOLVED.refuses_for_row_predicate(p) for p in probes)
    assert OPEN_RESOLVED.is_open


def test_three_spellings_of_open_produce_identical_verdicts(world) -> None:
    """The default policy, an explicit ``OPEN_GRANT``, and the default *adapter* agree.

    Run over every case in the shipped suite, comparing the whole verdict — ``bound`` and
    ``layers_evaluated`` included, because a rule that fired one layer earlier would leave
    ``passed`` alone and change those.
    """
    from governed_bi.govern.access import LOCAL_PRINCIPAL, OpenAccessPolicy
    from governed_bi.govern.policy import GovernancePolicy
    from governed_bi.ports import OPEN_GRANT

    suite, fixture = world
    adapter_grant = OpenAccessPolicy().grant_for(LOCAL_PRINCIPAL)
    default_grant = GovernancePolicy().access_grant
    for case in suite.cases:
        a = _check(fixture, case.sql, default_grant)
        b = _check(fixture, case.sql, OPEN_GRANT)
        c = _check(fixture, case.sql, adapter_grant)
        assert a == b == c, case.id
        assert _prepare(fixture, case.sql, OPEN_GRANT).sql == _prepare(
            fixture, case.sql, adapter_grant
        ).sql, case.id


def test_a_restrictive_grant_does_move_a_verdict(world) -> None:
    """The positive control for the test above.

    Without it, ``open == open == open`` would also hold for a seam that was never wired
    into ``check()`` at all — which is precisely the class of test audit D13 catalogued.
    """
    from governed_bi.ports import OPEN_GRANT

    suite, fixture = world
    sql = "SELECT a.actor FROM sales.audit_log AS a"
    assert _check(fixture, sql, OPEN_GRANT)["passed"]

    withheld = _check(fixture, sql, suite.world.grant())
    assert not withheld["passed"] and withheld["reason_code"] == "r_table_not_authorized"


def test_the_open_grant_still_licenses_nothing_extra(world) -> None:
    """Authorization is not a licence. Under the open grant an unlicensed table still
    refuses, so ``reach = every_table`` cannot be read as "and retrieval found it too"."""
    from governed_bi.ports import OPEN_GRANT

    _, fixture = world
    verdict = _check(fixture, "SELECT p.salary FROM sales.payroll AS p", OPEN_GRANT)
    assert verdict["reason_code"] == "r_table_not_licensed"


# ── half two: the seam refuses, and is attributed correctly ───────────────────


@pytest.mark.parametrize(
    "sql, rule",
    [
        # licensed and not authorized -> the new rule, never the licence rule
        ("SELECT a.actor FROM sales.audit_log AS a", "r_table_not_authorized"),
        # in neither set -> the licence, because the licence is asked first
        ("SELECT p.salary FROM sales.payroll AS p", "r_table_not_licensed"),
        # authorized and not licensed -> still the licence; a grant cannot widen one
        ("SELECT l.entry FROM sales.ledger AS l", "r_table_not_licensed"),
        # denied column of an authorized table
        ("SELECT h.employee_note FROM sales.hr_notes AS h", "r_column_not_authorized"),
        # a declared row predicate this engine does not apply
        ("SELECT t.subject FROM sales.tickets AS t", "r_row_predicate_unenforced"),
    ],
)
def test_each_authorization_rule_fires_under_its_own_name(world, sql: str, rule: str) -> None:
    """Attribution, driven through the real ``check()``.

    "It was refused" is not the claim. ``r_table_not_authorized`` reported as
    ``r_table_not_licensed`` would send an integrator to debug their router for a permission
    decision, and would put a permission refusal in the bucket open-work.md §4.2 counts as
    retrieval misses — the exact conflation this ADR exists to end.
    """
    from governed_bi.govern.layers import RULES

    suite, fixture = world
    verdict = _check(fixture, sql, suite.world.grant())
    assert not verdict["passed"]
    assert verdict["reason_code"] == rule, verdict["detail"]
    assert verdict["failed_layer"] is RULES[rule]


def test_an_unauthorized_statement_leaves_prepare_with_nothing_to_execute(world) -> None:
    """The verdict is not the artifact; the string is.

    ``tools/mutation_catalogue.py``'s ``m1-guard-bypass`` is the incident: ``prepare()``
    handed back runnable SQL for a refused verdict while 133/133 tests passed.
    """
    suite, fixture = world
    for sql in (
        "SELECT a.actor FROM sales.audit_log AS a",
        "SELECT h.employee_note FROM sales.hr_notes AS h",
        "SELECT t.subject FROM sales.tickets AS t",
    ):
        prepared = _prepare(fixture, sql, suite.world.grant())
        assert prepared.sql is None, prepared.sql


def test_a_predicate_the_database_enforces_does_not_refuse(world) -> None:
    """``enforcement = "database_role"`` is the operator's claim, recorded and not verified.

    If it refused, the only two settings would be "refuse everything with a predicate" and
    "no seam", and nobody would declare one.
    """
    suite, fixture = world
    verdict = _check(fixture, "SELECT l.id FROM sales.leads AS l", suite.world.grant())
    assert verdict["passed"], verdict["detail"]
    assert _prepare(fixture, "SELECT l.id FROM sales.leads AS l", suite.world.grant()).sql


def test_denial_is_keyed_on_the_bound_column_not_on_the_name(world) -> None:
    """``sales.leads`` declares a column spelled like the denied one and is not denied it."""
    suite, fixture = world
    assert _check(fixture, "SELECT l.employee_note FROM sales.leads AS l", suite.world.grant())[
        "passed"
    ]


def test_grant_keys_fold_the_way_the_corpus_does(world) -> None:
    """An integrator writes ``Sales.Orders``; the statement writes ``sales.orders``.

    The folding is ``govern.identifiers``' — the same function the licence and the column
    sets go through — so the integrator never learns that the two spellings were different.
    A seam that made them learn it would be shallower than the getter it replaced.
    """
    from governed_bi.ports import Grant, Reach

    _, fixture = world
    grant = Grant(reach=Reach.listed, tables=frozenset({"Sales.Orders", "SALES.CUSTOMERS"}))
    passed = _check(
        fixture,
        "SELECT c.id, o.amount FROM sales.customers AS c JOIN sales.orders AS o "
        "ON o.customer_id = c.id",
        grant,
    )
    assert passed["passed"], passed["detail"]


def test_a_malformed_policy_key_raises_rather_than_blocking(world) -> None:
    """A broken policy file is a caller error, not an unsafe query.

    ``check()`` resolves the grant outside its own ``except`` for the reason it normalises
    ``licensed`` there: a blocked verdict would record "this query was unsafe" for "the
    authorization argument was never wired up" (ADR 0006 G1's own wording).
    """
    from governed_bi.ports import Grant, Reach

    _, fixture = world
    with pytest.raises(ValueError):
        _check(
            fixture,
            "SELECT c.id FROM sales.customers AS c",
            Grant(reach=Reach.listed, tables=frozenset({"a.b.c"})),
        )


# ── the value type refuses to be ambiguous ────────────────────────────────────


def test_a_grant_with_no_reach_authorizes_nothing() -> None:
    """The default is deny. An adapter that returned before deciding must not open a door."""
    from governed_bi.govern.access import resolve_grant
    from governed_bi.ports import Grant

    resolved = resolve_grant(Grant(), None)
    assert not resolved.authorizes_table("sales.orders")
    assert not resolved.is_open


@pytest.mark.parametrize(
    "build, expect",
    [
        pytest.param(
            lambda G, R, P: G(reach=R.every_table, tables=frozenset({"a.b"})),
            "reach=every_table",
            id="open_and_listed_at_once",
        ),
        pytest.param(
            lambda G, R, P: G(
                row_predicates=(
                    P(table="a.b", expression="x"),
                    P(table="A.B", expression="y"),
                )
            ),
            "two row predicates",
            id="two_predicates_for_one_table",
        ),
        pytest.param(
            lambda G, R, P: G(reach=R.listed, tables=frozenset({"  "})),
            "cannot be blank",
            id="blank_key",
        ),
    ],
)
def test_a_contradictory_grant_fails_to_construct(build, expect: str) -> None:
    """Three ways a policy file can mean two things. Each raises where it is written down,
    not at the first query that touches it."""
    from governed_bi.ports import Grant, Reach, RowPredicate

    with pytest.raises(ValueError, match=expect):
        build(Grant, Reach, RowPredicate)


def test_a_principal_must_have_an_id() -> None:
    from governed_bi.ports import Principal

    with pytest.raises(ValueError, match="must have an id"):
        Principal(id="")


def test_the_digest_moves_with_the_content_and_not_with_the_order() -> None:
    """Whoever records the turn's security configuration needs one stable value."""
    from governed_bi.ports import Grant, Reach

    a = Grant(reach=Reach.listed, tables=frozenset({"s.a", "s.b"}))
    b = Grant(reach=Reach.listed, tables=frozenset({"s.b", "s.a"}))
    c = Grant(reach=Reach.listed, tables=frozenset({"s.a"}))
    assert a.digest() == b.digest()
    assert a.digest() != c.digest()


# ── two adapters, and the composition algebra the second one owns ─────────────


def test_both_adapters_satisfy_the_port() -> None:
    """"Two adapters justify the seam" as an assertion rather than as a sentence.

    ``ports.py``'s own rule is that a single-adapter seam is rejected; this file is where
    that rule is checked for the newest one.
    """
    from governed_bi.govern.access import OpenAccessPolicy, StaticRoleAccessPolicy
    from governed_bi.ports import AccessPolicy

    assert isinstance(OpenAccessPolicy(), AccessPolicy)
    assert isinstance(StaticRoleAccessPolicy({}), AccessPolicy)


POLICY_FILE = """
version = "1"

[role.analyst]
tables = ["sales.orders"]
denied_columns = ["sales.customers.email"]

[[role.analyst.row_predicate]]
table = "sales.orders"
expression = "region_id = 3"
enforcement = "database_role"

[role.support]
tables = ["sales.customers"]

[role.auditor]
reach = "every_table"
"""


def _policy(tmp_path: Path, text: str = POLICY_FILE):
    from governed_bi.govern.access import StaticRoleAccessPolicy

    path = tmp_path / "access.toml"
    path.write_text(text, encoding="utf-8")
    return StaticRoleAccessPolicy.from_toml(path)


def test_the_reference_adapter_unions_grants_and_denials(tmp_path: Path) -> None:
    """Grants are additive, denials are absolute. Stated once, here, so no fork restates it."""
    from governed_bi.ports import Principal, Reach

    policy = _policy(tmp_path)
    grant = policy.grant_for(Principal(id="p", roles=frozenset({"analyst", "support"})))
    assert grant.reach is Reach.listed
    assert grant.tables == frozenset({"sales.orders", "sales.customers"})
    assert grant.denied_columns == frozenset({"sales.customers.email"})

    # `auditor` widens the reach and does **not** lift the other role's denial.
    both = policy.grant_for(Principal(id="p", roles=frozenset({"analyst", "auditor"})))
    assert both.reach is Reach.every_table
    assert both.tables == frozenset()
    assert both.denied_columns == frozenset({"sales.customers.email"})
    assert not both.is_open


def test_an_unknown_role_authorizes_nothing(tmp_path: Path) -> None:
    """Not an error and not a wildcard. A typo'd role name must fail closed and silently
    stay closed, because the alternative to "no grant" is "some grant nobody wrote"."""
    from governed_bi.ports import Principal

    grant = _policy(tmp_path).grant_for(Principal(id="p", roles=frozenset({"nobody"})))
    assert grant.tables == frozenset() and not grant.is_open


def test_two_roles_disagreeing_about_a_predicate_raises(tmp_path: Path) -> None:
    """Picking one, or OR-ing two expressions this engine never parses, would be inventing
    an authorization."""
    from governed_bi.ports import Principal

    text = POLICY_FILE + """
[role.other]
tables = ["sales.orders"]

[[role.other.row_predicate]]
table = "sales.orders"
expression = "region_id = 9"
"""
    policy = _policy(tmp_path, text)
    with pytest.raises(ValueError, match="two roles declaring different row predicates"):
        policy.grant_for(Principal(id="p", roles=frozenset({"analyst", "other"})))


@pytest.mark.parametrize(
    "find, replace, expect",
    [
        pytest.param('version = "1"', 'version = "2"', "version is", id="wrong_version"),
        pytest.param(
            'tables = ["sales.orders"]', 'tables = ["a.b.c"]', "part(s)", id="table_key_too_deep"
        ),
        pytest.param(
            'denied_columns = ["sales.customers.email"]',
            'denied_columns = ["email"]',
            "part(s)",
            id="bare_column_name",
        ),
        pytest.param(
            'enforcement = "database_role"', 'enforcement = "inject"', "not one of", id="inject"
        ),
        pytest.param(
            'reach = "every_table"',
            'reach = "every_table"\ntables = ["sales.orders"]',
            "one of the two is a mistake",
            id="open_and_listed",
        ),
    ],
)
def test_the_policy_file_fails_at_load_not_at_query_time(
    tmp_path: Path, find: str, replace: str, expect: str
) -> None:
    """Five shapes, five load failures. A policy file is read once and enforced thousands of
    times, so a key that only fails on the query that touches it fails in production.

    ``inject`` is the one that matters most: there is no such enforcement, ADR 0012 rejects
    rewriting a checked statement, and a vocabulary that cannot spell the dangerous option is
    how that stays rejected.
    """
    broken = POLICY_FILE.replace(find, replace, 1)
    assert broken != POLICY_FILE, "the parametrised break did not match the fixture"
    with pytest.raises(ValueError) as err:
        _policy(tmp_path, broken)
    assert expect in str(err.value), str(err.value)


def test_a_policy_file_with_no_roles_fails_to_load(tmp_path: Path) -> None:
    """A file that grants nothing is a configuration mistake, not a lockdown. Refusing it
    means an operator who meant "deny everyone" has to write that down."""
    with pytest.raises(ValueError, match=r"no \[role"):
        _policy(tmp_path, 'version = "1"\n')


# ── the tool bounds, which have no statement for the layer stack to read ──────


def test_tool_bounds_default_to_the_open_grant() -> None:
    """The other half of behaviour identity: ``serve/`` constructs ``ToolBounds`` today
    without a grant, and must keep getting the answers it got."""
    from governed_bi.govern.bounds import ToolBounds

    bounds = ToolBounds(
        licensed=frozenset({"sales.orders"}), readable_assets=frozenset({"sales.orders"})
    )
    assert bounds.grant.is_open
    assert bounds.may_inspect_schema("sales.orders")
    assert bounds.may_sample("sales.orders.amount")
    assert not bounds.may_inspect_schema("sales.payroll")


def _restrictive_grant():
    """Authorizes ``sales.orders`` and denies one of its columns. Folded, as the bounds want."""
    from governed_bi.govern.access import resolve_grant
    from governed_bi.ports import Grant, Reach

    return resolve_grant(
        Grant(
            reach=Reach.listed,
            tables=frozenset({"sales.orders"}),
            denied_columns=frozenset({"sales.orders.amount"}),
        ),
        None,
    )


def test_tool_bounds_ask_the_grant_as_well_as_the_licence() -> None:
    """``inspect_schema`` and ``sample_rows`` build no statement the layer stack can read,
    so authorization has to be asked here or those two tools are the way around it."""
    from governed_bi.govern.bounds import ToolBounds

    bounds = ToolBounds(
        licensed=frozenset({"sales.orders", "sales.audit_log"}),
        readable_assets=frozenset(),
        grant=_restrictive_grant(),
        withheld=frozenset({"sales.audit_log", "sales.orders.amount"}),
    )
    assert bounds.may_inspect_schema("sales.orders")
    assert not bounds.may_inspect_schema("sales.audit_log")
    assert not bounds.may_sample("sales.orders.amount")
    assert bounds.may_sample("sales.orders.id")


def test_a_restrictive_grant_without_a_disclosure_set_will_not_construct() -> None:
    """The wiring failure that produced the ``inspect_schema`` hole, made unrepresentable.

    ``may_inspect_schema`` is a **table**-level test and ``inspect_schema`` returns **column**
    metadata, so the table test alone let a grant denying ``sales.orders.amount`` hand the model
    that column's id, physical name, type and nullability — while the rendered block, narrowed
    by ``withheld_by_grant``, correctly omitted it. The two answers to "what may this principal
    see" disagreed, which is exactly what ADR 0012 §8.4's one-function design claims to prevent.

    An **empty** set is a legitimate answer — a grant may deny a column no corpus declares — so
    emptiness cannot be the signal that nobody computed one. ``None`` is, and it raises.
    """
    from governed_bi.govern.bounds import ToolBounds

    with pytest.raises(ValueError, match="withheld"):
        ToolBounds(licensed=frozenset({"sales.orders"}), grant=_restrictive_grant())

    empty = ToolBounds(
        licensed=frozenset({"sales.orders"}),
        grant=_restrictive_grant(),
        withheld=frozenset(),
    )
    assert empty.discloses("sales.orders.amount"), (
        "an explicitly empty disclosure set is the caller saying nothing is withheld"
    )


def test_the_bounds_fold_a_mixed_case_key_the_way_the_grant_was_folded() -> None:
    """``may_sample``'s denial test failed **open**, and only its sibling was written down.

    ADR 0012 §8's last paragraph and ``docs/enterprise-fork.md`` described the
    ``may_inspect_schema`` half — a raw licensed key against a folded grant, a *false refusal*,
    "it fails closed". The adjacent line did not: ``denies_column`` took the same raw key, so a
    grant denying ``sales.orders.amount`` answered ``False`` for the asset id
    ``Sales.Orders.Amount`` and ``sample_rows`` proceeded to build a statement. ``check()``
    folds both sides and refused it, so no value left the box — but the ledger row was spent,
    which is the opposite of the "no ledger row spent" the docstring claimed.
    """
    from governed_bi.govern.bounds import ToolBounds

    bounds = ToolBounds(
        licensed=frozenset({"Sales.Orders"}),
        readable_assets=frozenset(),
        grant=_restrictive_grant(),
        withheld=frozenset(),
    )
    assert bounds.may_inspect_schema("Sales.Orders"), (
        "a mixed-case licensed key is still a false refusal against a folded grant"
    )
    assert not bounds.may_sample("Sales.Orders.Amount"), (
        "the denial test compared a raw asset id against a folded denial set, so a denied "
        "column reached the statement builder"
    )
    assert bounds.may_sample("Sales.Orders.Id")
