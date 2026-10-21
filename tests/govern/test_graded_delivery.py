"""Graded delivery left with the cost layer. Nothing is eligible."""

from __future__ import annotations

from governed_bi.govern.check import graded_delivery_eligible
from governed_bi.govern.layers import Layer, allow


def test_six_layers_remain_and_graded_delivery_is_never_eligible() -> None:
    assert len(Layer) == 6
    assert [layer.name for layer in Layer] == [
        "PARSE",
        "NO_WRITE",
        "FUNCTIONS",
        "BINDING",
        "COLUMNS",
        "TABLES",
    ]
    result = allow(evaluated=list(Layer), bound={})
    assert graded_delivery_eligible(result) is False
