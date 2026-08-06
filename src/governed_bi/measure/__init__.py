"""Derivation of quantities from observations. May import ``ports`` and ``register``.

Declaration lives in ``register``; this layer returns
:class:`~governed_bi.register.quantity.Measured` or says why it could not.
Formatting only via ``Measured.render`` (``tools/check_measurement_locality.py``).
"""


from __future__ import annotations

__all__: list[str] = []
