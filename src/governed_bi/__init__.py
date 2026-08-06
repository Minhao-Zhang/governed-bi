"""governed-bi: a governed agentic text-to-SQL engine.

Importing this package has no side effects. Layout and import direction
(enforced by ``tools/check_imports.py``)::

    ports.py        Protocols only. stdlib.
    register/       Declared tables. May import ports.
    measure/        Quantities, populations, statistics.
    corpus/         Assets, identity, filtered view, index.
    retrieve/       Query-time retrieval.
    govern/         ADR 0006 layer stack and executors.
    datasource/     Connectors.
    model/          Chat and embedding clients.
    serve/          LangGraph graph.
    record/         Write-only sink.
    eval/           Experiment driver.
    api/            Read routes.

See ADR 0005 §6 and ADR 0006.
"""


from __future__ import annotations

__all__: list[str] = []
