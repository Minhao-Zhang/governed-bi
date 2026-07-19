"""Guardrail must block file/network/system SQL functions (audit finding S1).

A column-less ``SELECT fn(...)`` references no table or column, so it slipped past
L3/L4/L5; the read-only connection does not stop read-functions like
``pg_read_file``. L2 now carries a function denylist. These lock that in.
"""

from __future__ import annotations

import pytest

from governed_bi.gateway.guardrails import GuardrailLayer, check


def _check(sql: str):
    return check(
        sql,
        allowed_columns=set(),
        hard_block_suspect=True,
        allowed_tables=frozenset(),
        dialect="postgres",
    )


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT pg_read_file('/etc/passwd')",
        "SELECT pg_ls_dir('/')",
        "SELECT lo_import('/etc/passwd')",
        "SELECT dblink('host=evil', 'SELECT 1')",
        "SELECT version()",
        "SELECT current_setting('data_directory')",
        "SELECT pg_sleep(10)",
    ],
)
def test_dangerous_function_is_hard_blocked(sql: str):
    verdict = _check(sql)
    assert not verdict.passed
    assert verdict.failed_layer is GuardrailLayer.policy_blacklist


@pytest.mark.parametrize(
    "sql",
    [
        # The whole Postgres XML-export family is the same exfiltration primitive:
        # the target table/schema/database is a string-literal arg, so the call is
        # an exp.Anonymous with no Table/Column nodes that L3/L4/L5 could inspect.
        # L2 must block every one of them by name, not just query_to_xml.
        """SELECT table_to_xml('beer_factory."transaction"', true, false, '')""",
        "SELECT table_to_xmlschema('beer_factory.customers', true, false, '')",
        "SELECT table_to_xml_and_xmlschema('beer_factory.customers', true, false, '')",
        "SELECT query_to_xml('SELECT * FROM beer_factory.customers', true, false, '')",
        "SELECT query_to_xmlschema('SELECT * FROM beer_factory.customers', true, false, '')",
        "SELECT query_to_xml_and_xmlschema('SELECT 1', true, false, '')",
        "SELECT cursor_to_xml('c', 0, true, false, '')",
        "SELECT cursor_to_xmlschema('c', true, false, '')",
        "SELECT schema_to_xml('beer_factory', true, false, '')",
        "SELECT schema_to_xmlschema('beer_factory', true, false, '')",
        "SELECT schema_to_xml_and_xmlschema('beer_factory', true, false, '')",
        "SELECT database_to_xml(true, false, '')",
        "SELECT database_to_xmlschema(true, false, '')",
        "SELECT database_to_xml_and_xmlschema(true, false, '')",
    ],
)
def test_xml_export_family_is_hard_blocked(sql: str):
    # Use a permissive allowlist to prove the block is L2 (the function denylist),
    # not an incidental L3/L4 miss: these calls reference no column or table anyway.
    verdict = check(
        sql,
        allowed_columns={
            "beer_factory.transaction.PurchasePrice",
            "beer_factory.customers.First",
        },
        hard_block_suspect=True,
        allowed_tables=frozenset({"beer_factory.transaction", "beer_factory.customers"}),
        dialect="postgres",
        default_schema="beer_factory",
    )
    assert not verdict.passed
    assert verdict.failed_layer is GuardrailLayer.policy_blacklist


def test_normal_aggregate_still_passes():
    verdict = check(
        'SELECT SUM("PurchasePrice") AS revenue FROM "beer_factory"."transaction"',
        allowed_columns={"beer_factory.transaction.PurchasePrice"},
        hard_block_suspect=True,
        allowed_tables=frozenset({"beer_factory.transaction"}),
        dialect="postgres",
        default_schema="beer_factory",
    )
    assert verdict.passed, verdict.reason
