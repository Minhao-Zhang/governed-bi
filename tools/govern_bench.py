"""Run the adversarial governance suite and print its report.

**Why this exists.** What governance buys had no number: the layer stack, the allowlist and the
scope gate had no adversarial evaluation at all (open-work.md §3.11, which now carries the first
one). Every other measurement in this repository costs model calls and has a noise
floor. This one costs nothing and has none: the layer stack is deterministic, so the suite runs
offline with no credentials and two runs of it are identical.

The suite itself is ``src/governed_bi/govern/adversarial.toml`` — cases as data, so this driver
and ``tests/govern/test_adversarial_suite.py`` read the same file rather than two lists that
drift. The gate that fails a build lives in the test; this prints the numbers.

**Deliberately not named ``check_*``.** ``tests/conformance/test_register_closure.py`` requires
every ``tools/check_*.py`` to be declared in CI or declared manual, and that register is for lint
gates that pass or fail a tree. This reports a measurement, and its pass/fail half already runs in
CI as part of ``pytest``. Naming it ``check_`` would add a second, redundant enforcement of the
same property in a register whose value is that each entry means something distinct.

**The second half: disclosure.** A `[[case]]` asks whether a statement was refused. A
`[[probe]]` asks what the principal was *shown* — the rendered context block, and the payload of
each tool that has no statement for the layer stack to read. Refusing is not disclosing, and on
2026-08-12 an independent review found three places where the two disagreed in the shipped tree
(`inspect_schema` handing over every denied column of an authorized table; a bare-spelled join's
ON clause naming a withheld table; `may_sample` failing open on a mixed-case denial). None of
the three is expressible as SQL, so a suite that measures only statements would let all three
back in.

The probe runner lives **here and not in `govern/adversarial_run.py`** because it must call
`serve/context.py::withheld_by_grant` and `serve/fetch.py`, which sit above `govern/` in the
layer order. A tool is above every layer, which is what tools are for. The cases stay data in
`adversarial.toml`, so this driver and `tests/govern/test_adversarial_suite.py` still read one
file rather than two lists that drift.

Usage::

    uv run --frozen python tools/govern_bench.py            # the report
    uv run --frozen python tools/govern_bench.py --cases    # every case and what it did
    uv run --frozen python tools/govern_bench.py --json     # the same numbers, machine-readable

Exit code is 1 when the suite has any failure — a bypass, a misattribution, a swallowed
guardrail error, a false refusal nobody declared, or a disclosure — so this is usable as a
pre-release check even though it is not registered as one.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import tomllib
from dataclasses import dataclass

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from governed_bi.govern.adversarial import (  # noqa: E402
    SUITE_FILE,
    AdversarialWorld,
    _world_assets,
    load_adversarial_suite,
)
from governed_bi.govern.adversarial_run import (  # noqa: E402
    SuiteReport,
    report_lines,
    run_adversarial_suite,
)
from governed_bi.govern.layers import Layer  # noqa: E402

PROBE_SURFACES: frozenset[str] = frozenset(
    {"context", "inspect_schema", "read_body", "may_sample"}
)


@dataclass(frozen=True, slots=True)
class ProbeResult:
    """One disclosure probe, and what the principal was actually shown."""

    id: str
    kind: str
    surface: str
    #: ``disclosed`` / ``withheld`` for an attack, ``shown`` / ``hidden`` for a benign control.
    status: str
    detail: str

    @property
    def failed(self) -> bool:
        return self.status in ("disclosed", "hidden")


def _probe_world_assets(world: AdversarialWorld, declared: list[dict]) -> dict[str, object]:
    """The world's tables and columns, plus the joins and terms only the probes read."""
    from governed_bi.corpus.schema import AssetType, Binding, JoinAsset, TermAsset

    assets = {a.id: a for a in _world_assets(world)}
    for row in declared:
        kind = str(row.get("kind") or "")
        if kind == "join":
            asset = JoinAsset(
                id=str(row["id"]),
                left_table=str(row["left_table"]),
                right_table=str(row["right_table"]),
                on=str(row["on"]),
                summary=str(row.get("summary") or ""),
            )
        elif kind == "term":
            asset = TermAsset(
                id=str(row["id"]),
                name=str(row["name"]),
                summary=str(row.get("summary") or ""),
                binding=Binding(target_type=AssetType.column, target_id=str(row["target_id"])),
            )
        else:
            raise ValueError(f"[[probe_asset]] {row.get('id')!r}: unknown kind {kind!r}")
        assets[asset.id] = asset
    return assets


def _disclosure_surfaces(world: AdversarialWorld, declared: list[dict]) -> dict[str, object]:
    """Everything this principal is shown, computed once, through the production functions.

    Not a re-implementation: the block comes from ``render_context`` with the withheld set
    ``assemble`` passes it, the bounds come from ``ToolBounds`` with the same set, and the tool
    payloads come from ``serve/fetch.py``. A probe harness that rebuilt any of the three would
    measure itself.
    """
    from governed_bi.govern.bounds import ToolBounds
    from governed_bi.serve.context import render_context, withheld_by_grant
    from governed_bi.serve.fetch import inspect_schema, read_body

    assets = _probe_world_assets(world, declared)
    resolved = __import__(
        "governed_bi.govern.access", fromlist=["resolve_grant"]
    ).resolve_grant(world.grant(), world.default_schema)
    withheld = withheld_by_grant(assets, resolved)

    # Every asset is treated as retrieved, which is the widest the renderer can be asked to be:
    # a probe over a narrow shortlist would pass for a table retrieval simply never found.
    # `by_type` and not only `selected`, because `_build_pieces` walks the type buckets and the
    # closure to decide *which ids the block may name* and reads `selected` only for scoring —
    # so a probe that filled `selected` alone renders an empty block and every attack passes.
    by_type: dict[str, list[str]] = {}
    for aid, asset in assets.items():
        by_type.setdefault(str(getattr(asset.asset_type, "value", "")), []).append(aid)
    retrieved = {
        "selected": {aid: {"score": 1.0} for aid in assets},
        "pulled_in": {},
        "by_type": by_type,
        "attributions": {},
    }
    block, _hash = render_context(
        retrieved=retrieved,
        assets_by_id=assets,
        schemas=sorted({str(getattr(a, "schema", "") or "") for a in assets.values()} - {""}),
        withheld=withheld,
    )
    bounds = ToolBounds(
        licensed=world.licensed,
        readable_assets=frozenset(assets) - withheld,
        grant=resolved,
        withheld=withheld,
    )
    return {
        "assets": assets,
        "bounds": bounds,
        "context": block,
        "inspect_schema": lambda t: inspect_schema(t, bounds=bounds, assets=assets)[0],
        "read_body": lambda t: read_body(
            [t], bounds=bounds, assets=assets, max_chars=80_000
        )[0],
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
            pid, kind, surface,
            "disclosed" if kind == "attack" else "hidden",
            f"may_sample({target!r}) = {allowed}, expected {expect}",
        )

    payload = surfaces["context"] if surface == "context" else surfaces[surface](target)
    leaked = [s for s in probe.get("forbidden") or () if str(s) in payload]
    absent = [s for s in probe.get("required") or () if str(s) not in payload]
    if leaked:
        return ProbeResult(pid, kind, surface, "disclosed", f"names {leaked}")
    if absent:
        return ProbeResult(pid, kind, surface, "hidden", f"does not name {absent}")
    return ProbeResult(pid, kind, surface, "withheld" if kind == "attack" else "shown", "")


def run_disclosure_probes() -> tuple[ProbeResult, ...]:
    """Every ``[[probe]]`` in the suite file, against the same world the cases read."""
    data = tomllib.loads(SUITE_FILE.read_text(encoding="utf-8"))
    probes = list(data.get("probe") or ())
    if not probes:
        raise ValueError(f"{SUITE_FILE.name}: no [[probe]] tables — the disclosure half is gone")
    seen = {str(p["id"]) for p in probes}
    if len(seen) != len(probes):
        raise ValueError("duplicate probe id; ids key the report")
    if not any(str(p.get("kind")) == "benign" for p in probes):
        raise ValueError(
            "no benign disclosure probes. A renderer that withheld everything would score a "
            "perfect disclosure rate, which is the argument the case suite already makes about "
            "its own controls."
        )
    surfaces = _disclosure_surfaces(load_adversarial_suite().world, list(data.get("probe_asset") or ()))
    return tuple(_run_probe(p, surfaces) for p in probes)


def probe_lines(results: tuple[ProbeResult, ...]) -> list[str]:
    """The disclosure report. Denominators on the line, as with every rate above."""
    attacks = [r for r in results if r.kind == "attack"]
    benign = [r for r in results if r.kind == "benign"]
    leaked = [r for r in attacks if r.status == "disclosed"]
    hidden = [r for r in benign if r.status == "hidden"]
    lines = [
        "",
        f"disclosure probes — {len(results)} ({len(attacks)} attack, {len(benign)} benign)",
        f"  disclosure rate      {len(leaked) / len(attacks):.3f}  ({len(leaked)}/{len(attacks)})"
        "   the principal was shown something the grant withholds",
        f"  over-withheld rate   {len(hidden) / len(benign):.3f}  ({len(hidden)}/{len(benign)})"
        "   the principal was denied something the grant allows",
    ]
    by_surface: dict[str, list[ProbeResult]] = {}
    for r in results:
        by_surface.setdefault(r.surface, []).append(r)
    lines.append("  by surface (attack / benign)")
    for surface in sorted(by_surface):
        group = by_surface[surface]
        n_attack = sum(1 for r in group if r.kind == "attack")
        lines.append(f"    {surface:<16}{n_attack:>3} / {len(group) - n_attack:>3}")
    failures = [r for r in results if r.failed]
    lines.append(f"  failures: {len(failures)}")
    lines += [f"    {r.status:<11} {r.id}: {r.detail}" for r in failures]
    return lines


def _json_payload(report: SuiteReport) -> dict[str, object]:
    """The report as data. Rates are counts and denominators, never pre-formatted strings:
    a consumer that wants three decimal places can produce them, and one that wants the
    denominator cannot recover it from ``"0.042"``."""
    def counts(status: str, kind: str) -> dict[str, int]:
        return {
            "n": len(report.with_status(status, kind)),
            "of": len(report.of_kind(kind)),
        }

    return {
        "version": report.version,
        "cases": len(report.results),
        "attack": {
            "bypassed": counts("bypassed", "attack"),
            "misattributed": counts("misattributed", "attack"),
            "guardrail_error": counts("guardrail_error", "attack"),
            "caught": counts("caught", "attack"),
        },
        "benign": {
            "false_refusal": counts("false_refusal", "benign"),
            "guardrail_error": counts("guardrail_error", "benign"),
            "allowed": counts("allowed", "benign"),
        },
        "layer_recall": {
            layer.name: {
                "caught": sum(
                    1
                    for r in report.of_kind("attack")
                    if r.case.expect_layer is layer and r.status == "caught"
                ),
                "owned": sum(
                    1 for r in report.of_kind("attack") if r.case.expect_layer is layer
                ),
            }
            for layer in Layer
        },
        "families": {k: {"attack": a, "benign": b} for k, (a, b) in report.by_family().items()},
        "failures": [
            {"id": r.case.id, "status": r.status, "detail": r.detail} for r in report.failures()
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", action="store_true", help="print every case and its status")
    parser.add_argument("--json", action="store_true", help="print the numbers as JSON")
    args = parser.parse_args()

    report = run_adversarial_suite()
    probes = run_disclosure_probes()

    if args.json:
        payload = _json_payload(report)
        payload["disclosure"] = {
            "probes": len(probes),
            "attack": {
                "disclosed": sum(1 for r in probes if r.kind == "attack" and r.failed),
                "of": sum(1 for r in probes if r.kind == "attack"),
            },
            "benign": {
                "hidden": sum(1 for r in probes if r.kind == "benign" and r.failed),
                "of": sum(1 for r in probes if r.kind == "benign"),
            },
            "failures": [
                {"id": r.id, "status": r.status, "detail": r.detail} for r in probes if r.failed
            ],
        }
        print(json.dumps(payload, indent=2))
    else:
        for line in report_lines(report):
            print(line)
        for line in probe_lines(probes):
            print(line)
        if args.cases:
            print()
            for result in report.results:
                bypass = f" {result.case.bypass}" if result.case.bypass else ""
                print(
                    f"  {result.status:<15} {result.case.kind:<7} "
                    f"{result.case.family:<10}{bypass:<5} {result.case.id}"
                )
            for probe in probes:
                print(f"  {probe.status:<15} {probe.kind:<7} {probe.surface:<15} {probe.id}")

    return 1 if report.failures() or any(r.failed for r in probes) else 0


if __name__ == "__main__":
    raise SystemExit(main())
