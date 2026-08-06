"""Inspect unbound terms and nearby assets to choose bindings."""

from __future__ import annotations

import pathlib

import yaml

AUTH = pathlib.Path("corpora/_variant-authored-20260805")


def main() -> None:
    unbound = []
    for p in sorted(AUTH.rglob("terms/*.yaml")):
        d = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        if d.get("asset_type") == "term" and not d.get("binding"):
            unbound.append((p, d))

    print(f"unbound={len(unbound)}")
    for p, d in unbound:
        schema = p.parts[-3]
        print("\n===", p.relative_to(AUTH))
        print("summary:", d.get("summary"))
        print("body:", (d.get("body") or "")[:200])
        # list tables/columns briefly
        tdir = AUTH / schema / "tables"
        if tdir.exists():
            for tp in sorted(tdir.glob("*.yaml")):
                td = yaml.safe_load(tp.read_text(encoding="utf-8")) or {}
                cols = [c.get("physical_name") for c in (td.get("columns") or [])]
                print(
                    f"  table {td.get('physical_name')} cols={cols[:12]}{'...' if len(cols)>12 else ''}"
                )
        mdir = AUTH / schema / "metrics"
        if mdir.exists():
            for mp in sorted(mdir.glob("*.yaml"))[:8]:
                md = yaml.safe_load(mp.read_text(encoding="utf-8")) or {}
                print(f"  metric {md.get('id')}")


if __name__ == "__main__":
    main()
