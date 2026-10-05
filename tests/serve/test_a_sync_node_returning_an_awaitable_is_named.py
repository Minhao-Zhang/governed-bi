"""A sync node that returns an awaitable crashes with the ``TypeError`` that names the mistake.

``inspect.isawaitable`` admits more than coroutines, and only a coroutine has ``close``. With a
bare ``update.close()`` a Future-like return raised ``AttributeError`` first, so the crash was
stamped under the wrong error type.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from governed_bi.serve.wrap import wrap_node


class _Awaitable:
    """Awaitable and not a coroutine: no ``close``."""

    def __await__(self) -> Any:
        yield
        return {}


async def _coroutine() -> dict:
    return {}


@pytest.mark.parametrize("returned", [_Awaitable, _coroutine], ids=["awaitable", "coroutine"])
def test_the_crash_is_stamped_as_a_type_error(returned: Any) -> None:
    node = wrap_node("guard", lambda _state: returned(), stream=False)
    update = asyncio.run(node({"turn_id": "t1"}))
    assert update["path_kind"] == "crashed"
    assert update["failure"]["error_type"] == "TypeError"
