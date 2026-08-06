"""Report which authored schemas still match gold summaries (not yet rewritten)."""

from __future__ import annotations

import pathlib
import sys

import yaml

REPO = pathlib.Path(__file__).resolve().parents[1]
GOLD = REPO / "corpora" / "gold-semantic-layer-20260804"
AUTH = REPO / "corpora" / "_variant-authored-20260805"


def summary_of(path: pathlib.Path) -> str:
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    return (doc or {}).get("summary") or ""


def main() -> None:
    changed_schema = []
    same_schema = []
    table_changed = 0
    table_same = 0
    grain_set = 0
    ellipsis = []
    overlong = []

    for d in sorted(AUTH.iterdir()):
        if not d.is_dir() or d.name.startswith("_"):
            continue
        sp = d / f"{d.name}.yaml"
        gp = GOLD / d.name / f"{d.name}.yaml"
        if not sp.exists():
            continue
        s = summary_of(sp)
        g = summary_of(gp) if gp.exists() else ""
        if s != g:
            changed_schema.append(d.name)
        else:
            same_schema.append(d.name)
        if s.endswith("…") or s.endswith("..."):
            ellipsis.append(d.name)
        if len(s) > 250:
            overlong.append((d.name, len(s)))

        tdir = d / "tables"
        if tdir.exists():
            for tp in tdir.glob("*.yaml"):
                doc = yaml.safe_load(tp.read_text(encoding="utf-8"))
                gs = GOLD / d.name / "tables" / tp.name
                gold_s = summary_of(gs) if gs.exists() else ""
                if (doc or {}).get("summary") != gold_s:
                    table_changed += 1
                else:
                    table_same += 1
                if (doc or {}).get("grain"):
                    grain_set += 1

    print(f"schemas_changed={len(changed_schema)}/{len(changed_schema)+len(same_schema)}")
    print(f"tables_changed={table_changed} tables_same_as_gold={table_same}")
    print(f"grain_populated={grain_set}")
    print(f"ellipsis_schemas={ellipsis}")
    print(f"overlong={overlong}")
    if same_schema:
        print("UNCHANGED_SCHEMAS:", ", ".join(same_schema))


if __name__ == "__main__":
    main()
