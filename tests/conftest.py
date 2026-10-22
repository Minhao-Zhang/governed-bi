"""Suite setup loads ``.env`` once, at process entry.

A library must not read ``.env``. Bridging per fixture cannot reach a client that
reads the process environment at import. An existing environment value wins, and
nothing loaded here is printed.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))


def _load_dotenv_into_environ() -> int:
    """Copy unset secrets from ``.env``. Settings in the file are not applied."""
    from governed_bi.credentials import load_into_environ

    return load_into_environ(refuse=False)


def pytest_runtest_teardown(item, nextitem) -> None:  # noqa: ARG001
    from governed_bi.registry.settings import reset_settings

    reset_settings()


#: Run at import, which pytest does before collecting any test — so a module-scoped fixture
#: that reads a credential at collection time already sees it.
_FILLED = _load_dotenv_into_environ()
