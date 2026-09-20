"""What the gates prod enforces would have done to an arm measured without them.

    uv run --frozen python tools/shadow_replay.py runs/eval/<arm>.jsonl \
        --dataset ../BIRD-Data-Obfuscation/eval_dataset \
        [--scope-verdicts runs/eval/bi_scope_<model>.jsonl]

The eval driver runs ``guard_rules_enabled={}`` and the served app enables all six rules, so
every arm on disk measures an engine with the input guard off. This replays the gates over the
finished artifact instead of enforcing them during the run, because a turn the guard refuses is
a turn whose answer nobody ever sees: an enforced arm can report what governance cost in
coverage and never what it cost in right answers. The reasoning, the three tiers and what this
cannot tell you are in :mod:`governed_bi.eval.shadow`.

Reads artifacts. Costs nothing, spends nothing, and runs against arms already on disk.

``g_bi_scope`` is the one gate that asks a model, so it is not replayable from a row. Produce
its verdicts with ``tools/bi_scope_probe.py`` and pass them with ``--scope-verdicts``; without
them the scope gate is left out of the projection and the report says so, rather than the
projection quietly describing five of the six rules prod runs.

Exit 0 when a projection was produced, 1 when a gate could not be evaluated.
"""


from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def _rows(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _questions(dataset: Path) -> dict[str, dict[str, Any]]:
    """The dataset keyed by question id. The guard reads text; the row carries only the id."""
    records = _rows(dataset / "test_final.jsonl")
    return {str(record["question_id"]): record for record in records}


def _verdicts(path: Path) -> dict[str, str]:
    """``question_id -> outcome``, as written by ``tools/bi_scope_probe.py``."""
    return {str(row["question_id"]): str(row["outcome"]) for row in _rows(path)}


def main(argv: list[str] | None = None) -> int:
    from governed_bi.eval.shadow import (
        abstention_gate,
        bi_scope_gate,
        deterministic_guard_gate,
        prod_projection,
    )
    from governed_bi.measure.population import Population

    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("artifact", type=Path)
    parser.add_argument("--dataset", type=Path, default=Path("../BIRD-Data-Obfuscation/eval_dataset"))
    parser.add_argument(
        "--scope-verdicts",
        type=Path,
        default=None,
        help="output of tools/bi_scope_probe.py; without it g_bi_scope is left out",
    )
    args = parser.parse_args(argv)

    rows = _rows(args.artifact)
    if not rows:
        print(f"{args.artifact} holds no rows", file=sys.stderr)
        return 1
    arm = Population.of(args.artifact.stem, rows)

    gates = [deterministic_guard_gate(rows, _questions(args.dataset)), abstention_gate(rows)]
    if args.scope_verdicts is not None:
        gates.append(bi_scope_gate(rows, _verdicts(args.scope_verdicts)))
    else:
        print(
            "no --scope-verdicts: g_bi_scope is NOT in the projection below. It is one of the "
            "six rules the served app enables, and the only one that asks a model, so this "
            "report is about the other five. tools/bi_scope_probe.py produces it."
        )

    try:
        projection = prod_projection(arm, gates)
    except ValueError as err:
        print(str(err), file=sys.stderr)
        return 1
    print("\n".join(projection.render()))
    for gate in projection.gates:
        print(f"  {gate.gate_id}: {gate.why}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
