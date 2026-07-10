"""HTTP API interface (FastAPI) over the governed serve flow + corpus views.

Optional: requires the ``api`` extra (``uv sync --extra api``). The app is built
by :func:`create_app` (a factory), so importing this package has no side effects
and the stack is assembled only when the factory runs. Serve it with
``uvicorn --factory governed_bi.api:create_app``. See ``governed_bi.api.app`` for
the endpoints and ``governed_bi.api.stack`` for how a deployment is assembled from
configuration.
"""

from __future__ import annotations

from .app import create_app
from .stack import ServeStack, build_stack

__all__ = ["create_app", "ServeStack", "build_stack"]
