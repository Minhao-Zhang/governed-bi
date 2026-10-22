"""A missing connector raises ``GovernanceUsageError`` instead of a governance refusal.

``refuse("r_not_a_read", "no connector configured")`` blamed the statement for a wiring fault
and recorded ``outcome: refused``.
"""

from __future__ import annotations

import ast
import pathlib
from typing import Any

import pytest

from governed_bi.corpus.analyst import analyst_corpus_from_keys
from governed_bi.govern.bounds import ToolBounds
from governed_bi.govern.check import GovernanceUsageError
from governed_bi.govern.layers import RULES
from governed_bi.govern.policy import GovernancePolicy
from governed_bi.serve import fetch

_BOUNDS = ToolBounds(
    retrieved_tables=frozenset({"sales.customers"}),
    readable_assets=frozenset({"sales.customers.id"}),
)
_CORPUS = analyst_corpus_from_keys(allowed=["sales.customers.id"])
_POLICY = GovernancePolicy(guard_rules_enabled={})


def test_run_query_without_a_connector_raises_rather_than_refusing() -> None:
    with pytest.raises(GovernanceUsageError) as caught:
        fetch.run_query(
            "SELECT id FROM sales.customers",
            bounds=_BOUNDS,
            corpus=_CORPUS,
            connector=None,
            policy=_POLICY,
        )
    assert "connector" in str(caught.value)


def test_sample_rows_without_a_connector_raises_rather_than_refusing() -> None:
    assets: dict[str, Any] = {
        "sales.customers.id": {
            "asset_type": "column",
            "id": "sales.customers.id",
            "physical_name": "id",
            "parent_table": "sales.customers",
        }
    }
    with pytest.raises(GovernanceUsageError) as caught:
        fetch.sample_rows(
            "sales.customers.id",
            limit=5,
            bounds=_BOUNDS,
            assets=assets,
            connector=None,
            corpus=_CORPUS,
            policy=_POLICY,
        )
    assert "connector" in str(caught.value)


def test_no_tool_body_manufactures_a_layer_result_for_its_own_wiring() -> None:
    """``fetch.py`` must not call ``refuse``; the walk is by AST so a comment cannot trip it."""
    tree = ast.parse(pathlib.Path(fetch.__file__).read_text(encoding="utf-8"))

    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "refuse" not in called, (
        "fetch.py constructs a governance result. A refusal built in serve/ attributes our "
        "own misconfiguration to the model's statement; raise GovernanceUsageError instead."
    )

    imported = {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    assert "refuse" not in imported, "fetch.py imports `refuse`, so it is one edit from calling it"

    # Anti-vacuity, both halves: the walk really does see this module's calls, and the rule id
    # the removed result used is still spelled the way this test assumes.
    assert "prepare" in called, "the AST walk found no prepare() call; it is not reading fetch.py"
    assert "r_not_a_read" in RULES
