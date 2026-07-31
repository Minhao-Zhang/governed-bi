"""HTTP API interface (FastAPI) over the governed serve flow + corpus views.

No extra required: FastAPI is a core dependency, so a plain ``uv sync`` is enough
(there is no ``api`` extra and never was). The app is built
by :func:`create_app` (a factory), so importing this package has no side effects
and the stack is assembled only when the factory runs. Serve it with
``uvicorn --factory governed_bi.api:create_app``. See ``governed_bi.api.app`` for
the endpoints and ``governed_bi.api.stack`` for how a deployment is assembled from
configuration.
"""

from __future__ import annotations

from .app import create_app
from .stack import ServeStack, build_stack, get_default_stack

__all__ = ["create_app", "ServeStack", "build_stack", "get_default_stack"]
