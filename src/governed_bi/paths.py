"""Where the repository is, for the two entry points that need to find ``tools/``.

**One definition, because there are two callers and they must agree.** ``python -m
governed_bi.serve`` and ``api/graph_app.py`` both locate ``tools/credentials.py`` relative to
the repository root, and both had their own ``_ROOT = Path(__file__).resolve().parent.parent
.parent.parent`` — four ``parent``s counted by hand, in two files, meaning one thing.
``tools/check_one_implementation.py`` refused that, correctly: the failure mode is not that the
count is hard to read but that one of the two gets edited when a file moves and the other keeps
resolving to a directory that no longer holds ``tools/``.

**Resolved at import time, and that is load-bearing rather than incidental.** ``Path.resolve()``
calls ``os.path.realpath`` → ``os.getcwd``, and ``getcwd`` is the one blocking call
`blockbuster` leaves armed under ``langgraph dev``. Doing it inside a function meant the first
request that reached the graph factory from an **async** handler died with
``BlockingError: Blocking call to os.getcwd``, surfaced as a 500 from
``/assistants/{id}/schemas``. A module-level constant runs while the module is imported, which
is before the event loop exists.

Nothing in ``src/`` may *read* ``.env`` — a library that decides its own configuration behind
its caller's back is the layering ``tools/check_imports.py`` exists to keep. This module only
says where the repository is; the two entry points do the reading.
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
