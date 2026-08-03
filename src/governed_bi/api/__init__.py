"""The HTTP surface (ADR 0007). Mounted by ``langgraph.json``: ``graphs.serve`` points at
``graph_app.make_graph`` and ``http.app`` at ``routes.app``.

Nothing here is imported by the library. ``tools/check_imports.py`` has declared this layer
since before v1 was deleted and it stayed empty through the rewrite; the dependency direction
is one-way on purpose, so `serve/` never learns that a server exists.
"""
