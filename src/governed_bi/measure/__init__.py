"""Things that compute a quantity. May import ``ports`` and ``register``.

The split from :mod:`governed_bi.register` is not "data vs code" — the register
holds predicates too. It is **declaration vs derivation**: a register says what a
field is and why absence there is an error; this layer turns observations into
numbers, and its whole job is to reach the number *or say why it could not*.

Which is why :class:`~governed_bi.register.quantity.Measured` is one layer down and
not here. Every function in this package returns one, and none of them may invent a
value for a quantity it failed to obtain. ``0`` is a measurement; the four v1
incidents this layer exists to prevent were all a zero standing in for an absence:

* ``sum_token_usage([])`` returned a dict of zeros, which priced a whole run as
  free — and the dict is truthy, so the ``if not token_sum`` guard beside it never
  fired.
* A usage payload of all zeros from a provider that reported nothing was priced at
  ``0.0``, a *measured* zero that then passed every ``is None`` check downstream
  and pulled an arm's cost total down as an observation.
* A rate over zero trials published as ``0``, when what was observed bounds the
  rate rather than measuring it.
* A stale price tuple that overstated a measured run nine-fold, beside two ladders
  that produced no dollar figure at all and reported success anyway.

Nothing here formats a number. :meth:`Measured.render` is the only permitted
formatting site in ``src/`` and ``tools/check_measurement_locality.py`` fails the
build on a rounding or format spec anywhere else — v1's rounding helpers turned an
unmeasured quantity into ``0.0`` on the way to a report, so the value was honest
right up to the last function that touched it.
"""

from __future__ import annotations

__all__: list[str] = []
