"""Compare two snapshots from ``tools/pivot_snapshot.py`` against a file of expected differences.

Every difference is printed, one line per (row, field). A difference is *expected* only when the
expected-differences file names its row, field and reason; anything else is *unexpected*, and the
exit code is 1 if there is one. Exit 0 means every difference was listed. Exit 2 means a file
could not be read.

Facet rewriting is off in the snapshot, and it uses the deterministic embedder and no agent model.
So a match proves that routing, retrieval, join planning, context rendering and the SQL checks did
not move. It cannot prove anything about the production embedder, the facet rewriters, the prompts,
the agent loop, abstention, clarification or the UI; those rest on the test suite and the baseline.

The expected-differences file is TOML, written before the run::

    [[expected]]
    kind   = "statement"
    ids    = ["case:benign_authz_a", "case:benign_authz_b"]   # or: id = "case:benign_authz_a"
    field  = "<row>"                  # a field name, "*" for any field, or "<row>" for a row
                                      # present in only one snapshot
    reason = "grant cases are deleted with access.py"

    [[rename]]                        # a renamed field: base rows are read under the new name,
    kind = "question"                 # and the value must still match
    from = "licensed"
    to   = "retrieved_tables"

``reason`` is required: a difference with no stated reason is the thing this tool exists to refuse.
An expected entry that matches no difference is reported as ``NOT SEEN`` and does not fail the
run, because the listed difference simply did not happen.

Usage::

    uv run --frozen python tools/pivot_compare.py BASE.jsonl NEW.jsonl [--expected FILE]
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import tomllib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

ROW = "<row>"
_SHOW = 160

Key = tuple[str, str]


class _Absent:
    """A field present in one snapshot and not the other, which ``None`` must not stand for."""

    def __repr__(self) -> str:
        return "<absent>"


_ABSENT = _Absent()


@dataclass(frozen=True, slots=True)
class Expectation:
    kind: str
    ids: frozenset[str]
    field: str
    reason: str


@dataclass(frozen=True, slots=True)
class Rename:
    kind: str | None
    old: str
    new: str


@dataclass(slots=True)
class Difference:
    key: Key
    field: str
    base: Any
    new: Any
    reason: str | None = None


@dataclass(slots=True)
class Comparison:
    differences: list[Difference] = field(default_factory=list)
    unseen: list[Expectation] = field(default_factory=list)

    @property
    def unexpected(self) -> list[Difference]:
        return [d for d in self.differences if d.reason is None]


def read_snapshot(path: pathlib.Path) -> dict[Key, dict[str, Any]]:
    rows: dict[Key, dict[str, Any]] = {}
    with path.open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            key = (str(row["kind"]), str(row["id"]))
            if key in rows:
                raise ValueError(f"{path}:{number}: duplicate row {key}")
            rows[key] = row
    return rows


def read_expected(path: pathlib.Path | None) -> tuple[list[Expectation], list[Rename]]:
    if path is None:
        return [], []
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    expected = []
    for entry in data.get("expected", ()):
        ids = {str(entry["id"])} if "id" in entry else {str(i) for i in entry.get("ids", ())}
        reason = str(entry.get("reason", "")).strip()
        if not ids or not reason or "kind" not in entry or "field" not in entry:
            raise ValueError(f"{path}: an [[expected]] needs kind, id or ids, field and reason: {entry}")
        expected.append(Expectation(str(entry["kind"]), frozenset(ids), str(entry["field"]), reason))
    renames = [
        Rename(entry.get("kind"), str(entry["from"]), str(entry["to"]))
        for entry in data.get("rename", ())
    ]
    return expected, renames


def _renamed(row: Mapping[str, Any], renames: Iterable[Rename]) -> dict[str, Any]:
    out = dict(row)
    for rename in renames:
        if rename.kind in (None, row["kind"]) and rename.old in out:
            out[rename.new] = out.pop(rename.old)
    return out


def compare(
    base: Mapping[Key, Mapping[str, Any]],
    new: Mapping[Key, Mapping[str, Any]],
    expected: Sequence[Expectation] = (),
    renames: Sequence[Rename] = (),
) -> Comparison:
    result = Comparison()
    for key in sorted(set(base) | set(new)):
        if key not in new:
            result.differences.append(Difference(key, ROW, "present", "missing"))
            continue
        if key not in base:
            result.differences.append(Difference(key, ROW, "missing", "present"))
            continue
        old_row, new_row = _renamed(base[key], renames), dict(new[key])
        for name in sorted(set(old_row) | set(new_row)):
            if old_row.get(name, _ABSENT) != new_row.get(name, _ABSENT):
                result.differences.append(
                    Difference(key, name, old_row.get(name, _ABSENT), new_row.get(name, _ABSENT))
                )

    used: set[int] = set()
    for difference in result.differences:
        for index, entry in enumerate(expected):
            if _matches(entry, difference):
                difference.reason = entry.reason
                used.add(index)
                break
    result.unseen = [entry for index, entry in enumerate(expected) if index not in used]
    return result


def _matches(entry: Expectation, difference: Difference) -> bool:
    kind, row_id = difference.key
    field_ok = entry.field == difference.field or (entry.field == "*" and difference.field != ROW)
    return entry.kind == kind and row_id in entry.ids and field_ok


def _show(value: Any) -> str:
    text = repr(value) if isinstance(value, _Absent) else json.dumps(value, sort_keys=True)
    return text if len(text) <= _SHOW else text[: _SHOW - 3] + "..."


def report_lines(result: Comparison) -> list[str]:
    lines = []
    for d in result.differences:
        tag = "UNEXPECTED" if d.reason is None else "expected  "
        why = "" if d.reason is None else f"  [{d.reason}]"
        lines.append(f"{tag} {d.key[0]} {d.key[1]} {d.field}: {_show(d.base)} -> {_show(d.new)}{why}")
    lines += [
        f"NOT SEEN   {e.kind} {sorted(e.ids)} {e.field}: {e.reason}" for e in result.unseen
    ]
    lines.append(
        f"{len(result.differences)} differences, {len(result.differences) - len(result.unexpected)} "
        f"expected, {len(result.unexpected)} unexpected, {len(result.unseen)} expected entries not seen"
    )
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("base", type=pathlib.Path, help="the snapshot of the commit branched from")
    parser.add_argument("new", type=pathlib.Path, help="the snapshot of this change")
    parser.add_argument("--expected", type=pathlib.Path, default=None, help="expected differences (TOML)")
    args = parser.parse_args(argv)
    try:
        base, new = read_snapshot(args.base), read_snapshot(args.new)
        expected, renames = read_expected(args.expected)
    except (OSError, ValueError, KeyError, tomllib.TOMLDecodeError) as err:
        print(f"cannot read: {type(err).__name__}: {err}", file=sys.stderr)
        return 2
    result = compare(base, new, expected, renames)
    for line in report_lines(result):
        print(line)
    return 1 if result.unexpected else 0


if __name__ == "__main__":
    raise SystemExit(main())
