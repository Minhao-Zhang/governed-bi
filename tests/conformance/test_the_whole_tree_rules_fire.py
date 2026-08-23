"""V17b, V19 and V23: the three rules that need more than one asset to be about.

They are in ``NOT_LOCAL`` in ``test_corpus_conformance_rules_fire.py``, so that file proves they
are *documented* and this one proves they can *fire* — which is the half that matters. V11 reported
zero on a corpus with 333 violations, and what made that possible was a rule with no case.

Each rule gets a negative control beside the positive one. A rule that fires on everything is not a
rule, and the failure mode is quiet: on a tree with pre-existing findings a permanently-firing rule
just raises the pin count and nothing looks wrong.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))

import check_corpus_conformance as cc

Asset = tuple[str, dict[str, Any], Path]


def _asset(kind: str, body: dict[str, Any], name: str = "a.yaml") -> Asset:
    return (kind, body, Path(name))


def _table(table_id: str, *columns: str, **over: Any) -> Asset:
    return _asset(
        "table",
        {
            "id": table_id,
            "schema": table_id.split(".")[0],
            "physical_name": table_id.split(".")[-1],
            "summary": f"{table_id.split('.')[-1]} holds one row per thing.",
            "body": "Grain is one thing.",
            "columns": [{"name": c, "summary": f"The {c} of this row."} for c in columns],
            **over,
        },
        f"tbl_{table_id.replace('.', '_')}.yaml",
    )


def _metric(base: str, expression: str) -> Asset:
    return _asset(
        "metric",
        {
            "id": "m.rate",
            "name": "rate",
            "base_table": base,
            "expression": expression,
            "summary": "rate is a rate over the base table.",
            "body": "The definition.",
        },
        "metric_rate.yaml",
    )


def _join(left: str, right: str) -> Asset:
    return _asset(
        "join",
        {
            "id": f"j.{left}_{right}",
            "left_table": left,
            "right_table": right,
            "summary": f"{left} joins {right} on the shared key.",
            "body": "ON left.key = right.key",
        },
        "join.yaml",
    )


# ── V17b ──────────────────────────────────────────────────────────────────────


def test_v17b_fires_on_a_column_that_is_on_no_table() -> None:
    findings = cc.check_metric_bindings(
        [_table("addr.zip_data", "zip_code"), _metric("addr.zip_data", "COUNT(grade_points)")]
    )
    assert findings, "an identifier on no table in the corpus must fire"
    assert "grade_points" in str(findings[0])
    assert "no table in this corpus" in str(findings[0])


def test_v17b_fires_on_a_column_that_needs_an_undeclared_join() -> None:
    """The half that makes this a rule and not a lint: an expression reading another table's
    column is a query with an undeclared join in it, and the engine cannot write that join."""
    findings = cc.check_metric_bindings(
        [
            _table("cs.registration", "sid"),
            _table("cs.course", "credit"),
            _metric("cs.registration", "SUM(credit)"),
        ]
    )
    assert findings, "a column reachable only through a join must fire"
    assert "declared join" in str(findings[0])


def test_v17b_passes_when_the_column_is_on_the_base_table() -> None:
    assert (
        cc.check_metric_bindings(
            [_table("addr.zip_data", "zip_code"), _metric("addr.zip_data", "COUNT(zip_code)")]
        )
        == []
    )


def test_v17b_accepts_a_qualified_reference_through_a_declared_join() -> None:
    """The negative control that keeps the rule from being "one table only". A declared join plus
    a qualified reference is exactly the shape the engine *can* write."""
    assert (
        cc.check_metric_bindings(
            [
                _table("cs.registration", "sid"),
                _table("cs.course", "credit"),
                _join("cs.registration", "cs.course"),
                _metric("cs.registration", "SUM(course.credit)"),
            ]
        )
        == []
    )


def test_v17b_does_not_accept_a_bare_name_because_a_joined_table_happens_to_have_it() -> None:
    """SQL would resolve this ambiguously or not at all, so accepting it here would bless an
    expression the warehouse rejects."""
    findings = cc.check_metric_bindings(
        [
            _table("cs.registration", "sid"),
            _table("cs.course", "credit"),
            _join("cs.registration", "cs.course"),
            _metric("cs.registration", "SUM(credit)"),
        ]
    )
    assert findings, "an unqualified reference to a joined table's column must still fire"


# ── V19 ───────────────────────────────────────────────────────────────────────


def test_v19_fires_when_a_body_names_an_excluded_column() -> None:
    """ADR 0003's finding, verbatim: an asset naming a `governance.excluded` column in text that
    is then injected into the SQL prompt. The column is hidden and its name is not."""
    findings = cc.check_excluded_not_named(
        [
            _table(
                "hr.people",
                "name",
                columns=[
                    {"name": "salary", "summary": "The pay.", "governance": {"excluded": True}}
                ],
            ),
            _asset(
                "term",
                {
                    "id": "t.pay",
                    "summary": "pay: what a person earns in a year.",
                    "body": "Read this from the salary column.",
                },
            ),
        ]
    )
    assert findings, "an excluded column named in a body must fire"
    assert "salary" in str(findings[0])


def test_v19_ignores_a_summary_because_a_summary_never_reaches_the_prompt() -> None:
    """**`body`, not `summary`.** `serve/context.py` reads `body`; `summary` goes to the retrieval
    index. A name in a summary is a routing signal and a name in a body is a disclosure, and a
    rule that conflated them would fire on the wrong field."""
    findings = cc.check_excluded_not_named(
        [
            _table(
                "hr.people",
                "name",
                columns=[
                    {"name": "salary", "summary": "The pay.", "governance": {"excluded": True}}
                ],
            ),
            _asset(
                "term",
                {
                    "id": "t.pay",
                    "summary": "pay: the salary a person earns in a year.",
                    "body": "Read this from the compensation figure.",
                },
            ),
        ]
    )
    assert findings == [], "a summary must not fire V19"


def test_v19_has_no_population_when_nothing_is_excluded() -> None:
    """Measured 2026-08-23: zero assets are excluded in either corpus, so the rule cannot refuse a
    legitimate asset today. That is why adding it was free, and it is worth a test because the
    argument stops holding the day an asset is excluded."""
    assert (
        cc.check_excluded_not_named(
            [
                _table("hr.people", "salary"),
                _asset(
                    "term",
                    {
                        "id": "t.pay",
                        "summary": "pay: what a person earns.",
                        "body": "Read this from the salary column.",
                    },
                ),
            ]
        )
        == []
    )


# ── V23 ───────────────────────────────────────────────────────────────────────


def test_v23_fires_on_two_files_declaring_one_id() -> None:
    """The defect `corpus/store.py::write` produces on an existing id. It passes every other rule
    here, loads with zero problems, and then raises `duplicate index id` in `build_index` --
    **after** the commit. Which is why a bundle is a diff and never a file copy."""
    findings = cc.check_unique_ids(
        [
            _asset("term", {"id": "t.pay", "summary": "s", "body": "b"}, "one.yaml"),
            _asset("term", {"id": "t.pay", "summary": "s2", "body": "b2"}, "two.yaml"),
        ]
    )
    assert findings, "a duplicate id must fire"
    assert "build_index raises" in str(findings[0])


def test_v23_passes_on_distinct_ids() -> None:
    assert (
        cc.check_unique_ids(
            [
                _asset("term", {"id": "t.pay", "summary": "s", "body": "b"}, "one.yaml"),
                _asset("term", {"id": "t.bonus", "summary": "s", "body": "b"}, "two.yaml"),
            ]
        )
        == []
    )


def test_v23_does_not_fire_on_assets_with_no_id() -> None:
    """Inline columns carry no `id` in YAML -- the loader derives it -- so a rule that grouped on
    the empty string would report every table's columns as one giant duplicate."""
    assert cc.check_unique_ids([_asset("column", {"physical_name": "zip_code"}, f"{i}.yaml") for i in range(3)]) == []
