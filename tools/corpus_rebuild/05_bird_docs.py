"""Stage BIRD's own column documentation against the obfuscated identifiers.

BIRD ships ``database_description/<table>.csv`` per database, and its columns line up with
ADR 0005's field spec almost one for one:

    column_name + column_description  ->  what the column IS      (summary material)
    value_description + data_format   ->  the value domain        (body material)

Nothing in the engine has ever read these files. Writes ``_build/bird_docs.jsonl``.

**Set expectations.** 515 CSVs, 3,663 rows, 2,542 distinct ``(db, column)`` pairs against 4,596
real columns; ``column_description`` is present on 91.5% of the rows it covers but its median
length is **28 characters**, and 9% merely restate the column name ("the title of the movie").
This is a starting point, not a description. The agent writes; it does not transcribe.

Encoding: these CSVs are a mix of UTF-8 and cp1252, and a file that decodes under neither is
reported rather than skipped silently — a schema quietly missing its documentation is a schema
the writer invents from nothing. **The list was `("utf-8-sig", "cp1252", "latin-1")` and that
made the promise vacuous:** latin-1 maps all 256 byte values, so it never raises, `_decode` could
never return `None`, and a file in any other encoding was staged as mojibake at exit 0. cp1252
already covers latin-1 apart from five undefined bytes, and a CSV containing one of those is not
text in either encoding — which is exactly the case worth reporting.

    uv run python tools/corpus_rebuild/05_bird_docs.py
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _common as C  # noqa: E402

#: No byte-preserving fallback on purpose — see the module docstring. A codec that cannot fail
#: turns the undecodable report into dead code.
ENCODINGS = ("utf-8-sig", "cp1252")
_ALNUM = re.compile(r"[^a-z0-9]")


def _decode(path: Path) -> str | None:
    raw = path.read_bytes()
    for encoding in ENCODINGS:
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=C.BUILD / "bird_docs.jsonl")
    args = ap.parse_args(argv)

    wanted = set(C.evaluated_schemas())
    rename = C.rename_map()
    rows: list[dict[str, object]] = []
    undecodable: list[str] = []
    stat: Counter[str] = Counter()

    for db in sorted(wanted):
        mapping = rename.get(db, {})
        for path in sorted(C.DATASET.glob(f"data/*/*_databases/{db}/database_description/*.csv")):
            text = _decode(path)
            if text is None:
                undecodable.append(str(path))
                continue
            original_table = path.stem
            for raw in csv.DictReader(io.StringIO(text)):
                item = {(k or "").strip().lower(): (v or "").strip() for k, v in raw.items()}
                original = item.get("original_column_name")
                if not original:
                    continue
                stat["rows"] += 1
                description = item.get("column_description", "")
                nl_name = item.get("column_name", "")
                value_description = item.get("value_description", "")
                stat["with_description"] += bool(description)
                stat["with_value_description"] += bool(value_description)
                if description and _ALNUM.sub("", description.lower()) == _ALNUM.sub("", original.lower()):
                    stat["description_restates_the_name"] += 1
                rows.append(
                    {
                        "db": db,
                        "table": mapping.get(original_table, original_table),
                        "column": mapping.get(original, original),
                        "original_table": original_table,
                        "original_column": original,
                        "nl_name": nl_name,
                        "description": description,
                        "data_format": item.get("data_format", ""),
                        "value_description": value_description,
                    }
                )

    rows.sort(key=lambda r: (r["db"], r["table"], r["column"]))
    written = C.write_jsonl(args.out, rows)
    print(f"{written} documented columns into {args.out}")
    for key in ("with_description", "with_value_description", "description_restates_the_name"):
        print(f"  {key:<32}{stat[key]:>6}  ({stat[key]/max(stat['rows'],1):.1%})")
    # Written before the undecodable check: the stats describe what *was* staged, and they are
    # most useful on the run that reported a problem. Returning first skipped them exactly then.
    (C.BUILD / "bird_docs_stats.json").write_text(
        json.dumps(dict(stat), indent=2, sort_keys=True), encoding="utf-8", newline="\n"
    )
    if undecodable:
        print(f"  UNDECODABLE, not staged: {undecodable}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
