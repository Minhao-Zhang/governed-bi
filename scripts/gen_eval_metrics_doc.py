"""Generate docs/eval-metrics.md from the register in governed_bi.eval.metrics.

Run after editing the register so the doc cannot drift from the code::

    uv run python scripts/gen_eval_metrics_doc.py            # write the doc
    uv run python scripts/gen_eval_metrics_doc.py --check     # CI: verify, write nothing

Every count the page prints is taken from ``len()`` of a register tuple, never from
re-listing the groups here. That is not style: the previous version summed
rates + counts + means + blocks and silently omitted ``SUMMARY_CONDITIONALS``, so the
page advertised 80 summary fields while the register declared 86 — and the only test
over the doc grepped for names in backticks, which every one of the six missing
fields passed.
"""

import argparse
import difflib
import json
import pathlib
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8")

DOC_PATH = pathlib.Path("docs/eval-metrics.md")

# Run in a subprocess so the generator needs no import path of its own, and so the
# register it reads is the one `uv run` resolves — the same one the tests import.
DUMP = r"""
import json
from governed_bi.eval import metrics as m
print(json.dumps({
    "schema": [(x.name, x.meaning) for x in m.MANIFEST_SCHEMA],
    "knobs": [(x.name, x.meaning) for x in m.MANIFEST_KNOBS],
    "scope": [(x.name, x.meaning) for x in m.MANIFEST_SCOPE],
    "operational": [(x.name, x.meaning) for x in m.MANIFEST_OPERATIONAL],
    "stamped": [(x.name, x.meaning) for x in m.MANIFEST_STAMPED],
    "mode_specific": [(x.name, x.meaning) for x in m.MANIFEST_MODE_SPECIFIC],
    "rates": [(x.name, x.meaning, x.denominator) for x in m.SUMMARY_RATES],
    "conditionals": [(x.name, x.meaning, x.denominator) for x in m.SUMMARY_CONDITIONALS],
    "counts": list(m.SUMMARY_COUNTS),
    "means": list(m.SUMMARY_MEANS),
    "blocks": list(m.SUMMARY_BLOCKS),
    "stage_events": list(m.STAGE_EVENT_FIELDS),
    "split_gap_rates": list(m.SPLIT_GAP_RATES),
    "split_gap_fields": list(m.SPLIT_GAP_FIELDS),
    "row": {
        "identity": list(m.ROW_IDENTITY), "verdict": list(m.ROW_VERDICT),
        "prediction": list(m.ROW_PREDICTION), "governance": list(m.ROW_GOVERNANCE),
        "context": list(m.ROW_CONTEXT), "routing": list(m.ROW_ROUTING),
        "width": list(m.ROW_WIDTH),
        "leakage": list(m.ROW_LEAKAGE), "oracle": list(m.ROW_ORACLE),
        "cost": list(m.ROW_COST), "provenance": list(m.ROW_PROVENANCE),
    },
    # Every printed count comes from here, i.e. from len() of the register tuple the
    # artifact is generated from. A count computed in the generator is a second
    # listing of the register, and a second listing is a listing that can be wrong.
    "n": {
        "manifest_required": len(m.MANIFEST_FIELDS),
        "manifest_declared": len(m.MANIFEST_DECLARED),
        "row": len(m.ROW_FIELDS),
        "summary": len(m.SUMMARY_FIELDS),
        "stage_events": len(m.STAGE_EVENT_FIELDS),
        "split_gap": len(m.SPLIT_GAP_FIELDS),
    },
}))
"""


def table(rows, headers):
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    for r in rows:
        lines.append("| " + " | ".join(f"`{c}`" if i == 0 else str(c) for i, c in enumerate(r)) + " |")
    return "\n".join(lines)


def group(name, names):
    return f"**{name}** — " + ", ".join(f"`{n}`" for n in names)


def render(d):
    n = d["n"]
    # Built as data, not as literal table rows: the counts are the whole point of this
    # table and a hand-typed row is where the wrong one came from.
    artifacts = [
        (
            "manifest.json",
            f"{n['manifest_declared']} ({n['manifest_required']} in every run)",
            "`index.COMPARABILITY_KEYS`, `index.RESUME_DRIFT_KEYS`",
        ),
        (
            "generations.<arm>.jsonl",
            f"{n['row']} per (question, arm)",
            "`_summarise_rows`, `analysis`, `power`, `error_taxonomy`",
        ),
        ("summary.json", n["summary"], "`index.quotable`"),
        (
            "stage_events.jsonl",
            f"{n['stage_events']} per (question, arm, stage)",
            "read by hand; per-stage latency attribution",
        ),
        (
            "split_gap.json",
            n["split_gap"],
            "read by hand; `--split both` only",
        ),
    ]
    return f"""# Eval metrics: every field a run records

A run writes up to five artifacts, and this is the register of what is in them. The
machine-readable source of truth is
[`src/governed_bi/eval/metrics.py`](../src/governed_bi/eval/metrics.py); this page
is generated from it — every field name and every count below comes from a register
tuple, so `uv run python scripts/gen_eval_metrics_doc.py --check` fails in CI if the
two disagree.

`tests/test_eval_metrics.py` checks the register against what the drivers emit:
the pooled driver's manifest and arm summary **in both directions** (a field that is
emitted-but-undeclared or declared-but-absent fails the suite), the generation row
against what the summariser reads off it, and the single-schema driver's
`ArmSummary` in the emitted-but-undeclared direction only — that driver reports a
documented subset of `summary.json`, so "declared but absent" is expected there.

{table(artifacts, ["Artifact", "Fields", "Consumer"])}

`stage_events.jsonl` is written by the pooled driver only, and `split_gap.json` only
under `--split both`. Neither is read by a gate.

## Why this file exists

Every one of the first three used to be an undeclared dict built independently by
each of the two drivers, and consumed by `.get()` in eight modules — where a renamed
or missing key degrades silently to `None` instead of raising.

That is not hypothetical. `comparable()` skips a knob that is `None` on both
sides, reasoning that two runs which both predate a knob did not differ in it.
Correct — and it is exactly why an *absent* key is dangerous: absence is
indistinguishable from agreement. The single-schema driver's manifest omitted six
of the eight comparability keys of the time. Four were harmless (it pins one schema,
so the router never runs and its knobs have no value to record). Two were not:
`split`, and `corpus_content_hash` — which `index.py`'s own comment names as the one
thing the check did not cover, *because the corpus is the treatment*. So two runs of
that driver, over different corpora on different splits, compared as the same
configuration. And it was the driver whose numbers were historically quoted.

Both modes now build through `metrics.build_manifest`, and
`metrics.write_manifest` validates before writing. A knob that genuinely does not
apply is recorded as `None` **explicitly**, with `routing_bypassed` saying why, so
"not applicable" and "not recorded" stop looking alike.

Presence is all a validator can check, though, and a *defaulted* parameter passes a
presence check while recording a value the run never used. That happened:
`llm_temperature` defaulted to `None` and the single-schema driver never passed it,
so every one of its manifests recorded "provider default" for runs whose temperature
was configured and really forwarded to the model. So every knob and every scope field
is now a **required** keyword of `build_manifest`, and `manifest_schema_version`
records that a given manifest was built that way — `comparable()` refuses a pair
whose records predate the guarantee rather than applying the None-on-both-sides rule
to manifests that cannot support it.

## 1. Manifest — what makes a row mean something

### Contract

{table(d["schema"], ["field", "meaning"])}

### Knobs (gate keys: must be present in every mode, `None` when N/A)

Every one of these is a required keyword of `build_manifest`, and
`index.COMPARABILITY_KEYS` is **derived** from this list minus an explicit,
documented exclusion set (`index.COMPARABILITY_EXCLUSIONS`) — so a knob added here
joins the comparability gate by default instead of silently skipping it.

{table(d["knobs"], ["field", "meaning"])}

### Scope (a resume that disagrees is a different experiment)

Also required keywords: `arms=()` recorded for a run that served three arms is a
false record no presence check can catch.

{table(d["scope"], ["field", "meaning"])}

### Operational (recorded, deliberately not gate keys)

These change how long a run takes, never what a scored row means, so these are the
only `build_manifest` parameters allowed a default.

{table(d["operational"], ["field", "meaning"])}

### Stamped after the build (declared, not required)

No builder can fill these, because the value does not exist when the manifest is
written — and the manifest is written *before* the run phase so that a crashed run
still leaves its knobs on disk.

{table(d["stamped"], ["field", "meaning"])}

### Mode-specific (present in one mode only)

{table(d["mode_specific"], ["field", "meaning"])}

## 2. Arm summary — the rates and their denominators

The recurring defect class in this harness is a rate whose denominator silently
absorbs another outcome's failures. Over all rows, an arm that refuses 8 of 10
reports the *best* graded-delivery rate and the *worst* safety-clearance rate,
because refusing is neither delivering nor clearing — so a rung that refuses more
looks like a rung that governs better. Naming the population is what makes that
reviewable, and a test asserts every declared rate names one.

{table(d["rates"], ["rate", "meaning", "denominator"])}

### Conditional diagnostics — which part of the governance is doing the work

Each of these reports a rate on **both sides** of something the run produced. Every
input was already recorded per row and aggregated against nothing until 2026-07-28.
They are within-arm, so they cost no extra serve and apply retroactively to any
existing `generations.<arm>.jsonl`.

**All of them are observational.** None is a randomised contrast, so none may be read
as the effect of the thing it splits on: two of them condition on an output of the
system itself (post-treatment selection across arms), two split on whether retrieval
matched (corpus coverage, not note value), and one compares questions that already
failed against questions that did not (two difficulty populations). Each declaration
below carries the specific caveat.

Each block carries its own `n_*` counts, and where a row can fail to record the
input, an `n_unstamped` count — an absent input is counted out, never filed on the
negative side. That is the trap the twin strata already document: `not r.get(...)`
puts an ABSENT key in the FALSE stratum, which silently turns one side of a split
into the pooled figure.

{table(d["conditionals"], ["block", "meaning", "denominator"])}

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

{group("width", d["row"]["width"])}

{group("leakage", d["row"]["leakage"])}

{group("oracle", d["row"]["oracle"])}

{group("cost", d["row"]["cost"])}

{group("provenance", d["row"]["provenance"])}

## 4. Stage events — one record per (question, arm, stage)

`stage_events.jsonl`, pooled driver only, flattened from the serve path's own
`stage_events` provenance. A separate file rather than row fields because a turn
emits many of these and the row is already the widest artifact.

{group("fields", d["stage_events"])}

## 5. Split gap — `train - test` per arm

`split_gap.json`, written only under `--split both`. Scoring the train split is not
a second result (`index.quotable` refuses a train-scored run); the *gap* is how much
of an arm's score does not survive being asked something new. Not paired and not
significance tested — a within-arm diagnostic, never a headline.

The gapped rates are a chosen subset of the rates above: every one is accuracy-like,
so "train is higher" means "did not transfer". Gapping `crash_rate` or
`refusal_rate` would invite reading operational noise as overfitting.

{group("gapped rates", d["split_gap_rates"])}

{group("file fields", d["split_gap_fields"])}

## Regenerating this page

The tables and every count above come from the register. After editing
`src/governed_bi/eval/metrics.py`, re-run the generator and commit both:

```bash
uv run python scripts/gen_eval_metrics_doc.py
```

CI runs `--check`, which writes nothing and fails if this file is not what a fresh
generation would produce.
"""


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--check",
        action="store_true",
        help="write nothing; exit 0 if docs/eval-metrics.md matches a fresh "
        "generation, 1 with a diff if it does not (this is the CI step)",
    )
    args = ap.parse_args(argv)

    out = subprocess.run(
        ["uv", "run", "python", "-c", DUMP], capture_output=True, text=True, check=True
    )
    doc = render(json.loads(out.stdout))

    if not args.check:
        DOC_PATH.write_text(doc, encoding="utf-8")
        print(f"{DOC_PATH} written:", len(doc.splitlines()), "lines")
        return 0

    if not DOC_PATH.exists():
        print(f"{DOC_PATH} does not exist; run the generator without --check")
        return 1
    # `read_text` / `write_text` both go through text mode, so this compares the doc's
    # logical content and is insensitive to the CRLF the writer produces on Windows.
    current = DOC_PATH.read_text(encoding="utf-8")
    if current == doc:
        print(f"{DOC_PATH} is up to date with governed_bi.eval.metrics")
        return 0

    diff = list(
        difflib.unified_diff(
            current.splitlines(),
            doc.splitlines(),
            fromfile=f"{DOC_PATH} (committed)",
            tofile="fresh generation from governed_bi.eval.metrics",
            lineterm="",
            n=1,
        )
    )
    print(
        f"{DOC_PATH} is stale: it is not what the register generates. "
        "Run `uv run python scripts/gen_eval_metrics_doc.py` and commit the result.\n"
    )
    shown = diff[:60]
    print("\n".join(shown))
    if len(diff) > len(shown):
        print(f"... {len(diff) - len(shown)} more diff line(s)")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
