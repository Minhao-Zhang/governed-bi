"""Safely set summary / grain on a corpus asset YAML without store.write().

Usage:
  uv run --frozen python tools/_set_asset_fields.py PATH --summary '...'
  uv run --frozen python tools/_set_asset_fields.py PATH --summary '...' --grain 'one row per X'

Validates summary length against summary_max_chars (250). Does not truncate.
Exits 1 on validation failure; prints new len on success.
"""

from __future__ import annotations

import argparse
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

import yaml  # noqa: E402

from governed_bi.register.knobs import knob_default  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("path", type=pathlib.Path)
    p.add_argument("--summary")
    p.add_argument("--grain")
    p.add_argument("--clear-grain", action="store_true")
    args = p.parse_args()

    if args.summary is None and args.grain is None and not args.clear_grain:
        print("nothing to set", file=sys.stderr)
        return 2

    cap = int(knob_default("summary_max_chars"))
    text = args.path.read_text(encoding="utf-8")
    doc = yaml.safe_load(text)
    if not isinstance(doc, dict):
        print(f"not a mapping: {args.path}", file=sys.stderr)
        return 1

    if args.summary is not None:
        s = args.summary.strip()
        if not s or len(s) > cap:
            print(f"summary len={len(s)} cap={cap} REJECT", file=sys.stderr)
            return 1
        # soft check: no mid-truncation ellipsis
        if s.endswith("…") or s.endswith("..."):
            print("summary ends with ellipsis — rewrite, do not truncate", file=sys.stderr)
            return 1
        doc["summary"] = s

    if args.clear_grain:
        doc.pop("grain", None)
    elif args.grain is not None:
        g = args.grain.strip()
        if g:
            doc["grain"] = g

    args.path.write_text(
        yaml.dump(
            doc,
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False,
            width=1000,
        ),
        encoding="utf-8",
    )
    print(f"ok {args.path} summary_len={len(doc.get('summary', ''))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
