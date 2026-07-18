"""The ``schema`` field (a directory + SQL namespace) must reject traversal (S4).

``/corpus/edit`` derives the write directory from ``asset.schema``; ``is_valid_id``
only guards the asset id. A ``schema`` like ``../../..`` previously escaped the
corpus root. It is now validated at parse and re-checked in ``write_corpus``.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from governed_bi.corpus import parse_asset
from governed_bi.corpus.serialize import write_corpus


def _table(schema: str) -> dict:
    return {
        "asset_type": "table",
        "id": "tbl_beer_factory_x",
        "schema": schema,
        "physical_name": "x",
    }


@pytest.mark.parametrize("schema", ["../../etc", "..", "a/b", "a\\b", "with space"])
def test_parse_rejects_unsafe_schema(schema: str):
    with pytest.raises(ValidationError):
        parse_asset(_table(schema))


def test_parse_accepts_plain_identifier():
    asset = parse_asset(_table("beer_factory"))
    assert asset.schema == "beer_factory"


@pytest.mark.parametrize("schema", ["../evil", "a/b", "..", "/abs"])
def test_write_corpus_refuses_unsafe_schema(tmp_path, schema: str):
    with pytest.raises(ValueError):
        write_corpus(tmp_path, schema, [])
