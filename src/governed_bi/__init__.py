"""governed-bi: a governed agentic text-to-SQL engine.

**Importing this package has no side effects, deliberately.**

v1's ``__init__`` auto-loaded a repo-root ``.env``. The consequence was that a
developer keeping a real ``OPENAI_API_KEY`` there leaked it into every test
process, which flipped paths meant to be offline onto a live model:
non-deterministic, order-dependent, and billed. The test suite then had to strip
credentials session-wide to undo an effect the package created on import.

So: no dotenv loading, no logging configuration, no settings resolution, no
registry population here. Every one of those is an explicit call by whoever owns
the process — which is also the only way a short-lived process can be sure it got
the configuration it thinks it did.

Layout, and what may import what:

.. code-block:: text

    ports.py        Protocols only. stdlib. Imported by everything, imports nothing.
    register/       Declared tables. stdlib. May import ports.
    measure/        Quantities, populations, statistics. May import register.
    corpus/         Assets, identity, the filtered view, the index derived from it.
    retrieve/       Query-time retrieval. Deterministic except extraction.
    govern/         ADR 0006: the layer stack and the four executors.
    datasource/     The two connectors.
    model/          Chat and embedding clients.
    serve/          The LangGraph graph.
    record/         The write-only sink.
    eval/           The experiment driver.
    api/            Read routes.

Enforced by ``tools/check_imports.py``, not by convention: v1's rule that
"callers are documented as passing ``for_analyst()``" was unenforced and was
breached by the pooled driver, which put excluded PII column names into the
routing index.

See ``docs/adr/0005-v2-memory-layer-and-faceted-retrieval.md`` §6,
``docs/adr/0006-execution-time-governance.md``, and
``docs/lessons-from-v1.md``.
"""

from __future__ import annotations

__all__: list[str] = []
