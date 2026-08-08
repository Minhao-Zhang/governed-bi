"""Does a corpus tree obey ADR 0005's field spec? Exit 1 if not.

Two modes. ``--file`` checks one asset file and is what the rebuild loop calls after each
write; the default walks a whole tree and prints a per-rule report. Rules that need the whole
corpus or an external file are reported as **not evaluated** in ``--file`` mode rather than
passed, because a rule that silently skips is worse than one that fails.

Why this exists: the corpus shipped today passes both rules the Pydantic model enforces
(``1 <= len(summary) <= 250``, identifier present) and violates most of what the ADR says in
prose -- 100% of one arm's schema/table/column summaries are identifier lists, 0/928 joins
carry a ``body``, 441 of 949 terms drop an alias the retrieval bridge depends on. Prose rules
that nothing executes are not rules.

Reads raw YAML rather than ``corpus.store.load``: this must give a useful answer on a
half-written tree, where the loader would raise.

``identifier_fields`` comes from ``ASSET_REGISTER`` and is not restated here. Two spellings of
one policy is how ``airline."Air Carriers"`` ended up with no table asset while 24 few-shots
cited it.

**On reading the held-out split (V12).** This tool loads ``test_final.jsonl`` to *forbid* its
text. That is the opposite of the defect it guards: tuning a corpus against the split adapts to
it, while refusing content that appears in it cannot. Nothing here writes an asset.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

import yaml

from governed_bi.register.assets import ASSET_REGISTER, AssetType

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CORPUS = ROOT.parent / "BIRD-corpus"
DEFAULT_DATASET = ROOT.parent / "BIRD-Data-Obfuscation" / "eval_dataset"

#: The sentinel the scaffold writes. It must fail, so an unfinished asset cannot ship.
SENTINEL = "TODO"

#: Per-type ``summary`` cap. 400 for a schema because it is the routing signal for a whole
#: database and is the one place the cap measurably binds (mean 220.8 chars on the richest
#: corpus written so far, against 154.4 for tables and 99.8 for columns). Provisional: the cap
#: is a retrieval parameter, so ``tools/routing_recall.py`` settles it, not taste.
SUMMARY_CAP: dict[AssetType, int] = {t: 400 if t is AssetType.schema else 250 for t in AssetType}

#: Every type's §1.2 entry names a ``body``, and ``summary`` never reaches the model
#: (``serve/context.py``), so an empty one delivers nothing but the structural line.
BODY_REQUIRED: frozenset[AssetType] = frozenset(AssetType)

#: Function words. The ratio separates a sentence from an identifier roster; the corpus's own
#: densifier names "a function-word ratio of 0.00" as the symptom, and every templated summary
#: measured so far sits at exactly 0.000.
FUNCTION_WORDS = frozenset(
    "a an the of in on for to from by with at as is are was were be been that which who whose "
    "this these those and or not its it their there each per into over under between within "
    "about across than then when where while has have had do does no any all both".split()
)
MIN_FUNCTION_RATIO = 0.10

#: Shapes produced by the generators that wrote the corpus we are replacing.
TEMPLATES: tuple[re.Pattern[str], ...] = (
    re.compile(r"^\s*\S+\s+[—-]\s+\S+\.\S+\s*$"),          # zip_code — alias.zip_code
    re.compile(r"^\s*(\S+)\s*\(\S+\)\s*:\s*\S+\s*,"),      # Address (Address): a, b, c
    re.compile(r"^[^:]{1,60}:\s*\d+\s+tables?\s+[—-]"),    # addr: 9 tables — a, b
)

#: ``body``'s job, per §1.2. In ``summary`` it spends the retrieval budget on text the model
#: never sees.
VALUE_TALK = re.compile(r"(\be\.g\.|\bfor example\b|\bsuch as\b|\bcoded as\b|'[^']{1,60}')", re.I)

#: The mechanical identifier suffix. Satisfies the identifier rule without being a sentence.
PAREN_TAIL = re.compile(r"\((?:column|table)\s+\S+\)\s*\.?\s*$", re.I)

#: Never disclose how an unreliable column came to be unreliable. Saying it is unreliable is
#: governance; saying it was fabricated to imitate another column is a description of the
#: benchmark, and naming that other column makes this one rank for its questions.
FORBIDDEN_WORDS = ("decoy", "trap", "fabricated", "synthetic", "planted", "mimic", "imitat")

#: ``Means 'x' (obfuscated to 'x')`` -- 42% of column bodies in both existing corpora.
TAUTOLOGY_BODY = re.compile(r"^(physical column\s+'[^']*'\.\s*)?means\s+['\"]", re.I)

#: Per-file byte caps. Not one number: a table file carries every one of its columns inline,
#: so a 73-column table is legitimately large, while a few-shot is one question and one query
#: and anything bigger is a materialised result set. The corpus being replaced holds 15 assets
#: over 80,000 bytes -- one of them 5.1 MB, a ``VALUES`` list harvested from a constant-answer
#: gold query -- and those 15 are half its bytes. Both caps sit under ``context_budget_chars``
#: (80,000), because a single asset that cannot fit in the context block is not deliverable.
FILE_CAP: dict[str, int] = {"few_shot": 4_000, "*": 32_000}

_ALNUM = re.compile(r"[^a-z0-9]")
_WORD = re.compile(r"[A-Za-z][A-Za-z'-]*")


def _closed() -> None:
    """Import-time: every asset type has a cap and a body decision."""
    missing = [t.value for t in AssetType if t not in SUMMARY_CAP]
    if missing:  # pragma: no cover - import-time guard
        raise AssertionError(f"SUMMARY_CAP is missing {missing}; a new type needs a decision")


_closed()


class Finding(str):
    """One violation line. A ``str`` so the report can just sort them."""


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _where(kind: str, asset: dict[str, Any], path: Path) -> str:
    """``file:asset`` for a finding. Inline columns carry no ``id`` in YAML — the loader
    derives it — so without ``physical_name`` here every column in a table reports as
    ``:column`` and the writer cannot tell which one failed."""
    label = asset.get("id") or (_text(asset.get("physical_name")) if kind == "column" else "") or kind
    return f"{path.name}:{label}"


def _norm(value: str) -> str:
    return _ALNUM.sub("", value.lower())


def function_ratio(summary: str) -> float:
    words = _WORD.findall(summary.lower())
    return sum(1 for w in words if w in FUNCTION_WORDS) / len(words) if words else 0.0


def is_prose(summary: str) -> bool:
    return not any(t.match(summary) for t in TEMPLATES) and function_ratio(summary) >= MIN_FUNCTION_RATIO


def load_assets(path: Path) -> list[tuple[str, dict[str, Any], Path]]:
    """``(asset_type, mapping, file)`` for one YAML file, columns unpacked from their table."""
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as err:
        return [("<unparseable>", {"_error": str(err)}, path)]
    if not isinstance(doc, dict):
        return [("<unparseable>", {"_error": "top level is not a mapping"}, path)]
    out = [(str(doc.get("asset_type") or "<missing>"), doc, path)]
    if doc.get("asset_type") == "table":
        for col in doc.get("columns") or []:
            if isinstance(col, dict):
                # An inline column carries no ``schema`` of its own -- the loader derives it
                # from the table (``corpus/store.py``). Copying it here is what lets V11 key
                # on ``(db, physical_name)``; without it that rule silently matched nothing
                # and reported a clean corpus that names the column each decoy resembles.
                out.append(("column", {"schema": doc.get("schema"), **col}, path))
    return out


def walk(root: Path) -> list[tuple[str, dict[str, Any], Path]]:
    found: list[tuple[str, dict[str, Any], Path]] = []
    for p in sorted(root.rglob("*.yaml")):
        if ".git" in p.parts:
            continue
        found.extend(load_assets(p))
    return found


# ── the rules ─────────────────────────────────────────────────────────────────


def check_local(kind: str, a: dict[str, Any], where: str) -> dict[str, list[Finding]]:
    """Every rule answerable from one asset alone."""
    out: dict[str, list[Finding]] = defaultdict(list)
    if kind == "<unparseable>":
        out["V0"].append(Finding(f"{where}: {a.get('_error')}"))
        return out
    try:
        at = AssetType(kind)
    except ValueError:
        out["V0"].append(Finding(f"{where}: unknown asset_type {kind!r}"))
        return out

    summary = _text(a.get("summary"))
    body = _text(a.get("body"))
    cap = SUMMARY_CAP[at]

    if not summary:
        out["V1"].append(Finding(f"{where}: summary is empty (build_index raises on this)"))
    elif len(summary) > cap:
        out["V1"].append(Finding(f"{where}: summary is {len(summary)} chars, cap {cap} for {kind}"))

    if summary == SENTINEL or summary.startswith(SENTINEL):
        out["V2"].append(Finding(f"{where}: summary is still the scaffold sentinel"))

    for field in ASSET_REGISTER[at].identifier_fields:
        raw = a.get(field)
        ident = str(raw).rsplit(".", 1)[-1] if raw else ""
        if not ident:
            out["V3"].append(Finding(f"{where}: {field} is unset, so the identifier rule cannot hold"))
        elif ident not in summary:
            out["V3"].append(Finding(f"{where}: summary omits {field}={ident!r}"))

    if summary and summary != SENTINEL and not is_prose(summary):
        out["V4"].append(
            Finding(f"{where}: summary is not prose (function-word ratio "
                    f"{function_ratio(summary):.2f}): {summary[:70]!r}")
        )

    # A few-shot's summary *is* the question (ADR 0005 §1.2), and questions legitimately quote
    # the values they ask about. Policing value-talk there would reject the spec.
    if at is not AssetType.few_shot and VALUE_TALK.search(summary):
        out["V5"].append(Finding(f"{where}: summary carries values or examples, which belong in body"))
    if PAREN_TAIL.search(summary):
        out["V5"].append(Finding(f"{where}: summary ends in a mechanical '(column x)' tail"))

    if at in BODY_REQUIRED and not body:
        out["V6"].append(Finding(f"{where}: {kind} has no body, and the model never sees summary"))

    if at is AssetType.column and body:
        name = _text(a.get("physical_name"))
        if TAUTOLOGY_BODY.match(body):
            out["V7"].append(Finding(f"{where}: body is the tautology \"Means 'x'\""))
        elif name and _norm(body) == _norm(name):
            out["V7"].append(Finding(f"{where}: body only restates the column name"))

    if at is AssetType.term:
        for syn in a.get("synonyms") or []:
            if str(syn).lower() not in summary.lower():
                out["V8"].append(
                    Finding(f"{where}: synonym {syn!r} is not in summary, so it is unreachable "
                            "(only summary is indexed)")
                )

    blob = " ".join(
        [summary, body, *(str(r) for r in (a.get("rules") or [])),
         _text((a.get("reliability") or {}).get("note") if isinstance(a.get("reliability"), dict) else "")]
    ).lower()
    # V10 governs what the *writer* discloses. A few-shot is a verbatim train question and its
    # gold SQL, harvested by script and never authored, so a film called "The Trap" is not a
    # disclosure. If a few-shot ever becomes agent-written this exemption has to go.
    for word in () if at is AssetType.few_shot else FORBIDDEN_WORDS:
        if word in blob:
            out["V10"].append(Finding(f"{where}: text contains {word!r}"))
            break
    return out


def check_references(assets: Iterable[tuple[str, dict[str, Any], Path]]) -> list[Finding]:
    """V9 -- every declared reference resolves. Whole-corpus only."""
    ids = {str(a.get("id")) for kind, a, _ in assets if a.get("id")}
    for kind, a, _ in assets:
        if kind == "table":
            tid = str(a.get("id") or "")
            for col in a.get("columns") or []:
                if isinstance(col, dict) and col.get("physical_name"):
                    ids.add(f"{tid}.{col['physical_name']}")
    bad: list[Finding] = []
    for kind, a, path in assets:
        where = _where(kind, a, path)
        targets: list[tuple[str, Any]] = []
        if kind == "term" and isinstance(a.get("binding"), dict):
            targets.append(("binding.target_id", a["binding"].get("target_id")))
        if kind == "metric":
            targets.append(("base_table", a.get("base_table")))
        if kind == "join":
            targets += [("left_table", a.get("left_table")), ("right_table", a.get("right_table"))]
        for field, target in targets:
            if target and str(target) not in ids:
                bad.append(Finding(f"{where}: {field}={target!r} resolves to no asset"))
    return bad


def check_suspect_summaries(assets, trap_manifest: Path) -> list[Finding]:
    """V11 -- a suspect column's summary must not carry the vocabulary of what it resembles.

    A retrieval rule, not a disclosure one: ``summary`` is the index, so a caveat naming the
    real column makes the unreliable one rank for that column's questions and compete for the
    same 30-column budget.
    """
    traps = json.loads(trap_manifest.read_text(encoding="utf-8"))
    resembles: dict[tuple[str, str], str] = {
        (t["db"], t["names"]["rename"]): t["source_column"] for t in traps if t.get("names")
    }
    bad: list[Finding] = []
    for kind, a, path in assets:
        if kind != "column":
            continue
        rel = a.get("reliability")
        status = (rel or {}).get("status") if isinstance(rel, dict) else None
        if str(getattr(status, "value", status)) != "suspect":
            continue
        db, name = _text(a.get("schema")), _text(a.get("physical_name"))
        source = resembles.get((db, name))
        summary = _text(a.get("summary"))
        if source and source != name and re.search(rf"\b{re.escape(source)}\b", summary):
            bad.append(
                Finding(f"{_where(kind, a, path)}: summary of a suspect column names {source!r}, "
                        "which makes it rank for that column's questions")
            )
    return bad


def check_loadable(paths: Iterable[Path]) -> list[Finding]:
    """V14 -- the engine can actually load the file.

    The other rules read raw YAML so they still answer on a half-written tree, and that leaves
    a hole: a file with a valid ``asset_type`` and a well-formed summary can still be rejected
    by the model. The first scaffold wrote ``provenance.source: introspection``, which is not
    one of the four the enum allows; every text rule passed and the loader returned zero assets
    from 18 files.
    """
    from governed_bi.corpus.store import load_file

    bad: list[Finding] = []
    for path in paths:
        _, problems = load_file(path)
        for problem in problems:
            bad.append(Finding(f"{path.name}: {getattr(problem, 'reason', problem)}"))
    return bad


def check_split_leak(assets, test_split: Path) -> list[Finding]:
    """V12 -- no asset quotes a held-out question."""
    questions = {
        " ".join(json.loads(line).get("question", "").lower().split())
        for line in test_split.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }
    questions.discard("")
    bad: list[Finding] = []
    for kind, a, path in assets:
        blob = " ".join(" ".join(str(a.get(f) or "").lower().split()) for f in ("summary", "body"))
        for q in questions:
            if len(q) > 25 and q in blob:
                bad.append(Finding(f"{_where(kind, a, path)}: quotes a test-split question"))
                break
    return bad


RULES: dict[str, str] = {
    "V0": "the file parses and declares a known asset_type",
    "V1": "1 <= len(summary) <= cap (400 schema, else 250)",
    "V2": "summary is not the scaffold sentinel",
    "V3": "summary contains the identifier ASSET_REGISTER declares",
    "V4": "summary is prose, not a template or an identifier roster",
    "V5": "summary carries no values, examples or '(column x)' tail",
    "V6": "a type whose spec names a body has a non-empty one",
    "V7": "a column body is not a tautology",
    "V8": "a term's summary contains every one of its synonyms",
    "V9": "every declared reference resolves to a real asset",
    "V10": "no text discloses how an unreliable column was made",
    "V11": "a suspect column's summary omits the column it resembles",
    "V12": "no asset quotes a held-out question",
    "V13": "no file exceeds its byte cap (few_shot 4k, else 32k)",
    "V14": "the engine's loader accepts the file",
}
WHOLE_TREE_ONLY = ("V9", "V11", "V12")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="check_corpus_conformance", description=__doc__)
    ap.add_argument("--corpus-dir", type=Path, default=DEFAULT_CORPUS)
    ap.add_argument("--file", type=Path, default=None, help="check one asset file (rebuild loop)")
    ap.add_argument("--trap-manifest", type=Path, default=DEFAULT_DATASET / "trap_manifest.json")
    ap.add_argument("--test-split", type=Path, default=DEFAULT_DATASET / "test_final.jsonl")
    ap.add_argument("--max-lines", type=int, default=15, help="findings printed per rule")
    args = ap.parse_args(argv)

    if args.file:
        assets = load_assets(args.file)
        scope, whole = f"{args.file}", False
    else:
        if not args.corpus_dir.is_dir():
            print(f"no corpus at {args.corpus_dir}", file=sys.stderr)
            return 2
        assets = walk(args.corpus_dir)
        scope, whole = f"{args.corpus_dir} ({len(assets)} assets)", True

    findings: dict[str, list[Finding]] = defaultdict(list)
    for kind, a, path in assets:
        where = _where(kind, a, path)
        for rule, lines in check_local(kind, a, where).items():
            findings[rule].extend(lines)

    findings["V14"].extend(check_loadable(sorted({p for _, _, p in assets})))

    kind_of_file: dict[Path, str] = {}
    for kind, _, p in assets:
        kind_of_file.setdefault(p, kind)
    for p, kind in sorted(kind_of_file.items()):
        cap = FILE_CAP.get(kind, FILE_CAP["*"])
        if p.exists() and p.stat().st_size > cap:
            findings["V13"].append(
                Finding(f"{p.name}: {p.stat().st_size:,} bytes, cap {cap:,} for {kind}")
            )

    skipped: dict[str, str] = {}
    if whole:
        findings["V9"].extend(check_references(assets))
        if args.trap_manifest.exists():
            findings["V11"].extend(check_suspect_summaries(assets, args.trap_manifest))
        else:
            skipped["V11"] = f"no trap manifest at {args.trap_manifest}"
        if args.test_split.exists():
            findings["V12"].extend(check_split_leak(assets, args.test_split))
        else:
            skipped["V12"] = f"no test split at {args.test_split}"
    else:
        for rule in WHOLE_TREE_ONLY:
            skipped[rule] = "needs the whole tree"

    by_type = Counter(kind for kind, _, _ in assets)
    print(f"corpus conformance: {scope}")
    if whole:
        print(f"  {dict(sorted(by_type.items()))}")
    print(f"  {'rule':<5}{'violations':>12}  description")
    total = 0
    for rule, description in RULES.items():
        if rule in skipped:
            print(f"  {rule:<5}{'not evaluated':>12}  {description}  [{skipped[rule]}]")
            continue
        n = len(findings.get(rule, ()))
        total += n
        print(f"  {rule:<5}{n:>12}  {description}")

    if total:
        print(f"\n{total} violation(s):", file=sys.stderr)
        for rule in RULES:
            lines = findings.get(rule, [])
            if not lines:
                continue
            print(f"\n  [{rule}] {RULES[rule]} — {len(lines)}", file=sys.stderr)
            for line in sorted(lines)[: args.max_lines]:
                print(f"    {line}", file=sys.stderr)
            if len(lines) > args.max_lines:
                print(f"    … {len(lines) - args.max_lines} more", file=sys.stderr)
        return 1

    unevaluated = f"; {len(skipped)} rule(s) not evaluated" if skipped else ""
    print(f"\nall evaluated rules pass{unevaluated}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
