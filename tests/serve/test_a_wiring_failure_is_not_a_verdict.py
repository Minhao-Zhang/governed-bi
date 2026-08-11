"""An unconfigured connector is our mistake, not the statement's.

``check.py`` states the doctrine: *"a security parameter was not wired up … never a statement's
fault."* ``fetch.py`` applied it to a missing ``corpus`` (G1, raising ``GovernanceUsageError``) and
broke it for a missing ``connector`` four lines away, manufacturing
``refuse("r_not_a_read", "no connector configured")`` — a rule ``layers.py`` assigns to
``Layer.NO_WRITE``, which is to say *"the model proposed a write"*. The ledger row an
unconfigured connector produced was therefore indistinguishable from a real governance refusal,
and the turn recorded ``outcome: refused`` for a deployment fault. 2026-08-10 audit (C2).

The fabricated row was also load-bearing in the suite, which is the part worth remembering: three
tests in ``test_agent_core_partial_ledger.py`` and one in ``test_agent_tools_hitl.py`` asserted on
"a governed statement" that was this refusal, with no connector anywhere and nothing having reached
``check()``. One of them said so in its own failure message. They pass a stub connector now.
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
    licensed=frozenset({"sales.customers"}),
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


def test_no_tool_body_manufactures_a_layer_verdict_for_its_own_wiring() -> None:
    """The general form, so the next wiring check cannot reintroduce the pattern.

    ``fetch.py`` may not call ``refuse``. Every legitimate verdict in this module comes back from
    ``prepare``/``check``, which own :data:`RULES`; a rule id constructed here is ``serve/``
    deciding which layer gets blamed, and ``layers.py`` says the layer comes from ``RULES`` and
    never from a caller. Asserted structurally because the alternative is enumerating every
    wiring hole, and it was a *second* hole four lines from a correct one that made this a bug.

    By AST rather than by substring: the first version of this test searched the text for
    ``"refuse("`` and failed on the comment in ``fetch.py`` that explains what was removed. A
    prose-sensitive assertion about code is the fragility this audit spent its time on.
    """
    tree = ast.parse(pathlib.Path(fetch.__file__).read_text(encoding="utf-8"))

    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "refuse" not in called, (
        "fetch.py constructs a governance verdict. A refusal built in serve/ attributes our "
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
    # the removed verdict used is still spelled the way this test assumes.
    assert "prepare" in called, "the AST walk found no prepare() call; it is not reading fetch.py"
    assert "r_not_a_read" in RULES
