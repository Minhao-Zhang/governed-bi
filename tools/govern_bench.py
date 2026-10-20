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
`[[probe]]` asks what a tool that builds no statement (`inspect_schema`, `sample_rows`) shows the
model, which only the licence bound in `ToolBounds` stands in front of. The runner is here, not
in `govern/adversarial_run.py`, because it calls `serve/fetch.py`, above `govern/` in the layer
order.

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
    guard_report_lines,
    report_lines,
    run_adversarial_suite,
    run_guard_suite,
)
from governed_bi.govern.layers import Layer  # noqa: E402
from governed_bi.govern.policy import GUARD_RULE_IDS  # noqa: E402

PROBE_SURFACES: frozenset[str] = frozenset({"inspect_schema", "may_sample"})


@dataclass(frozen=True, slots=True)
class ProbeResult:
    """One disclosure probe, and what the tool actually showed."""

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
    """What the two tools show, computed once through the production functions.

    The bounds come from ``ToolBounds`` and the payload from ``serve/fetch.py``; a probe harness
    that rebuilt either would measure itself.
    """
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
            pid, kind, surface,
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
            "no benign disclosure probes. A bound that refused everything would score a "
            "perfect disclosure rate, which is the argument the case suite already makes about "
            "its own controls."
        )
    surfaces = _disclosure_surfaces(load_adversarial_suite().world)
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
        "   a tool showed something outside the licensed set",
        f"  over-withheld rate   {len(hidden) / len(benign):.3f}  ({len(hidden)}/{len(benign)})"
        "   a tool refused something the licensed set allows",
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
    guard = run_guard_suite()
    probes = run_disclosure_probes()

    if args.json:
        payload = _json_payload(report)
        # Its own block and not folded into `attack`/`benign` above: the statement half's
        # denominators are statements and this half's are questions, and a combined bypass
        # rate over both would be a number with two populations in it.
        payload["prompt_injection"] = {
            "questions": len(guard.results),
            "attack": {
                "bypassed": {
                    "n": len(guard.with_status("bypassed", "attack")),
                    "of": len(guard.of_kind("attack")),
                },
                "misattributed": {
                    "n": len(guard.with_status("misattributed", "attack")),
                    "of": len(guard.of_kind("attack")),
                },
                "guardrail_error": {
                    "n": len(guard.with_status("guardrail_error", "attack")),
                    "of": len(guard.of_kind("attack")),
                },
                "caught": {
                    "n": len(guard.with_status("caught", "attack")),
                    "of": len(guard.of_kind("attack")),
                },
            },
            "benign": {
                "false_refusal": {
                    "n": len(guard.with_status("false_refusal", "benign")),
                    "of": len(guard.of_kind("benign")),
                },
                "allowed": {
                    "n": len(guard.with_status("allowed", "benign")),
                    "of": len(guard.of_kind("benign")),
                },
            },
            "rule_recall": {
                rule_id: {
                    "n": len(
                        [
                            r
                            for r in guard.of_kind("attack")
                            if r.case.expect_rule == rule_id and r.status == "caught"
                        ]
                    ),
                    "of": len(
                        [r for r in guard.of_kind("attack") if r.case.expect_rule == rule_id]
                    ),
                }
                for rule_id in sorted(GUARD_RULE_IDS)
            },
            "failures": [
                {"id": r.case.id, "status": r.status, "detail": r.detail}
                for r in guard.failures()
            ],
        }
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
        for line in guard_report_lines(guard):
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
            for result in guard.results:
                print(
                    f"  {result.status:<15} {result.case.kind:<7} "
                    f"{str(result.case.expect_rule or '-'):<22} {result.case.id}"
                )
            for probe in probes:
                print(f"  {probe.status:<15} {probe.kind:<7} {probe.surface:<15} {probe.id}")

    # All three halves gate. A driver that printed a prompt-injection bypass and exited zero
    # would be the same shape as the defect the half exists because of: a number nobody acts on.
    return 1 if report.failures() or guard.failures() or any(r.failed for r in probes) else 0


if __name__ == "__main__":
    raise SystemExit(main())
