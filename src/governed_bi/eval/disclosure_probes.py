"""What ``inspect_schema`` and ``may_sample`` show, for each ``[[probe]]``.

The runner lives in ``eval/`` because it calls ``serve/fetch.py``. ``govern/``
cannot import ``serve/``.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass

from governed_bi.govern.adversarial import (
    SUITE_FILE,
    AdversarialWorld,
    _world_assets,
    load_adversarial_suite,
)

PROBE_SURFACES: frozenset[str] = frozenset({"inspect_schema", "may_sample"})


@dataclass(frozen=True, slots=True)
class ProbeResult:
    """One probe and what the tool showed."""

    id: str
    kind: str
    surface: str
    #: ``disclosed`` / ``withheld`` for an attack, ``shown`` / ``hidden`` for a benign control.
    status: str
    detail: str

    @property
    def failed(self) -> bool:
        return self.status in ("disclosed", "hidden")


def _disclosure_surfaces(world: AdversarialWorld) -> dict[str, object]:
    """Schema text and sample permission, through the production functions."""
    from governed_bi.govern.bounds import ToolBounds
    from governed_bi.serve.fetch import inspect_schema

    assets = {a.id: a for a in _world_assets(world)}
    bounds = ToolBounds(licensed=world.licensed, readable_assets=frozenset(assets))
    return {
        "bounds": bounds,
        "inspect_schema": lambda t: inspect_schema(t, bounds=bounds, assets=assets)[0],
    }


def _run_probe(probe: dict, surfaces: dict) -> ProbeResult:
    surface = str(probe.get("surface") or "")
    if surface not in PROBE_SURFACES:
        raise ValueError(f"probe {probe.get('id')!r}: surface {surface!r} is not declared")
    pid, kind = str(probe["id"]), str(probe["kind"])
    target = str(probe.get("target") or "")

    if surface == "may_sample":
        expect = str(probe.get("expect") or "")
        if expect not in ("allowed", "refused"):
            raise ValueError(f"probe {pid}: may_sample needs expect = allowed | refused")
        allowed = surfaces["bounds"].may_sample(target)
        if allowed == (expect == "allowed"):
            return ProbeResult(pid, kind, surface, "withheld" if kind == "attack" else "shown", "")
        return ProbeResult(
            pid,
            kind,
            surface,
            "disclosed" if kind == "attack" else "hidden",
            f"may_sample({target!r}) = {allowed}, expected {expect}",
        )

    payload = surfaces[surface](target)
    leaked = [s for s in probe.get("forbidden") or () if str(s) in payload]
    absent = [s for s in probe.get("required") or () if str(s) not in payload]
    if leaked:
        return ProbeResult(pid, kind, surface, "disclosed", f"names {leaked}")
    if absent:
        return ProbeResult(pid, kind, surface, "hidden", f"does not name {absent}")
    return ProbeResult(pid, kind, surface, "withheld" if kind == "attack" else "shown", "")


def run_disclosure_probes() -> tuple[ProbeResult, ...]:
    """Every ``[[probe]]``, on the same world the cases read."""
    data = tomllib.loads(SUITE_FILE.read_text(encoding="utf-8"))
    probes = list(data.get("probe") or ())
    if not probes:
        raise ValueError(f"{SUITE_FILE.name}: no [[probe]] tables — the disclosure half is gone")
    seen = {str(p["id"]) for p in probes}
    if len(seen) != len(probes):
        raise ValueError("duplicate probe id; ids key the report")
    if not any(str(p.get("kind")) == "benign" for p in probes):
        raise ValueError(
            "no benign disclosure probes. A bound that refused everything would score a "
            "perfect disclosure rate, which is the argument the case suite already makes about "
            "its own controls."
        )
    surfaces = _disclosure_surfaces(load_adversarial_suite().world)
    return tuple(_run_probe(p, surfaces) for p in probes)
