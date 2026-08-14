"""governed-bi: a governed agentic text-to-SQL engine.

Importing this package has no side effects. Imports run downward only, in the order
``tools/check_imports.py`` declares::

    paths -> credentials -> ports -> register -> measure -> corpus -> retrieve -> govern
          -> datasource -> model -> serve -> eval -> api

That list is the whole of what is here: the gate fails when a package under this one is
missing from it, or declared in it and absent from disk. See ADR 0005 §6 and ADR 0006.
"""


from __future__ import annotations

__all__: list[str] = []
