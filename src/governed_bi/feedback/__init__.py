"""The return path: failures in, reviewable corpus changes out (ADR 0015).

May import ``paths``, ``ports``, ``register``, ``measure``, ``corpus``. **May not import
``serve``, ``govern``, ``eval`` or ``api``** — nothing here runs during a turn, and a store only
an HTTP handler can reach is a store no script can audit.

It sits immediately after ``corpus`` in ``tools/check_imports.py::LAYERS`` for one reason worth
stating: a patch is judged by ``corpus/validate.py::problems_with`` and
``corpus/parse.py::from_mapping``, **the same validators the loader uses**, not by a second copy
of the rules. A patch this layer accepts is a file the engine can load.

**No path here writes to ``GOVERNED_BI_CORPUS_DIR``.** The corpus is versioned outside this
repository and is not rebuildable from it; this layer produces candidates and a human commits
them. The one write primitive that touches corpus text lives in ``corpus/patch.py`` and is aimed
at a staging tree.

The grant seam is composed in ``api/``, where the session lives, so nothing here imports
``api/visibility.py``.
"""


from __future__ import annotations

__all__: list[str] = []
