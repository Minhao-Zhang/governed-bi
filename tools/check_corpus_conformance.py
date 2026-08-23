"""Does a corpus tree obey ADR 0005's field spec? Exit 1 if not.

Three modes. ``--file`` checks one asset file and is what the rebuild loop calls after each
write; the default walks a whole tree and prints a per-rule report. Rules that need the whole
corpus or an external file are reported as **not evaluated** in ``--file`` mode rather than
passed, because a rule that silently skips is worse than one that fails.

Why this exists: the corpus this kit replaced (measured 2026-08-08) passed both rules the
Pydantic model enforces (``1 <= len(summary) <= 250``, identifier present) and violated most of
what the ADR says in prose -- 100% of one arm's schema/table/column summaries were identifier
lists, 0/928 joins carried a ``body``, 441 of 949 terms dropped an alias the retrieval bridge
depends on. Prose rules that nothing executes are not rules.

Reads raw YAML rather than ``corpus.store.load``: this must give a useful answer on a
half-written tree, where the loader would raise.

``identifier_fields`` comes from ``ASSET_REGISTER`` and is not restated here. Two spellings of
one policy is how ``airline."Air Carriers"`` ended up with no table asset while 24 few-shots
cited it.

``--json`` emits the findings and **exits 0**, because it is an inventory rather than a gate:
``tools/check_ratchet.py`` is what decides whether the inventory is allowed. A mode that both
reported and failed would make the ratchet unable to read a tree that has findings, which is every
tree it exists for.

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

# The 2026-08-23 rules, split out at the 1,000-line cap. Predicates only -- this module
# still owns the RULES table, the report and the exit codes.
#
# `Finding`, `_text` and `_where` come from there too, and the direction matters: the
# predicates need them and so does the report, so leaving them here would make the import
# circular and copying them would make two `Finding` types that compare unequal.
from conformance_rules_metric_and_content import (  # noqa: E402 - sibling script, path-added
    Finding,
    _text,
    _where,
    check_excluded_not_named,
    check_guard_rules,
    check_metric_bindings,
    check_metric_expression,
    check_unique_ids,
)

from governed_bi.corpus.identity import derive_column_id
from governed_bi.register.assets import ASSET_REGISTER, AssetType
from governed_bi.register.knobs import knob_default

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CORPUS = ROOT.parent / "BIRD-corpus"
DEFAULT_DATASET = ROOT.parent / "BIRD-Data-Obfuscation" / "eval_dataset"

#: The sentinel the scaffold writes. It must fail, so an unfinished asset cannot ship.
SENTINEL = "TODO"

#: ``summary`` cap, **read from the knob rather than restated**. It is one global value for
#: every type (``knobs.py`` `summary_max_chars`, enforced in ``corpus/validate.py``), and this
#: file first hard-coded 400 for schema assets on the argument that the routing signal for a
#: whole database deserves more room. It does not get more room: V1 passed at 340 characters
#: and the model rejected the file, so the writer met a validator that was more permissive than
#: the thing it validates. Raising it is a knob change and a treatment change, not a cap here.
SUMMARY_CAP: dict[AssetType, int] = {t: int(knob_default("summary_max_chars")) for t in AssetType}

#: Every type's §1.2 entry names a ``body``, and ``summary`` never reaches the model
#: (``serve/context.py``), so an empty one delivers nothing but the structural line.
BODY_REQUIRED: frozenset[AssetType] = frozenset(AssetType)

#: Function words. The ratio separates a sentence from an identifier roster; the corpus's own
#: densifier names "a function-word ratio of 0.00" as the symptom, and every templated summary
#: measured so far sits at exactly 0.000.
FUNCTION_WORDS = frozenset(
    "a an the of in on for to from by with at as is are was were be been that which who whose "
    "this these those and or not its it their there each per into over under between within "
    "about across than then when where while has have had do does no any all both "
    # Prepositions and subordinators the first list missed. "a line item's price *before*
    # discount" scored 0.083 and failed V4 -- correct English rejected because the checker did
    # not know a preposition. Closed-class words only: nothing here lets an identifier roster
    # through, and every one of them is what prose uses to relate two nouns.
    "before after during without against above below behind beyond along among around toward "
    "towards onto upon out up down off near via if because unless whether until since although "
    "though but also".split()
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
#: The quoted-literal arm needs the lookarounds: without them ``the restaurant's name and the
#: city's rating`` reads as a quoted literal spanning the two possessives, and a writer ends up
#: deleting correct English to satisfy a false positive.
VALUE_TALK = re.compile(
    r"(\be\.g\.|\bfor example\b|\bsuch as\b|\bcoded as\b|(?<![A-Za-z])'[^']{1,60}'(?![A-Za-z]))",
    re.I,
)

#: The mechanical identifier suffix. Satisfies the identifier rule without being a sentence.
PAREN_TAIL = re.compile(r"\((?:column|table)\s+\S+\)\s*\.?\s*$", re.I)

#: Never disclose how an unreliable column came to be unreliable. Saying it is unreliable is
#: governance; saying it was fabricated to imitate another column is a description of the
#: benchmark, and naming that other column makes this one rank for its questions.
#: Matched at a word boundary, not as a bare substring: `imitat` was catching `limitations`
#: (l-IMITAT-ions), which is ordinary vocabulary for a data caveat, and `trap`/`planted` would
#: have caught `trapezoid`/`transplanted` the same way. The stems stay stems so `imitates`,
#: `imitating` and `fabricated` are all still caught.
#:
#: `offside trap` is exempt because it is a *value*, not a description of the benchmark:
#: `european_football_2.atributos_equipo.clase_linea_defensores` holds exactly two literals,
#: `Cover` and `Offside Trap`. Censoring it cost the body the one string a query needs, and the
#: writer who hit this described the setting behaviourally instead -- a corpus made worse by a
#: rule that was never aimed at it. The exemption is deliberately narrow: only this bigram, so
#: `trap` on its own still fails.
#:
#: `steam trap` joins it for the same reason, from the second corpus to hit this rule. A steam
#: trap is a real piece of building equipment and `maximo_active_assets.class_description` holds
#: `STEAM TRAP` as its third most common value (2,541 assets) -- so this is a value in an
#: enumerated domain, not a description of how a column was made. The alternative was deleting
#: the one string that lets "how many steam traps are there" retrieve the asset register, which
#: is the failure mode the `offside trap` note already describes. Two lookbehinds rather than one
#: alternation because Python's `re` requires each to be fixed-width.
#:
#: The pattern here is worth naming for whoever adds the third: this rule guards *authored
#: descriptions of provenance*, and every false positive so far has been an *enumerated domain
#: value* that happens to collide. If a fourth arrives, the better fix is likely to stop matching
#: inside quoted or upper-case value rosters at all, rather than to keep extending this list.
FORBIDDEN = re.compile(
    "(?<![A-Za-z])(decoy|(?<!offside )(?<!steam )trap|mimic|planted|synthetic)(?![A-Za-z])"
    "|(?<![A-Za-z])(fabricat|imitat)",
    re.I,
)

#: Rules that police *authored* prose, and the one type whose text is not authored. A few-shot's
#: summary **is** a training question, harvested verbatim by script: it quotes the values it asks
#: about (V5), it can name a film called "The Trap" (V10), and a terse wh-question like
#: "How many employees sold X?" carries no function words at all (V4). Holding harvested text to
#: a prose standard makes the writer falsify the asset to satisfy the checker. If a few-shot ever
#: becomes agent-written, this exemption has to go with it.
AUTHORED_ONLY: frozenset[str] = frozenset({"V4", "V5", "V10"})

#: ``Means 'x' (obfuscated to 'x')`` -- 42% of column bodies in both of the corpora this kit
#: replaced (measured 2026-08-08).
TAUTOLOGY_BODY = re.compile(r"^(physical column\s+'[^']*'\.\s*)?means\s+['\"]", re.I)

#: Per-**asset** body caps. The motive is unchanged and still good: the corpus this kit replaced
#: held 15 assets over 80,000 bytes -- one of them 5.1 MB, a ``VALUES`` list harvested from a
#: constant-answer gold query -- and those 15 were half its bytes. A body is where that pathology
#: lives, and a body is what :func:`~governed_bi.serve.fetch.read_body` hands the model.
#:
#: **This measured the file until 2026-08-13, and the file is not the delivery unit.**
#: ``corpus/store.py`` splits a table's inline columns into their own assets and leaves the parent
#: holding a list of ids; nothing on the serve path ever reads the YAML. Measured on the
#: facilities corpus, the six files the old 32,000-byte cap failed deliver 3,871-8,435 chars --
#: file size overstated the real cost by 7.7x on the worst one and 11.3x on another. The rule was
#: therefore firing on *column count* (~75+), which is a fact about a schema and not a defect:
#: a 66-column table passed at 29,178 bytes for no reason anyone could state.
#:
#: ``few_shot`` keeps its own number for its own reason -- one question and one query, so anything
#: bigger is a materialised result set. Observed maxima are 1,575 (facilities) and 1,346 (BIRD).
#: 8,000 for everything else sits 2.7x above the largest body in either corpus (2,912).
BODY_CAP: dict[str, int] = {"few_shot": 4_000, "*": 8_000}

#: What one table actually costs the context block: its structural line, its body, and the roster
#: its pulled-in columns fold into (``serve/context.py``). **This is the half a per-asset cap
#: cannot see.** A roster entry runs ~53 chars, so a 1,500-column table would render 80,000 chars
#: and consume the entire budget while every individual asset passed its cap and nothing
#: complained. That is the deliverability question the file cap was crudely approximating, and
#: this is it asked directly.
#:
#: 20,000 is a quarter of ``context_budget_chars`` (80,000) -- one table may not take a quarter of
#: the whole rendered budget. Worst observed: 8,435 (``archibus_room_attributes``, 156 columns)
#: and 6,787 (``european_football_2.partido``, 118), so 2.4x headroom.
CLOSURE_CAP: int = 20_000

_ALNUM = re.compile(r"[^a-z0-9]")
_WORD = re.compile(r"[A-Za-z][A-Za-z'-]*")


def _closed() -> None:
    """Import-time: every asset type has a cap and a body decision."""
    missing = [t.value for t in AssetType if t not in SUMMARY_CAP]
    if missing:  # pragma: no cover - import-time guard
        raise AssertionError(f"SUMMARY_CAP is missing {missing}; a new type needs a decision")


_closed()








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


def _own_identifiers(at: AssetType, a: dict[str, Any]) -> list[str]:
    """The names this asset is entitled to spell: its identifier fields, and its table's name.

    Used only to exempt them from V10. A column on a table called `fabrication_log` has to be
    able to say so. Longest first, so a name that contains another is blanked whole.
    """
    names = {
        str(a.get(field)).rsplit(".", 1)[-1]
        for field in ASSET_REGISTER[at].identifier_fields
        if a.get(field)
    }
    for field in ("parent_table", "base_table", "left_table", "right_table"):
        if a.get(field):
            names.add(str(a[field]).rsplit(".", 1)[-1])
    return sorted((n for n in names if n), key=len, reverse=True)


#: Dialect for V17a. The same one ``govern/`` parses generated SQL at -- restated nowhere, because
#: a metric expression that parses here and not there is a metric that fails at serve time.
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

    # Only touched when there is something to add: `out` is a defaultdict, so an unconditional
    # `extend([])` creates an empty key and "no findings" stops meaning `{}`.
    if at is AssetType.metric:
        for finding in check_metric_expression(a, where):
            out["V17a"].append(finding)
    for finding in check_guard_rules(kind, a, where):
        out["V21"].append(finding)

    body_cap = BODY_CAP.get(kind, BODY_CAP["*"])
    if len(body) > body_cap:
        out["V13"].append(
            Finding(f"{where}: body is {len(body):,} chars, cap {body_cap:,} for {kind}")
        )

    for field in ASSET_REGISTER[at].identifier_fields:
        raw = a.get(field)
        ident = str(raw).rsplit(".", 1)[-1] if raw else ""
        if not ident:
            out["V3"].append(Finding(f"{where}: {field} is unset, so the identifier rule cannot hold"))
        elif ident not in summary:
            out["V3"].append(Finding(f"{where}: summary omits {field}={ident!r}"))

    def polices(rule: str) -> bool:
        """Does ``rule`` apply to this asset? ``AUTHORED_ONLY`` is the whole answer.

        Read through the set rather than an inline ``at is not AssetType.few_shot``: that
        spelling left ``AUTHORED_ONLY`` defined and referenced nowhere, so it documented an
        exemption it did not enforce, and adding a rule id to it did nothing. It also exempted
        only half of V5 -- the ``PAREN_TAIL`` branch was ungated, which contradicted the
        declaration. "Prose rules that nothing executes are not rules" applies to this file too.
        """
        return not (at is AssetType.few_shot and rule in AUTHORED_ONLY)

    if polices("V4") and summary and summary != SENTINEL and not is_prose(summary):
        out["V4"].append(
            Finding(f"{where}: summary is not prose (function-word ratio "
                    f"{function_ratio(summary):.2f}): {summary[:70]!r}")
        )

    if polices("V5"):
        if VALUE_TALK.search(summary):
            out["V5"].append(
                Finding(f"{where}: summary carries values or examples, which belong in body")
            )
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
    # An asset's own name is not a disclosure, and V3 *requires* the summary to carry it. The
    # two rules contradicted each other on `shipping.camion.annee_fabrication` -- French for
    # year of manufacture, so the physical name contains the `fabricat` stem and no legal
    # summary existed. Blank the identifier out before searching rather than weakening the stem.
    for own in _own_identifiers(at, a):
        blob = blob.replace(own.lower(), " ")
    hit = FORBIDDEN.search(blob) if polices("V10") else None
    if hit:
        out["V10"].append(Finding(f"{where}: text contains {hit.group(0)!r}"))
    return out


def check_references(assets: Iterable[tuple[str, dict[str, Any], Path]]) -> list[Finding]:
    """V9 -- every declared reference resolves. Whole-corpus only.

    An inline column's id comes from :func:`~governed_bi.corpus.identity.derive_column_id`, not
    from ``f"{table_id}.{physical_name}"``. The two agree only while every column name is a bare
    identifier: ``derive_column_id`` slugs the name, so a column physically called
    ``Air Carriers`` is ``...Air_Carriers_66c534`` to the loader and ``...Air Carriers`` to the
    hand-rolled spelling. A corpus that referenced such a column *correctly* therefore failed
    this rule, and V9 returns exit 1 — a gate blocking a valid tree. The module docstring names
    this exact class of bug as the reason this file imports its policy instead of restating it.
    """
    ids = {str(a.get("id")) for kind, a, _ in assets if a.get("id")}
    for kind, a, _ in assets:
        if kind == "table":
            tid = str(a.get("id") or "")
            for col in a.get("columns") or []:
                if isinstance(col, dict) and col.get("physical_name"):
                    ids.add(derive_column_id(tid, str(col["physical_name"])))
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


def check_suspect_set(assets, trap_manifest: Path, table_manifest: Path,
                      rename_map: Path) -> list[Finding]:
    """V15 -- exactly the manifest's columns are marked suspect, no more and no fewer.

    V11 polices how a suspect column is *worded*; nothing until now policed *which* columns
    carry the mark, and the two failure directions cost different things. Marking a real column
    suspect is the expensive one: ``reliability.note`` is never dropped from the context, so the
    model is told every turn not to use a column it needs.

    The packet hands writers a flat, de-duplicated list of bare column names, so a name that is
    real on one table and planted on another arrives indistinguishable. That is how
    ``regional_sales.emplacements_magasin.code_zone`` -- a real telephone area code -- came to be
    suppressed alongside the planted ``code_zone_geo``, while a *different* real ``code_zone``
    on ``zones_geographiques`` holds state abbreviations.

    The manifest keys tables by their upstream BIRD name, so every lookup goes through
    ``schema_rename_map.json`` first. Comparing against the raw name silently reports every real
    column in a renamed schema as mis-marked, which is what a first pass at this check did.
    """
    rmap = json.loads(rename_map.read_text(encoding="utf-8"))
    traps = json.loads(trap_manifest.read_text(encoding="utf-8"))
    tables = json.loads(table_manifest.read_text(encoding="utf-8"))

    planted: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for t in traps:
        if t.get("names"):
            db = t["db"]
            renamed = (rmap.get(db) or {}).get(t["table"], t["table"])
            planted[db].add((renamed, t["names"]["rename"]))
    decoy_tables: dict[str, set[str]] = defaultdict(set)
    for t in tables:
        renamed = (t.get("names") or {}).get("rename") or {}
        if t.get("db") and renamed.get("table"):
            decoy_tables[t["db"]].add(renamed["table"])

    bad: list[Finding] = []
    for kind, a, path in assets:
        if kind != "table":
            continue
        db, tbl = _text(a.get("schema")), _text(a.get("physical_name"))
        if db not in planted and db not in decoy_tables:
            continue
        whole = tbl in decoy_tables.get(db, set())
        for col in a.get("columns") or []:
            if not isinstance(col, dict):
                continue
            name = _text(col.get("physical_name"))
            rel = col.get("reliability")
            status = (rel or {}).get("status") if isinstance(rel, dict) else None
            marked = str(getattr(status, "value", status)) == "suspect"
            should = whole or (tbl, name) in planted.get(db, set())
            if marked and not should:
                bad.append(Finding(
                    f"{path.name}:{tbl}.{name}: marked suspect but the manifest says it is real; "
                    "the caveat renders every turn and can never be dropped"))
            elif should and not marked:
                bad.append(Finding(f"{path.name}:{tbl}.{name}: the manifest plants this column, "
                                   "but it carries no reliability caveat"))
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


def check_delivery_closure(paths: Iterable[Path]) -> list[Finding]:
    """V16 -- a table plus the roster its columns fold into fits :data:`CLOSURE_CAP`.

    **Measured with the renderer that does the delivering**, not with a second copy of its
    arithmetic: ``serve/context.py``'s own ``_structural_line`` and ``_roster_entry`` are what
    decide the cost, so a rule that recomputed it here would be free to drift from the thing it
    claims to bound. That is also why this loads the file through ``corpus/store.py`` rather than
    reading the raw YAML the other rules use -- inline columns only become assets on the way
    through the loader, and it is the assets that are rendered.

    Per file, which is exact rather than convenient: a column asset exists only inside its
    table's file, so one file holds a whole closure and no cross-file pass is needed.

    A *hit* column keeps its own body instead of folding, so the true worst case is slightly
    above what this measures. The gap is small -- column bodies run ~119 chars at the median, so
    twenty hits add ~2,600 -- and it cannot be computed without a query, which a conformance rule
    does not have.
    """
    from governed_bi.corpus.store import load_file
    from governed_bi.serve.context import _roster_entry, _structural_line

    bad: list[Finding] = []
    for path in paths:
        loaded, _ = load_file(path)
        tables = [a for a in loaded if type(a).__name__ == "TableAsset"]
        columns = [a for a in loaded if type(a).__name__ == "ColumnAsset"]
        for table in tables:
            roster = sum(len(_roster_entry(c)) + 1 for c in columns)
            cost = len(_structural_line(table, terse=False)) + len(str(_field_of(table, "body")))
            total = cost + roster
            if total > CLOSURE_CAP:
                bad.append(
                    Finding(
                        f"{path.name}: renders {total:,} chars "
                        f"({cost:,} table + {roster:,} roster over {len(columns)} columns), "
                        f"cap {CLOSURE_CAP:,}"
                    )
                )
    return bad


def _field_of(asset: Any, name: str) -> Any:
    """One asset field, whether the asset is a model or a mapping. ``""`` when absent."""
    if isinstance(asset, dict):
        return asset.get(name) or ""
    return getattr(asset, name, "") or ""


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
    "V1": "1 <= len(summary) <= summary_max_chars",
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
    "V13": "no asset body exceeds its cap (few_shot 4k, else 8k)",
    "V14": "the engine's loader accepts the file",
    "V15": "exactly the manifest's columns are marked suspect",
    "V16": "a table and its folded column roster fit the delivery cap",
    "V17a": "a metric expression parses as SQL at the engine's dialect",
    "V17b": "every identifier in a metric expression resolves on base_table or a declared join",
    "V19": "no model-visible body names a governance-excluded column or asset",
    "V21": "model-visible text passes govern/guard.py's GUARD_RULES",
    "V23": "asset ids are unique across the tree",
}

#: Rules that need more than one asset, and are reported **not evaluated** rather than passed in
#: ``--file`` mode. V17b is here because "reachable through a declared join" is a question about
#: the join assets, and V23 because a duplicate needs a second file to duplicate.
WHOLE_TREE_ONLY = ("V9", "V11", "V12", "V15", "V17b", "V23")


def _where_of(line: str) -> str:
    """The ``file:asset`` a finding is about, or ``""`` if the line is not in that shape.

    Split off the front rather than parsed, because the message that follows contains colons of its
    own. ``""`` drops the line from the JSON: an identity the ratchet cannot key on is worse than a
    missing finding, since it would pin as one thing and re-appear as another.
    """
    parts = str(line).split(":", 2)
    return f"{parts[0]}:{parts[1]}" if len(parts) >= 3 else ""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="check_corpus_conformance", description=__doc__)
    ap.add_argument("--corpus-dir", type=Path, default=DEFAULT_CORPUS)
    ap.add_argument("--file", type=Path, default=None, help="check one asset file (rebuild loop)")
    ap.add_argument("--trap-manifest", type=Path, default=DEFAULT_DATASET / "trap_manifest.json")
    ap.add_argument("--table-manifest",
                    type=Path, default=DEFAULT_DATASET / "trap_table_manifest.json")
    ap.add_argument("--rename-map",
                    type=Path, default=DEFAULT_DATASET / "schema_rename_map.json")
    ap.add_argument("--test-split", type=Path, default=DEFAULT_DATASET / "test_final.jsonl")
    ap.add_argument("--max-lines", type=int, default=15, help="findings printed per rule")
    ap.add_argument(
        "--json",
        action="store_true",
        help="emit findings as JSON on stdout (for tools/check_ratchet.py); exit 0 either way",
    )
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

    findings["V16"].extend(check_delivery_closure(sorted({p for _, _, p in assets})))

    skipped: dict[str, str] = {}
    if whole:
        findings["V9"].extend(check_references(assets))
        findings["V17b"].extend(check_metric_bindings(assets))
        findings["V19"].extend(check_excluded_not_named(assets))
        findings["V23"].extend(check_unique_ids(assets))
        if args.trap_manifest.exists():
            findings["V11"].extend(check_suspect_summaries(assets, args.trap_manifest))
        else:
            skipped["V11"] = f"no trap manifest at {args.trap_manifest}"
        if args.test_split.exists():
            findings["V12"].extend(check_split_leak(assets, args.test_split))
        else:
            skipped["V12"] = f"no test split at {args.test_split}"
        if args.trap_manifest.exists() and args.table_manifest.exists() and args.rename_map.exists():
            findings["V15"].extend(
                check_suspect_set(assets, args.trap_manifest, args.table_manifest, args.rename_map)
            )
        else:
            skipped["V15"] = "needs the trap, table and rename manifests"
    else:
        for rule in WHOLE_TREE_ONLY:
            skipped[rule] = "needs the whole tree"

    if args.json:
        # A finding's **identity** is (rule, asset), and that is all this emits alongside the
        # message. The ratchet pins identities: a reworded message must not read as a new finding,
        # and a finding moving to another asset must not read as the same one.
        print(
            json.dumps(
                {
                    "corpus": str(args.corpus_dir if whole else args.file),
                    "whole_tree": whole,
                    "not_evaluated": skipped,
                    "findings": [
                        {"rule": rule, "where": _where_of(line), "message": str(line)}
                        for rule in RULES
                        for line in sorted(findings.get(rule, ()))
                        if _where_of(line)
                    ],
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0

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
