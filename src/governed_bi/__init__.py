"""governed-bi: a governed agentic text-to-SQL engine.

Importing this package has no side effects. Imports run downward only, in the order
``tools/check_imports.py`` declares::

    paths -> credentials -> ports -> register -> measure -> corpus -> retrieve
          -> govern -> datasource -> model -> serve -> conform -> eval -> api

The gate fails when a package here is missing from that list, or listed and absent.
"""


from __future__ import annotations

__all__: list[str] = []
