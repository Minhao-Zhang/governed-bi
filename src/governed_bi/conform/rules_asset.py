"""Every rule answerable from one asset alone: V0-V8, V10, V13, and the two it delegates.

A predicate over one YAML mapping, which is what separates this file from ``rules_tree.py`` (needs
a second asset, a manifest or the loader) and from ``rules_metric_and_content.py`` (reuses
``govern/`` and ``sqlglot``). :func:`check_local` is the single entry: it returns rule id -> findings
for one asset, and the rebuild loop's ``--file`` mode is exactly this file's rules plus the two
whole-tree-independent ones.
"""


from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from governed_bi.register.assets import ASSET_REGISTER, AssetType
from governed_bi.register.knobs import knob_default
from governed_bi.register.quantity import Measured

from .findings import Finding, _text
from .rules_metric_and_content import check_guard_rules, check_metric_expression

#: The sentinel the scaffold writes. It must fail, so an unfinished asset cannot ship.
SENTINEL = "TODO"

#: ``summary`` cap, **read from the knob rather than restated**. It is one global value for
#: every type (``knobs.py`` `summary_max_chars`, enforced in ``corpus/validate.py``), and this
#: file first hard-coded 400 for schema assets on the argument that the routing signal for a
#: whole database deserves more room. It does not get more room: V1 passed at 340 characters
#: and the model rejected the file, so the writer met a validator that was more permissive than
#: the thing it validates. Raising it is a knob change and a treatment change, not a cap here.
SUMMARY_CAP: dict[AssetType, int] = {t: int(knob_default("summary_max_chars")) for t in AssetType}

#: Every type's §1.2 entry names a ``body``, and a missing one delivers nothing but the structural
#: line.
#:
#: This used to say ``summary`` never reaches the model. It does, for exactly one asset:
#: ``serve/context.py`` renders a few-shot from its ``body``, and **with no body it renders
#: ``summary`` and ``sql`` concatenated**. So for a bodyless few-shot the summary is prompt text --
#: which is a second reason to require a body rather than an argument against it, since a summary
#: is written for the retrieval index and reaches the model only by that accident.
#: :func:`~governed_bi.conform.rules_metric_and_content.model_visible_text` is the one place that
#: answers "what does the model see", and V19 and V21 both read it.
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

_ALNUM = re.compile(r"[^a-z0-9]")
_WORD = re.compile(r"[A-Za-z][A-Za-z'-]*")


def _closed() -> None:
    """Import-time: every asset type has a cap and a body decision."""
    missing = [t.value for t in AssetType if t not in SUMMARY_CAP]
    if missing:  # pragma: no cover - import-time guard
        raise AssertionError(f"SUMMARY_CAP is missing {missing}; a new type needs a decision")





def _norm(value: str) -> str:
    return _ALNUM.sub("", value.lower())


def function_ratio(summary: str) -> float:
    words = _WORD.findall(summary.lower())
    return sum(1 for w in words if w in FUNCTION_WORDS) / len(words) if words else 0.0


def is_prose(summary: str) -> bool:
    return not any(t.match(summary) for t in TEMPLATES) and function_ratio(summary) >= MIN_FUNCTION_RATIO


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
        # The ratio is rendered through ``Measured`` rather than an ``f"{x:.2f}"`` spec, and that
        # is a rule of ``src/`` this file did not have to obey while it lived under ``tools/``:
        # ``tools/check_measurement_locality.py`` permits one formatting site in the package, so a
        # precision claim is made in the one place that also knows how to render an absence. Two
        # decimal places, unchanged -- the finding text is what a writer reads and what
        # ``verify_patch.py`` diffs.
        ratio = Measured.of(function_ratio(summary))
        out["V4"].append(
            Finding(f"{where}: summary is not prose (function-word ratio "
                    f"{ratio.render()}): {summary[:70]!r}")
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
