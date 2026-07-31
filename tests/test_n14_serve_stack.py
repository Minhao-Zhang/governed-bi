"""M4 N14: default stack singleton + usage from call return values."""

from __future__ import annotations

from governed_bi.api.stack import (
    _reset_default_stack_for_tests,
    get_default_stack,
)
from governed_bi.retrieval.schema_router import SchemaPick


def test_get_default_stack_returns_same_object():
    _reset_default_stack_for_tests()
    a = get_default_stack()
    b = get_default_stack()
    assert a is b


def test_schema_pick_carries_usage_from_return_value():
    class _Chat:
        def complete_with_usage(self, system, user):
            return "beer_factory", {"input_tokens": 3, "output_tokens": 1}

    from governed_bi.corpus import Corpus
    from governed_bi.retrieval.schema_router import pick_schema

    # Need a minimal corpus? pick_schema with 2 candidates will call chat.
    # With empty corpus summaries still work.
    decision = pick_schema(
        Corpus(),
        "how many beers?",
        ["beer_factory", "other"],
        chat=_Chat(),
    )
    assert decision.schema == "beer_factory"
    assert decision.usage_metadata == {"input_tokens": 3, "output_tokens": 1}


def test_schema_pick_namedtuple_default_usage_is_none():
    assert SchemaPick("x").usage_metadata is None
