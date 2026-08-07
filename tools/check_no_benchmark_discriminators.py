"""Refuse the hand-authored sibling discriminators (audit §6.1). No data needed.

A *sibling discriminator* is a phrase written into a corpus ``summary`` to tell the router
which of two similarly-named schemas a benchmark question is really about — ``soccer_2016``
prefixed with ``cricket IPL batsman bowling`` because that schema's questions are about
cricket despite its name. Writing one requires having read the held-out questions, and
``summary`` is the only text that enters either retrieval channel
(``retrieve/index.py``), so the phrase is a direct answer key.

**Why this gate exists and ``check_train_only.py`` was not enough.** These phrases contain no
question wording, so the containment check finds nothing; they are short, so the n-gram rate
barely moves. The contaminated corpus was measured and it passed, with statistics
byte-identical to the control. A rate arm cannot see a leak that carries no question text.

**Why it is a source gate rather than a corpus gate.** The corpus that held these is
untracked and reproducible only by re-running the scripts that wrote them, so the durable
artifact to defend against is the *script*. Two held these tables and both were deleted; this
file keeps the tables so the phrases cannot come back anywhere in the tree — a script, a
fixture, a YAML asset, a docstring quoting one as an example.

**The audit named one producer; running this gate found a second.**
``tools/_nuclear_dense_plus_prefix.py`` was §6.1's subject. ``tools/_revise_miss_summaries.py``
was not mentioned anywhere in the audit, and it is the worse of the two: its 26 phrases carry
*negative* discriminators — ``soccer_2016`` led with ``"cricket IPL … NEVER football soccer"``
— and its ``MISS_SCHEMAS`` set names the schemas the router got **wrong**, so the phrases are
a direct transcription of which held-out questions were missed and which sibling stole the
slot. §6.1 was a lower bound.

**Two rules, because a list of phrases only catches a repeat.** Rule A is the verbatim tables.
Rule B is the *shape*: a summary that shouts what its schema is **NOT**. Ordinary domain prose
does not need to; that spelling appears when someone is fixing a sibling collision they
learned about from an evaluation.

**Inside a corpus, both rules read ``summary`` and nothing else.** The first version scanned
every line of every corpus file and was unusable: 31,599 hits, of which 31,560 were rule B
firing on the obfuscation dataset's own decoy marker — ``'DECOY column: not a real business
field. Do NOT use it to answer questions.'`` — which is the corpus telling the model to
*ignore* a column, the opposite of a leak. Rule A over-fired for the same reason in a subtler
way: ``PREFIX['simpson_episodes'] = 'Simpsons season-20'`` is also just what that schema is,
so it matched the ``body`` prose of six uncontaminated corpora including the one ``.env``
serves. Both were false positives of the same kind — a phrase found somewhere a phrase cannot
do any harm.

Scoping to ``summary`` is not a tolerance, it is the rule restated: ``summary`` is the only
text that enters either retrieval channel, so a discriminator anywhere else cannot steer the
router. ``body``, ``rules`` and column ``note`` are read by the model *after* the schema has
already been chosen. Outside a corpus — ``.py``, ``.md``, fixtures — rule A still scans every
line, because there the artifact being defended against is the producing script, not the text.

**The limit this leaves.** A single-line JSON corpus document is not decomposed into fields,
so its summaries are unscanned. No corpus in this tree is written that way; if one is, this
gate is blind to it.

**What neither rule does.** A future build inventing a positive-only discriminator in new
words is invisible here, exactly as a reworded leak is invisible to ``check_train_only``. The
general control is provenance — an asset recording which script and which model wrote its
summary — and that is a different gate over a field nothing currently writes (§6.3).

Exit 1 on a hit. Never passes vacuously: an empty scan is a failure.
"""

from __future__ import annotations

import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]

#: Verbatim from ``tools/_nuclear_dense_plus_prefix.py``'s ``PREFIX`` table at the commit that
#: deleted it, keyed by the schema each phrase was written to disambiguate. Verbatim so the
#: list is checkable against history rather than reconstructed.
#:
#: Kept in full rather than hashed. A gate whose subject is unreadable cannot be reviewed, and
#: these are not secrets — the benchmark is public. What was not allowed was putting them in a
#: corpus.
RETIRED_DISCRIMINATORS: dict[str, str] = {
    "ice_hockey_draft": "draft-prospects scouting ELITEID",
    "hockey": "NHL-career Stanley-Cup HOF",
    "soccer_2016": "cricket IPL batsman bowling",
    "european_football_2": "FIFA club-football bookmaker",
    "professional_basketball": "NBA ABA NBL basketball",
    "movie_platform": "Mubi lists subscriber trialist",
    "movies_4": "TMDB production cast crew",
    "movielens": "MovieLens audience-ratings",
    "disney": "Walt-Disney animated voice-actors",
    "food_inspection": "inspection-score violation risk-category",
    "food_inspection_2": "sanitarian fines inspection-point",
    "restaurant": "California cuisine directory rating",
    "menu": "historical menu-page dish-price",
    "beer_factory": "rootbeer brewery brand review",
    "car_retails": "classicmodels scale-model productlines",
    "regional_sales": "sales-team store-location net-profit",
    "sales": "bicycle-parts free-gift",
    "superstore": "four-region Central East South West",
    "retails": "TPC-H partsupp lineitem",
    "retail_complains": "finance-complaints call-center",
    "law_episode": "Law-and-Order Primetime-Emmy",
    "simpson_episodes": "Simpsons season-20",
    "address": "ZIP-code zip_data demographics",
    "student_loan": "student-loan disability bankruptcy",
    "synthea": "synthetic-EHR patients encounters",
    "works_cycles": "AdventureWorks BOM workorders",
    "toxicology": "molecule atom bond toxicity",
}

#: Verbatim from ``tools/_revise_miss_summaries.py``'s ``LEADS`` table at the commit that
#: deleted it. The second producer, absent from the audit. Each phrase leads a schema summary
#: and most end in a negative clause telling the router which sibling *not* to pick.
#:
#: ``noqa: E501`` on the long ones, and the exemption is the point: these are needles, and a
#: needle wrapped to fit a line limit is a needle that no longer matches. Ten of them exceed
#: 120 characters in the source they came from, which is also why ``ruff check .`` — CI's third
#: step, before every gate — failed for the whole life of the ``v2`` branch. The contamination
#: script was what stopped CI from running the gates.
RETIRED_LEADS: dict[str, str] = {  # noqa: E501
    "ice_hockey_draft": "scouting draft prospects ELITEID height weight CSS junior leagues NOT career HOF Stanley",
    "hockey": "NHL WHA career scoring goalies Stanley Cup HOF standings NOT draft prospects",
    "soccer_2016": "cricket IPL ball-by-ball batsman bowling wickets toss umpire NEVER football soccer",
    "european_football_2": "European club football soccer FIFA attributes leagues bookmaker odds NOT cricket basketball",  # noqa: E501
    "professional_basketball": "NBA ABA NBL basketball all-star rebounds coaches draft NOT hockey football cricket",
    "movie_platform": "Mubi social lists critic reviews subscriber trialist rating-score NOT MovieLens production rental",  # noqa: E501
    "movies_4": "TMDB production cast crew keywords companies box-office budget NOT user-ratings Mubi rental",
    "movielens": "MovieLens audience ratings actors directors occupation release-year NOT Mubi TMDB Disney",
    "disney": "Walt Disney animated voice-actors heroes villains songs segment-revenue NOT MovieLens TMDB",
    "food_inspection": "food-safety inspection score violation risk businesses owner San-Francisco NOT cuisine directory",  # noqa: E501
    "food_inspection_2": "municipal food-safety sanitarian employee inspection-point fines license taverns NOT SF-score directory",  # noqa: E501
    "restaurant": "California restaurant cuisine food-type review-rating city county region directory NOT inspection violation",  # noqa: E501
    "menu": "historical menu-page dish price venue sponsor event appearance-history NOT live restaurant inspection",
    "beer_factory": "root-beer brand brewery store customer transaction star-review geolocation NOT finance complaints",
    "car_retails": "classicmodels scale-model productlines offices payments MSRP buyPrice Sales-Rep NOT AdventureWorks bike-parts",  # noqa: E501
    "regional_sales": "US regional sales-team store-location warehouse channel net-profit discount orders NOT TPC-H complaints",  # noqa: E501
    "sales": "bicycle-parts retailer product quantity employee customer free-gift NOT classicmodels Superstore",
    "superstore": "Superstore four-region Central East South West order-lines profit discount NOT classicmodels TPC-H",
    "retails": "TPC-H wholesale nation region partsupp lineitem account-balance NOT classicmodels Superstore",
    "retail_complains": "consumer-finance complaints call-center reviews credit mortgage deposits NOT product sales orders",  # noqa: E501
    "law_episode": "Law-and-Order episode credits awards Primetime star-votes id-keyed NOT Simpsons",
    "simpson_episodes": "Simpsons season-20 character awards name-keyed credits star-votes NOT Law-and-Order",
    "address": "ZIP-code demographics households income housing elevation metro congressional area-codes",
    "student_loan": "student-loan disability bankruptcy unemployment enlistment enrollment absence payment-due name-lists",  # noqa: E501
    "synthea": "synthetic EHR patients encounters conditions medications allergies immunizations claims prevalence",
    "works_cycles": "AdventureWorks manufacturing sales cycles workorders BOM specialoffers NOT classicmodels scale-model",  # noqa: E501
}

#: Rule B: a summary shouting what its schema is *not*.
#:
#: Upper case is the whole signal. Ordinary domain prose writes "does not include returns"; the
#: shouted form is what someone writes when they are steering a router away from a sibling they
#: learned about from an evaluation. Both deleted producers used it.
NEGATIVE_DISCRIMINATOR = re.compile(r"\b(?:NOT|NEVER|EXCLUDES?)\s+[a-z]")

#: Rule B scans data, not code: a comment reading "NOT a bypass" is normal in this tree.
DATA_SUFFIXES: frozenset[str] = frozenset({".yaml", ".yml", ".json", ".jsonl"})

#: A ``summary`` key at any nesting depth, quoted (JSON) or bare (YAML). Group 1 is the
#: indent, which is what ends the value: a scalar's continuation lines are indented past its
#: key, and the next key at the same depth is not.
SUMMARY_KEY = re.compile(r'^(\s*)"?summary"?\s*:\s*(.*)$')

#: The ``<id>: `` every corpus summary opens with, which both producers wrote their phrase
#: immediately after.
LEAD_IN = re.compile(r"^[\w.\-]+:\s*")

#: Rule B is for schema summaries. A *term* asset defines itself by negation as a matter of
#: course — ``student_loan``'s "female student" is "a student whose name does NOT appear in the
#: male (nan_xing) table", because that schema has no female table. Shouting is only evidence
#: of router-steering where the producers wrote, and both wrote schema summaries.
#:
#: Phrased as "not a declared non-schema asset" rather than "declares schema" on purpose: a
#: file that declares no type at all is still scanned. The other spelling would mean a corpus
#: that omits ``asset_type`` — or spells it differently — escapes rule B silently, and a gate
#: that can be switched off by a missing line is the vacuous pass this one refuses.
ASSET_TYPE = re.compile(r"^\s*\"?asset_type\"?\s*:\s*\"?([A-Za-z_]+)", re.MULTILINE)

#: Only under a corpus root — an arbitrary JSON fixture is not a semantic-layer summary.
DATA_ROOTS: tuple[str, ...] = ("corpus", "corpora")

#: Where a phrase could do damage: producing code, and any corpus tree.
SCAN_ROOTS: tuple[str, ...] = ("src", "tools", "tests", "corpus", "corpora")

SCAN_SUFFIXES: frozenset[str] = frozenset({".py", ".yaml", ".yml", ".json", ".jsonl", ".md"})

SKIP_DIRS: frozenset[str] = frozenset({"__pycache__", ".venv", ".git", ".ruff_cache"})

#: This file quotes every phrase by construction, and so does its test.
EXEMPT: frozenset[str] = frozenset(
    {
        "tools/check_no_benchmark_discriminators.py",
        "tests/conformance/test_no_benchmark_discriminators.py",
    }
)


def files_to_scan() -> list[pathlib.Path]:
    out: list[pathlib.Path] = []
    for root in SCAN_ROOTS:
        base = REPO / root
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in SCAN_SUFFIXES:
                continue
            if SKIP_DIRS & set(path.parts):
                continue
            if path.relative_to(REPO).as_posix() in EXEMPT:
                continue
            out.append(path)
    return sorted(out)


def _is_corpus_data(path: pathlib.Path) -> bool:
    parts = path.relative_to(REPO).parts
    return path.suffix.lower() in DATA_SUFFIXES and bool(parts) and parts[0] in DATA_ROOTS


def summary_blocks(lines: list[str]) -> list[tuple[int, str]]:
    """``(first line number, joined value)`` for each ``summary`` in the file.

    Indentation rather than a YAML parse: 59,661 corpus files is too many to load, and the
    line number is what makes a hit checkable. Joined rather than per-line because both rules
    read the value as a sentence — one asks what it *starts* with, the other would otherwise
    miss a phrase that wrapped.
    """
    blocks: list[tuple[int, str]] = []
    inside: int | None = None
    current: list[str] = []
    start = 0

    def flush() -> None:
        nonlocal current
        if current:
            blocks.append((start, " ".join(current).strip()))
            current = []

    for number, line in enumerate(lines, start=1):
        if inside is not None:
            stripped = line.strip()
            if stripped and (len(line) - len(line.lstrip())) <= inside:
                flush()
                inside = None  # dedented to a sibling key: the value ended
            else:
                current.append(stripped)
                continue
        match = SUMMARY_KEY.match(line)
        if match is not None:
            flush()
            inside, start, current = len(match.group(1)), number, [match.group(2).strip()]
    flush()
    return blocks


def leads_with(value: str, phrase: str) -> bool:
    """Does ``value`` open with ``phrase``, past the ``<id>: `` both producers wrote first?

    Position is the signal. ``_nuclear_dense_plus_prefix`` built ``f"{schema}: {prefix} …"``
    and ``_revise_miss_summaries``' phrases "lead a schema summary" — so a table phrase found
    *mid-sentence* is the schema describing itself, not a prepended steer.
    """
    head = value.lstrip("'\"").strip()
    head = LEAD_IN.sub("", head, count=1)
    return head.casefold().startswith(phrase.casefold())


def hits(paths: list[pathlib.Path]) -> list[str]:
    """``path:line: what`` for every occurrence of either rule."""
    found: list[str] = []
    retired = [
        (schema, phrase, table)
        for table, entries in (("PREFIX", RETIRED_DISCRIMINATORS), ("LEADS", RETIRED_LEADS))
        for schema, phrase in entries.items()
    ]
    for path in paths:
        rel = path.relative_to(REPO).as_posix()
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            # Named rather than skipped: an unreadable file under a corpus root is a file this
            # gate did not check, and a scan that silently shrinks is a scan that passes.
            found.append(f"{rel}: unreadable, so unchecked")
            continue
        lines = text.splitlines()
        if not _is_corpus_data(path):
            # Outside a corpus the subject is a producing script, so the whole file is in
            # scope and position means nothing: a phrase in a docstring is still the table.
            for number, line in enumerate(lines, start=1):
                haystack = line.lower()
                for schema, phrase, table in retired:
                    if phrase.lower() in haystack:
                        found.append(f"{rel}:{number}: rule A, {table}[{schema!r}] = {phrase!r}")
            continue

        blocks = summary_blocks(lines)
        for number, value in blocks:
            for schema, phrase, table in retired:
                if leads_with(value, phrase):
                    found.append(
                        f"{rel}:{number}: rule A, summary opens with "
                        f"{table}[{schema!r}] = {phrase!r}"
                    )
        declared = ASSET_TYPE.search(text)
        if declared is None or declared.group(1).casefold() == "schema":
            for number, value in blocks:
                match = NEGATIVE_DISCRIMINATOR.search(value)
                if match is not None:
                    found.append(
                        f"{rel}:{number}: rule B, a shouted negative discriminator "
                        f"({match.group(0)!r}) in a schema summary"
                    )
    return found


def main() -> int:
    paths = files_to_scan()
    if not paths:
        print(
            f"no files matched {sorted(SCAN_SUFFIXES)} under {list(SCAN_ROOTS)} — refusing to "
            "pass vacuously",
            file=sys.stderr,
        )
        return 1

    problems = hits(paths)
    if problems:
        print(
            f"{len(problems)} hand-authored benchmark discriminator(s) in the tree:\n",
            file=sys.stderr,
        )
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        print(
            "\nThese phrases were written by reading the held-out questions, and `summary` is "
            "the only text that enters retrieval. A corpus carrying one cannot be used for any "
            "measurement, and it passes check_train_only with statistics identical to the "
            "control, because the phrases contain no question wording.",
            file=sys.stderr,
        )
        return 1

    n_phrases = len(RETIRED_DISCRIMINATORS) + len(RETIRED_LEADS)
    data_files = sum(1 for p in paths if _is_corpus_data(p))
    print(
        f"no hand-authored benchmark discriminators across {len(paths)} file(s); "
        f"rule A: {n_phrases} retired phrase(s), {len(EXEMPT)} exempt path(s); "
        f"rule B: the summaries in {data_files} corpus data file(s)"
    )
    if data_files == 0:
        print(
            "note: no corpus data on disk, so rule B checked nothing. Rule A still covers the "
            "producers, which is the artifact that persists."
        )
    print(
        "This is the absence of two known contaminations, not proof of a clean corpus — a "
        "newly invented positive-only discriminator is invisible here."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
