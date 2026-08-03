"""Parcel F's acceptance criterion: the fixtures its specifications name.

Split out of ``test_turn_contract.py`` when that file hit the hard cap of ADR 0005 §6, then
**kept after the cap moved to 1000**, because the specifications next door still have five
fixes to absorb and headroom is worth more than one file. Nothing here was edited on the way
across: the twenty-two assertion messages were byte-identical before and after the move,
which is the only available proof that a "pure move" was pure.

These fixtures are part of the acceptance criterion, not scaffolding around it. **Editing a
fixture here to make a contract test pass is editing the contract.** If a fixture looks
wrong, stop and say so.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

# ── the fixtures the specifications name ──────────────────────────────────────

#: ``src/governed_bi``. A behavioural fix that reimplements a declared comparison inline satisfies
#: the behaviour and leaves the declaration unreachable, so some assertions are structural.
SRC = Path(__file__).resolve().parent.parent.parent / "src" / "governed_bi"


def _base_turn(**overrides: Any) -> dict[str, Any]:
    """The state a served turn starts from — copied from ``test_pass_two_and_context.py:54`` rather
    than imported. The keys are not decoration: a thinner turn fails on absence, not the property."""
    payload: dict[str, Any] = {
        "question": "how many customers", "thread_id": "thread-f2", "turn_index": 1,
        "run_id": "run-f2", "turn_id": "turn-f2", "question_id": "q-f2",
        "db_id": "sales_a", "attempt_id": "attempt-f2", "n_re_served": 0,
        "corpus_content_hash": "corpus-hash", "prompt_set_hash": "prompt-hash",
        "knobs_resolved": {"route_top_n": 1, "candidate_depth": 50},
        "messages": [], "usage": [], "route_top_n": 1,
    }
    payload.update(overrides)
    return payload


#: One rule armed. The refuse path of the three terminal paths.
INJECTION_RULES = {"g_encoding": False, "g_length": False, "g_instruction_override": True,
                   "g_role_injection": False, "g_tool_forgery": False}


def _policy(*, rules: dict[str, bool] | None = None, attempt_cap: int | None = None) -> Any:
    """A policy with an explicit per-rule mapping. ``guard_rules_enabled`` ships ``UNSET``
    and ``guard()`` refuses to run without one, so "no policy" is not "no guard"."""
    from governed_bi.govern.policy import GovernancePolicy

    extra = {} if attempt_cap is None else {"run_query_attempt_cap": attempt_cap}
    return GovernancePolicy(guard_rules_enabled=rules or {}, **extra)


def _scripted_run_query(sql: str, *, calls: int = 1) -> Any:
    """Calls ``run_query`` ``calls`` times, then answers. Tool calls copied from ``eval/arms.py:89-101``;
    ``ScriptedChatModel`` replies by counting ``AIMessage``s, so ``calls`` calls means that many attempts."""
    from langchain_core.messages import AIMessage

    from governed_bi.serve.scripted_model import ScriptedChatModel

    call = {"name": "run_query", "args": {"sql": sql}, "type": "tool_call"}
    turns = [AIMessage(content="", tool_calls=[{**call, "id": f"rq-{i}"}]) for i in range(calls)]
    return ScriptedChatModel(responses=[*turns, AIMessage(content="answered from the tool")])


class _EchoConnector:
    """A connector double, used **only** where the property under test is the shape of the record rather
    than what a database did. The two specifications about governance-versus-execution take the real
    ``PostgresConnector`` and skip without a server; this is no substitute for that."""

    dialect = "postgres"

    def execute(self, sql: str, max_rows: int | None = None) -> Any:
        return (["count"], [(3,)], False)


def _texts(state: Any) -> list[str]:
    """Every message body in a final state, as text."""
    return [str(getattr(m, "content", m)) for m in (state.get("messages") or ())]


def _call_sites_in_src(name: str, *, defined_in: str) -> list[str]:
    """Files under ``src/`` that call ``name``, excluding the module declaring it."""
    import re

    pattern = re.compile(rf"\b{re.escape(name)}\s*\(")
    return [
        path.relative_to(SRC).as_posix()
        for path in sorted(SRC.rglob("*.py"))
        if path.relative_to(SRC).as_posix() != defined_in
        and pattern.search(path.read_text(encoding="utf-8"))
    ]


#: The two env vars / `.env` keys that may hold the DSN, in order of precedence.
DSN_KEYS = ("GOVERNED_BI_PG_DSN", "PG_RENAME_DECOY_DSN")


def _dsn() -> str:
    """The DSN, from the environment or `.env`. Never logged — it carries credentials."""
    import os

    for key in DSN_KEYS:
        if os.environ.get(key):
            return str(os.environ[key])
    env = Path(__file__).resolve().parent.parent.parent / ".env"
    for line in env.read_text(encoding="utf-8").splitlines() if env.exists() else ():
        key, _, value = line.partition("=")
        if key.strip() in DSN_KEYS and not line.strip().startswith("#"):
            return value.strip().strip("\"'")
    return ""


@pytest.fixture(scope="module")
def dsn() -> str:
    """Skip loudly when there is no server. The reason must reach the reader."""
    value = _dsn()
    if not value:
        pytest.skip(
            "no Postgres DSN: set GOVERNED_BI_PG_DSN or PG_RENAME_DECOY_DSN. With connector=None a "
            "governed refusal is a wiring failure, so these two tests are not optional but pending."
        )
    import psycopg

    try:
        with psycopg.connect(value, connect_timeout=5):
            pass
    except Exception as err:  # pragma: no cover - environment dependent
        pytest.skip(f"Postgres unreachable ({type(err).__name__}); see the DSN's host")
    return value


@dataclass(frozen=True)
class _Probe:
    """A live schema plus everything a real turn needs to reach it."""

    schema: str
    connector: Any
    index: Any
    assets_by_id: dict[str, Any]
    corpus: Any


def _entry(asset: Any, schema: str) -> Any:
    """One asset as an ``IndexEntry``, the mapping ``tests/serve/conftest.py:27`` is the only working
    example of. The tag comes from the declared ``TagRule`` table, not a second local rule."""
    from governed_bi.retrieve.index import IndexEntry, schema_tag_for

    return IndexEntry(
        id=asset.id,
        summary=asset.summary,
        asset_type=asset.asset_type,
        schema_tag=schema_tag_for(
            asset.asset_type, name=getattr(asset, "name", None), schema=getattr(asset, "schema", None),
            parent_schema=schema, base_table_schema=schema, binding_schema=schema,
            left_table_schema=schema,
        ),
    )


@pytest.fixture(scope="module")
def probe(dsn):
    """A throwaway schema, a real connector, and a **real seeded corpus** indexed over it.

    Its own schema, dropped afterwards, because the reachable server holds the real obfuscated BIRD
    data and a test that wrote into it would corrupt the eval corpus.

    The index is **lexical only** — no embedder is passed, so nothing below exercises the semantic
    channel; that needs a hand-written :class:`~governed_bi.ports.Embedder` (``ports.py:96``) and
    ``src/governed_bi/model/`` does not exist. Said here rather than left implicit.
    """
    import psycopg

    from governed_bi.corpus.analyst import for_analyst
    from governed_bi.corpus.seed import seed
    from governed_bi.datasource.postgres import PostgresConnector
    from governed_bi.retrieve.index import build_index

    name = "gbi_turn_contract_probe"
    with psycopg.connect(dsn, autocommit=True) as con:
        con.execute(f'DROP SCHEMA IF EXISTS "{name}" CASCADE')
        con.execute(f'CREATE SCHEMA "{name}"')
        con.execute(f'CREATE TABLE "{name}".customers (id integer PRIMARY KEY, email text)')
        # A table **no join reaches**, added 2026-08-03 after execution falsified the
        # fixture note below. `customers` and `orders` cannot serve as the unlicensed table:
        # the seeded corpus mints `join_..._orders_customers` whose summary reads "orders
        # joins customers on cid", so the question "customers" hits the *join* lexically and
        # `resolve`'s closure licenses both endpoints -- which ADR 0006 §8 requires
        # (`licensed` includes every table pulled in by resolve). No change to `serve/` could
        # have made the old fixture pass without breaking join licensing.
        con.execute(f'CREATE TABLE "{name}".audit_log (id integer PRIMARY KEY, note text)')
        con.execute(f'CREATE TABLE "{name}".orders (id integer PRIMARY KEY, cid integer '
                    f'REFERENCES "{name}".customers(id), amount numeric)')
        con.execute(f"INSERT INTO \"{name}\".customers VALUES (1,'a@x'),(2,'b@x'),(3,'c@x')")

    connector = PostgresConnector(dsn)
    try:
        assets, problems = seed(connector.introspect(name), schema=name)
        assert assets and not problems, problems
        yield _Probe(
            schema=name,
            connector=connector,
            index=build_index([_entry(a, name) for a in assets]),
            assets_by_id={a.id: a for a in assets},
            corpus=for_analyst(assets),
        )
    finally:
        connector.close()
        with psycopg.connect(dsn, autocommit=True) as con:
            con.execute(f'DROP SCHEMA IF EXISTS "{name}" CASCADE')
