"""Server: the serve harness (``LangGraph`` + middleware).

The online governed agent that *consumes* the corpus to answer, **fail-closed
and auditable**. A deterministic LangGraph DAG with conditional routing — never
autonomous ReAct (design-spine #2: the question can be wide, the SQL must be
narrow).

Middleware: ``before_model`` injects context (working memory, RLS scope,
semantic-layer router); ``wrap_tool_call`` runs the guardrails and is where
fail-closed lives.

Modules map to the flow (``docs/server.md``):

- ``routing``    — query understanding, term binding, intent route.
- ``cache``      — SQL semantic-cache fast path.
- ``flow``       — the deterministic DAG wiring the stages together.
- ``middleware`` — before_model / wrap_tool_call hooks.
- ``answer``     — answer assembly + reliability stamp.

Retrieval, join planning, guardrails, and gateway execution live in the
``retrieval``, ``graph``, and ``gateway`` packages (shared substrate).
"""
