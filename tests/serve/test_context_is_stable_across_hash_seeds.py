"""The rendered context must not depend on ``PYTHONHASHSEED``.

``resolve`` and ``connect`` build ``retrieved.pulled_in`` by walking sets, so its order follows the
process's string-hash seed, and the renderer keeps rules in first-seen order; so the renderer sorts
``pulled_in`` by asset id. One process cannot see a seed dependence, so this test renders the same
retrieval in two subprocesses under two seeds.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap

# Eight pulled-in tables, each with its own rule, built from a *set* inside the child so the
# dictionary's insertion order follows that process's seed exactly as `resolve` does.
_CHILD = textwrap.dedent(
    """
    from governed_bi.serve.context import render_context

    ids = {f"s.t_{name}" for name in "abcdefgh"}
    assets = {
        "s": {"id": "s", "asset_type": "schema", "name": "s"},
        **{
            aid: {
                "id": aid,
                "asset_type": "table",
                "schema": "s",
                "physical_name": aid.split(".", 1)[1],
                "rules": [f"rule for {aid}"],
            }
            for aid in ids
        },
    }
    retrieved = {
        "selected": {},
        "by_type": {"schema": ["s"]},
        "pulled_in": {aid: "resolve" for aid in ids},
    }
    block, digest = render_context(retrieved=retrieved, assets_by_id=assets, schemas=["s"])
    print(digest)
    print(block)
    """
)


def _render_under(seed: int) -> str:
    env = {**os.environ, "PYTHONHASHSEED": str(seed)}
    done = subprocess.run(
        [sys.executable, "-c", _CHILD], env=env, capture_output=True, text=True, check=True
    )
    return done.stdout


def test_pulled_in_tables_with_rules_render_the_same_block_under_two_seeds() -> None:
    first, second = _render_under(1), _render_under(2)

    assert first.splitlines()[0] == second.splitlines()[0], (
        "the context hash depends on PYTHONHASHSEED:\n--- seed 1\n"
        f"{first}\n--- seed 2\n{second}"
    )
    # The block is the thing a model reads; the hash alone could match on an empty one.
    assert first == second
    assert first.count("rule for") == 8


def test_rules_of_pulled_in_tables_come_out_in_asset_id_order() -> None:
    rules = [line for line in _render_under(1).splitlines() if line.startswith("- rule for")]

    assert rules == [f"- rule for s.t_{name}" for name in "abcdefgh"]
