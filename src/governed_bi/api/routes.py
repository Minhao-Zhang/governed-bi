"""Mountable ASGI app for the LangGraph Server custom routes (``langgraph.json``).

The read + edit routes (capabilities, health, schema, ER graph, knowledge graph,
corpus assets, skills, ``/corpus/edit``, and the non-streaming ``/chat``
fallback) as one FastAPI app, so the LangGraph server serves them next to its own
``/threads`` and ``/runs`` and the frontend has a single base URL.

Built at import (a module-level ``app``) because ``langgraph.json`` references it
by path (``http.app: ./src/governed_bi/api/routes.py:app``). Importing this module
therefore assembles the serve stack; that is intended for the server process. The
standalone :func:`governed_bi.api.create_app` factory is still the entry point for
the offline REST profile.
"""

from __future__ import annotations

# Absolute import: the LangGraph server loads this module by file path (no parent
# package), so a relative ``from .app`` would fail. The package is installed
# (langgraph.json ``dependencies: ["."]``), so the absolute import resolves.
from governed_bi.api.app import create_app

app = create_app()
"""The ASGI app the LangGraph server mounts (see module docstring)."""
