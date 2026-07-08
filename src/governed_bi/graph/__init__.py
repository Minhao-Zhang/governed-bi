"""Graph projection + join planning.

The graph is a **derived, rebuildable projection** of the YAML corpus (D9),
never authored directly. BIRD uses an in-memory ``networkx`` graph for
Steiner-tree join planning; Neo4j is the optional enterprise-scale projection.

- ``projection`` — build the property graph from parsed assets.
- ``planner`` — Steiner-tree join planning over the inferred FK graph.
"""
