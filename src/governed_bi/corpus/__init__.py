"""The memory layer: the eight asset types, their validation, and where they live.

Layer 4. May import ``ports``, ``register`` and ``measure``; nothing later
(``tools/check_imports.py``). Importing this package has no side effects.

Five modules, split by concern rather than by size:

.. code-block:: text

    identity.py   ids and paths -- the two places a name becomes a filesystem path
    schema.py     the eight dataclasses, plus raw-mapping <-> asset conversion
    validate.py   the rules: summary bounds, identifier present, tag rule satisfied
    store.py      YAML on disk; per-item error isolation, never a raise for a bad item
    hash.py       corpus_content_hash -- the treatment identity, no "unknown" sentinel

The two invariants everything here turns on (ADR 0005 §0):

* **I1 -- ``summary`` is the only indexed field**, bounded at 250 characters. The
  index is a shared scoring space, so one oversized entry changes what every other
  entry's score means. Over-length is a validation error, never a truncation.
* **I2 -- ``body`` is what the system uses on hit.** Unbounded and **optional**. The
  seed produces assets with no body at all, and that is what makes ADR 0005's
  "steps 6-9 are measurable with no model" true.

Neither this package nor anything in it decides *policy*. Per-type policy --
which field must appear in ``summary``, how the index tags an asset's schema, the
retrieval budget -- is declared once in ``register/assets.py`` and read from
there. Restating any of it here is the defect
that left v1's ``NegativeExampleAsset`` generated, embedded, indexed and
structurally unreachable, because a budget lookup in a second table defaulted to
zero.
"""

from __future__ import annotations

__all__: list[str] = []
