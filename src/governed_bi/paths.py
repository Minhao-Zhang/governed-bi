"""Where the repository is.

One definition, because ``python -m governed_bi.serve`` and ``api/graph_app.py`` must agree
when a file moves (``tools/check_one_implementation.py`` enforces it).

**Resolved at import time, not in a function.** ``Path.resolve()`` calls ``os.getcwd``, the
blocking call `blockbuster` leaves armed under ``langgraph dev``; resolving inside an async
handler raised ``BlockingError: Blocking call to os.getcwd``. Module level runs before the
event loop exists.

Only the entry points may *read* ``.env``, and ``credentials`` is where the reading happens;
this module only says where the repository is.
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["REPO_ROOT"]

#: The repository root: the directory holding ``pyproject.toml``.
#: ``src/governed_bi/paths.py`` → ``src/governed_bi`` → ``src`` → root.
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
