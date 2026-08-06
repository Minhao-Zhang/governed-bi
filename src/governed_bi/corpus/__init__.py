"""Memory layer: eight asset types, validation, and on-disk store (ADR 0005).

May import ``ports``, ``register``, ``measure``. No import side effects.

Invariants (ADR 0005 §0): I1 — ``summary`` is the only indexed field (≤250 chars,
validation error on over-length, never truncate). I2 — ``body`` is optional and
unbounded (what the system uses on hit). Per-type policy lives in
``register/assets.py`` only.
"""


from __future__ import annotations

__all__: list[str] = []
