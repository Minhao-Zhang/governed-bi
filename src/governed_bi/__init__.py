"""governed-bi: a governed agentic text-to-SQL engine.

Importing this package has no side effects. Imports run downward only, in the order
``tools/check_imports.py`` declares::

    ports -> register -> measure -> corpus -> retrieve -> govern
          -> datasource -> model -> serve -> record -> eval -> api

``record`` is declared there but no such package exists; ``verify`` exists and is not
declared, so nothing constrains its imports. See ADR 0005 §6 and ADR 0006.
"""


from __future__ import annotations

__all__: list[str] = []
