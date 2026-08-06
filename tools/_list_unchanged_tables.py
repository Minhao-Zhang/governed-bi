"""List authored tables whose summary still matches gold."""

from __future__ import annotations

import pathlib

import yaml

GOLD = pathlib.Path("corpora/gold-semantic-layer-20260804")
AUTH = pathlib.Path("corpora/_variant-authored-20260805")


def main() -> None:
    d = yaml.safe_load((AUTH / "mondial_geo" / "mondial_geo.yaml").read_text(encoding="utf-8"))
    print("mondial_geo schema len", len(d["summary"]))
    print(d["summary"])
    print("--- unchanged tables ---")
    same: list[str] = []
    for schema_dir in sorted(AUTH.iterdir()):
        if not schema_dir.is_dir() or schema_dir.name.startswith("_"):
            continue
        tdir = schema_dir / "tables"
        if not tdir.exists():
            continue
        for tp in sorted(tdir.glob("*.yaml")):
            a = yaml.safe_load(tp.read_text(encoding="utf-8")) or {}
            gp = GOLD / schema_dir.name / "tables" / tp.name
            g = yaml.safe_load(gp.read_text(encoding="utf-8")) if gp.exists() else {}
            if a.get("summary") == (g or {}).get("summary"):
                same.append(f"{schema_dir.name}/{tp.name} | {a.get('summary', '')[:100]}")
    print("count", len(same))
    for s in same:
        print(s)


if __name__ == "__main__":
    main()
