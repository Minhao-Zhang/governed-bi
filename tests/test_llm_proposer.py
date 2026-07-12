"""Tests for the LLM curator proposer (curator.llm_proposer.LlmProposer).

Offline: the ChatClient is a scripted StaticChatClient returning canned JSON.
"""

from __future__ import annotations

import pytest

from governed_bi.corpus.schemas import (
    Column,
    ColumnRole,
    LogicalType,
    ProvenanceStatus,
    ReliabilityStatus,
    TableAsset,
)
from governed_bi.curator import LlmProposer, Proposer, review
from governed_bi.llm import StaticChatClient

ORDERS_JSON = """
{
  "table_description": "Customer orders placed at the shop.",
  "grain": "one row per order",
  "columns": {
    "OrderID": {"description": "Unique order identifier.", "reliability": "ok"},
    "amount": {"description": "Order total in dollars.", "reliability": "ok"},
    "note": {"description": "Free-text note.", "reliability": "suspect",
             "note": "DO NOT USE: free text, unreliable for analysis."}
  }
}
"""


def _facts_column(name, logical, *, is_unique, samples=None):
    return Column(
        physical_name=name,
        physical_type=logical.value.upper(),
        logical_type=logical,
        nullable=True,
        is_unique=is_unique,
        sample_values=samples or [],
    )


def _orders_table():
    return TableAsset(
        id="tbl_demo_orders",
        schema="demo",
        physical_name="orders",
        columns=[
            _facts_column("OrderID", LogicalType.integer, is_unique=True, samples=[1, 2, 3]),
            _facts_column("amount", LogicalType.decimal, is_unique=False, samples=[9.5]),
            _facts_column("note", LogicalType.string, is_unique=False),
        ],
    )


def test_satisfies_protocol():
    assert isinstance(LlmProposer(StaticChatClient(ORDERS_JSON)), Proposer)


def test_authors_descriptions_and_grain():
    [table] = LlmProposer(StaticChatClient(ORDERS_JSON)).propose([_orders_table()])
    assert table.description == "Customer orders placed at the shop."
    assert table.grain == "one row per order"
    by_name = {c.physical_name: c for c in table.columns}
    assert by_name["OrderID"].description == "Unique order identifier."
    assert by_name["amount"].description == "Order total in dollars."


def test_flags_suspect_column_with_caveat():
    [table] = LlmProposer(StaticChatClient(ORDERS_JSON)).propose([_orders_table()])
    note = next(c for c in table.columns if c.physical_name == "note")
    assert note.reliability.status is ReliabilityStatus.suspect
    assert note.reliability.note.startswith("DO NOT USE")
    # non-suspect columns stay ok
    amount = next(c for c in table.columns if c.physical_name == "amount")
    assert amount.reliability.status is ReliabilityStatus.ok


def test_roles_come_from_the_base_and_facts_are_untouched():
    [table] = LlmProposer(StaticChatClient(ORDERS_JSON)).propose([_orders_table()])
    by_name = {c.physical_name: c for c in table.columns}
    # role is the base (heuristic) decision
    assert by_name["OrderID"].role is ColumnRole.primary_key
    assert by_name["amount"].role is ColumnRole.measure
    # Facts preserved verbatim
    assert by_name["OrderID"].physical_type == "INTEGER"
    assert by_name["OrderID"].sample_values == [1, 2, 3]
    assert by_name["amount"].is_unique is False


def test_prompt_carries_the_table_facts():
    chat = StaticChatClient(ORDERS_JSON)
    LlmProposer(chat).propose([_orders_table()])
    _system, user = chat.calls[0]
    assert "orders" in user
    assert "OrderID" in user
    assert "amount" in user


def test_malformed_response_degrades_to_base():
    [table] = LlmProposer(StaticChatClient("not json at all")).propose([_orders_table()])
    # No fabricated prose, but the base proposal (roles/provenance) survives.
    assert table.description is None
    assert all(c.description is None for c in table.columns)
    assert table.columns[0].role is ColumnRole.primary_key
    assert table.audit.provenance.status is ProvenanceStatus.proposed


@pytest.mark.parametrize(
    "columns_value",
    ['["OrderID"]', "null", '"OrderID"', "42"],
)
def test_valid_json_with_non_dict_columns_degrades_safely(columns_value):
    # A valid-JSON but hostile/malformed response whose "columns" is not an object
    # must not crash (fail-safe); table-level prose still applies, columns stay base.
    payload = '{"table_description": "Orders.", "columns": %s}' % columns_value
    [table] = LlmProposer(StaticChatClient(payload)).propose([_orders_table()])
    assert table.description == "Orders."  # table-level prose still applied
    assert all(c.description is None for c in table.columns)  # columns left as base
    assert table.columns[0].role is ColumnRole.primary_key  # roles intact


def test_output_passes_the_adversary_review():
    proposed = LlmProposer(StaticChatClient(ORDERS_JSON)).propose([_orders_table()])
    assert review(proposed) == []


def test_does_not_mutate_input():
    table = _orders_table()
    LlmProposer(StaticChatClient(ORDERS_JSON)).propose([table])
    assert table.description is None
    assert all(c.description is None for c in table.columns)
    assert all(c.reliability.status is ReliabilityStatus.ok for c in table.columns)


def test_model_name_stamped_into_provenance():
    [table] = LlmProposer(StaticChatClient(ORDERS_JSON), model_name="gpt-5.5").propose([_orders_table()])
    assert table.audit.provenance.model == "gpt-5.5"
