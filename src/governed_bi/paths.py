"""Where the repository is, for the two entry points that need to find ``tools/``.

One definition, because ``python -m governed_bi.serve`` and ``api/graph_app.py`` must agree
when a file moves (``tools/check_one_implementation.py`` enforces it).

**Resolved at import time, not in a function.** ``Path.resolve()`` calls ``os.getcwd``, the
blocking call `blockbuster` leaves armed under ``langgraph dev``; resolving inside an async
handler raised ``BlockingError: Blocking call to os.getcwd``. Module level runs before the
event loop exists.

Nothing in ``src/`` may *read* ``.env`` (``tools/check_imports.py``); this module only says
where the repository is, and the entry points do the reading.
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["REPO_ROOT", "TOOLS_DIR"]

#: The repository root: the directory holding ``pyproject.toml``, ``tools/`` and ``corpora/``.
#: ``src/governed_bi/paths.py`` → ``src/governed_bi`` → ``src`` → root.
REPO_ROOT = Path(__file__).resolve().parent.parent.parent

#: Where ``credentials.py`` lives. Entry points put this on ``sys.path`` and import from it,
#: rather than ``src/`` importing it — ``tools/`` is not part of the package.
TOOLS_DIR = REPO_ROOT / "tools"
