"""Generate the rate/knob tables in docs/eval-metrics.md from the register.

Run after editing governed_bi.eval.metrics so the doc cannot drift from the code.
"""

import json
import pathlib
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8")

DUMP = r"""
import json
from governed_bi.eval import metrics as m
print(json.dumps({
    "knobs": [(x.name, x.meaning) for x in m.MANIFEST_KNOBS],
    "scope": [(x.name, x.meaning) for x in m.MANIFEST_SCOPE],
    "operational": [(x.name, x.meaning) for x in m.MANIFEST_OPERATIONAL],
    "rates": [(x.name, x.meaning, x.denominator) for x in m.SUMMARY_RATES],
    "counts": list(m.SUMMARY_COUNTS),
    "means": list(m.SUMMARY_MEANS),
    "blocks": list(m.SUMMARY_BLOCKS),
    "row": {
        "identity": list(m.ROW_IDENTITY), "verdict": list(m.ROW_VERDICT),
        "prediction": list(m.ROW_PREDICTION), "governance": list(m.ROW_GOVERNANCE),
        "context": list(m.ROW_CONTEXT), "routing": list(m.ROW_ROUTING),
        "leakage": list(m.ROW_LEAKAGE), "oracle": list(m.ROW_ORACLE),
        "cost": list(m.ROW_COST), "provenance": list(m.ROW_PROVENANCE),
    },
}))
"""

out = subprocess.run(
    ["uv", "run", "python", "-c", DUMP], capture_output=True, text=True, check=True
)

d = json.loads(out.stdout)


def table(rows, headers):
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    for r in rows:
        lines.append("| " + " | ".join(f"`{c}`" if i == 0 else str(c) for i, c in enumerate(r)) + " |")
    return "\n".join(lines)


def group(name, names):
    return f"**{name}** — " + ", ".join(f"`{n}`" for n in names)


n_row = sum(len(v) for v in d["row"].values())
n_summary = len(d["rates"]) + len(d["counts"]) + len(d["means"]) + len(d["blocks"])
n_manifest = len(d["knobs"]) + len(d["scope"]) + len(d["operational"])

doc = f"""# Eval metrics: every field a run records

A run writes three artifacts, and this is the register of what is in them. The
machine-readable source of truth is
[`src/governed_bi/eval/metrics.py`](../src/governed_bi/eval/metrics.py); this page
is generated from it, and `tests/test_eval_metrics.py` asserts the register
matches what the drivers actually emit — in both directions, so a field that is
emitted-but-undeclared or declared-but-absent fails the suite.

| Artifact | Fields | Consumer |
|---|---|---|
| `manifest.json` | {n_manifest} | `index.COMPARABILITY_KEYS`, `index.RESUME_DRIFT_KEYS` |
| `generations.<arm>.jsonl` | {n_row} per (question, arm) | `_summarise_rows`, `analysis`, `power`, `error_taxonomy` |
| `summary.json` | {n_summary} | `index.quotable` |

## Why this file exists

Every one of the three used to be an undeclared dict built independently by each
of the two drivers, and consumed by `.get()` in eight modules — where a renamed
or missing key degrades silently to `None` instead of raising.

That is not hypothetical. `comparable()` skips a knob that is `None` on both
sides, reasoning that two runs which both predate a knob did not differ in it.
Correct — and it is exactly why an *absent* key is dangerous: absence is
indistinguishable from agreement. The single-schema driver's manifest omitted six
of the eight comparability keys. Four were harmless (it pins one schema, so the
router never runs and its knobs have no value to record). Two were not: `split`,
and `corpus_content_hash` — which `index.py`'s own comment names as the one thing
the check did not cover, *because the corpus is the treatment*. So two runs of
that driver, over different corpora on different splits, compared as the same
configuration. And it was the driver whose numbers were historically quoted.

Both modes now build through `metrics.build_manifest`, and
`metrics.write_manifest` validates before writing. A knob that genuinely does not
apply is recorded as `None` **explicitly**, with `routing_bypassed` saying why, so
"not applicable" and "not recorded" stop looking alike.

## 1. Manifest — what makes a row mean something

### Knobs (gate keys: must be present in every mode, `None` when N/A)

{table(d["knobs"], ["field", "meaning"])}

### Scope (a resume that disagrees is a different experiment)

{table(d["scope"], ["field", "meaning"])}

### Operational (recorded, deliberately not gate keys)

These change how long a run takes, never what a scored row means.

{table(d["operational"], ["field", "meaning"])}

## 2. Arm summary — the rates and their denominators

The recurring defect class in this harness is a rate whose denominator silently
absorbs another outcome's failures. Over all rows, an arm that refuses 8 of 10
reports the *best* graded-delivery rate and the *worst* safety-clearance rate,
because refusing is neither delivering nor clearing — so a rung that refuses more
looks like a rung that governs better. Naming the population is what makes that
reviewable, and a test asserts every declared rate names one.

{table(d["rates"], ["rate", "meaning", "denominator"])}

### Counts

Each count exists so an exclusion from a rate above stays visible: a rate
reported without its excluded count reads as full coverage.

{group("counts", d["counts"])}

### Means and breakdown blocks

{group("means", d["means"])}

{group("blocks", d["blocks"])}

## 3. Generation row — one record per (question, arm)

{group("identity", d["row"]["identity"])}

{group("verdict", d["row"]["verdict"])}

{group("prediction", d["row"]["prediction"])}

{group("governance", d["row"]["governance"])}

{group("context", d["row"]["context"])}

{group("routing", d["row"]["routing"])}

{group("leakage", d["row"]["leakage"])}

{group("oracle", d["row"]["oracle"])}

{group("cost", d["row"]["cost"])}

{group("provenance", d["row"]["provenance"])}

## Regenerating this page

The tables above come from the register. After editing
`src/governed_bi/eval/metrics.py`, re-run the generator and commit both:

```bash
uv run python scripts/gen_eval_metrics_doc.py
```
"""

pathlib.Path("docs/eval-metrics.md").write_text(doc, encoding="utf-8")
print("docs/eval-metrics.md written:", len(doc.splitlines()), "lines")
