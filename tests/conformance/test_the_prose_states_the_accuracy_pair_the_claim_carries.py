"""The accuracy pair, wherever prose states it, against the one literal that owns it.

**Why this file exists.** The pair moved 0.7555 -> 0.7548, and 0.7131 -> 0.7126 [retired], when
``gold_table_ids`` stopped comparing a gold's physical name against an asset id. `docs/open-work.md`
recorded "all ten sites are updated"; two were not, and one of them was a module header sitting a
couple of hundred lines above a string in the same file that carried the *new* figure. Review passed
that twice. `ui/scripts/check-review-copy.ts` closed the `ui/` side and
`tests/feedback/test_the_reproducer_answers_one_question_for_nothing.py` pins the Python side to the
constant, which left `docs/` with `grep -rn 0.7548 docs/` as its only instrument. This is the
instrument.

**A test and not a `tools/check_*.py`.** The `ui/` half is a script because `ui/` has no test
runner. Here there is one, the defect being gated is a *review* failure, and `pytest` is what a
reader runs before committing and what CI's `Tests` step runs — so the gate goes where the reader
already looks, and needs no new CI wiring to be true on every push. It stays as hermetic as the
script it mirrors: it reads `tools/reproduce_observation.py` with `ast` and every other file as
text, so no engine, no corpus, no database.

**One source, and no figure is written down here.** `tools/reproduce_observation.py::CLAIM` is one
string literal carrying both figures and both populations, "because a CLI and a screen disagreeing
about what a green T3 means is the two-answers defect the derived states exist to avoid". Both
figures and both populations are extracted from it. If the pair moves again this module does not
change — the documents do, or they fail.

**What it matches.**

*1. Anchored claim sentences.* Every blank-line-delimited block (table rows and list items are their
own blocks) that contains the phrase *measured accuracy* must state only numbers `CLAIM` carries:
every accuracy-shaped number in it must be one of `CLAIM`'s two figures, and every comma-grouped
count must be one of `CLAIM`'s two populations. A block that says *measured accuracy* and states no
figure at all also fails, because "the measured accuracy is high" is how the sentence survives
having its number deleted. The phrase is the anchor rather than the digits because an author
updating a figure edits the digits and never the words around them — an anchor made of digits would
stop matching at exactly the moment it was needed. The counts are in scope for the same reason the
population is asserted next door: a figure defended by a script that reproduced its own `n` is the
failure that was already survived once here.

*2. Presence in the four documents that state the pair.* `docs/glossary.md`, `docs/return-path.md`,
`docs/adr/0015-the-return-path.md` and `docs/open-work.md` must each contain both of `CLAIM`'s
figures. This is the half that catches a document falling behind where the wording is not the
canonical one — `glossary.md` states the second figure in a following sentence that never repeats
the anchor phrase — and it is also the positive control: these four are the sites the defect was
recorded at, so the sweep asserts it found them rather than asserting that it found nothing.

**What it deliberately does not match, and why widening it breaks it.**

*Every other measurement in `docs/`.* The tree is full of accuracy-shaped numbers that are different
quantities — 0.714 delivered, 0.940 table coverage, 0.936 before the metric fix, recall@k, EX,
per-arm figures. A sweep for "no accuracy-shaped number in `docs/` unless `CLAIM` has it" fails on
all of them on the first run, and a gate that must be silenced to be green is a gate that gets
deleted. So the scope is the anchored sentence, not the file.

*Retired figures reappearing.* Deliberately absent, because it already has an implementation:
`src/governed_bi/register/citations.py::RETIRED_CLAIMS` declares falsified numbers as patterns and
`tools/check_citations.py` fails on any of them in `src`, `tools`, `docs` or `tests`, with a
`[retired]` line marker for a deliberate quotation. The pair simply is not registered there yet. A
denylist here would be a second answer to a question this repository already answers in one place,
which is what `tools/check_one_implementation.py` exists to stop. Registering it is the remaining
step and it is one entry plus markers on the four lines that quote the old pair as history.

*Prose that keeps the digits and loses the claim.* "About one in four", the exclusion that produced
the figure, and the arithmetic between the two populations are not checked here. The wording of the
claim is asserted against `CLAIM` in `tests/feedback/`; this module is about the numbers.

*Other spellings.* `75.48%`, `0.75`, and space-grouped counts (`1 150`, which `docs/open-work.md`
uses for a different quantity) are invisible to the patterns, and so is a phrase broken across a
Python string-concatenation boundary — `CLAIM` itself splits *measured accuracy* between two
adjacent literals and is not one of the blocks this anchor finds, which costs nothing because it is
the thing being compared against. `ui/` is out of scope because it has its own gate, and
`.github/workflows/ci.yml` because its one mention is a comment about the move itself.

**The scan asserts it scanned.** Audit finding D13 was six conformance sweeps that asserted
``not offenders`` over a walk that could reach zero files, so repointing a path passed green. Every
sweep here has a floor: the file walk, the number of anchored blocks, the number of distinct files
they came from, and the four named documents being in the scanned set. Below any floor the module
fails as vacuous rather than passing.
"""

from __future__ import annotations

import ast
import dataclasses
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SELF = Path(__file__).resolve()

#: The one place the pair is declared. Read with `ast`, not imported: `reproduce_observation` pulls
#: in the engine and a sibling script, and this gate has to run in a bare environment.
CLAIM_SOURCE = ROOT / "tools" / "reproduce_observation.py"

#: Prose roots. `ui/` is absent because `ui/scripts/check-review-copy.ts` owns that side and reads
#: its comments too; a second scanner over the same files could only disagree with the first.
DOC_ROOT = ROOT / "docs"
CODE_ROOTS = (ROOT / "src", ROOT / "tools", ROOT / "tests")

#: The four documents `docs/open-work.md` names as stating the pair as prose.
STATES_THE_PAIR: tuple[str, ...] = (
    "docs/glossary.md",
    "docs/return-path.md",
    "docs/adr/0015-the-return-path.md",
    "docs/open-work.md",
)

#: The wording, not the digits. Case-insensitive: `glossary.md` writes "the engine's measured
#: accuracy", the ADR "the engine's measured accuracy is", and a sentence may start with it.
ANCHOR = re.compile(r"measured accuracy", re.IGNORECASE)

#: An accuracy-shaped number. Three or four decimals, so `12.7%` and `3.16x` are not swept in.
FIGURE = re.compile(r"\b0\.\d{3,4}\b")

#: A comma-grouped population, which is how both `CLAIM` and every document spell `n`. Space-grouped
#: counts are a different quantity in `docs/open-work.md` and are not matched.
POPULATION = re.compile(r"\b\d{1,3},\d{3}\b")

#: A line that is its own block: a table row, a bullet, a numbered item. Without this, the anchored
#: block in `glossary.md` would be the whole table and every other row's figures would come with it.
OWN_BLOCK = re.compile(r"\s*(?:\||[-*+] |\d+\. )")

#: Floors. The walk reaches ~380 files and the anchor matches 8 blocks in 6 of them. The file floors
#: are far under what is there, so ordinary editing cannot trip them and a mistyped root cannot pass
#: them. The block floor is deliberately close: two blocks of slack, because losing three of the
#: sentences that state this claim is a change to how the claim is recorded, and should be a
#: decision somebody makes rather than a sweep quietly finding less to check.
MIN_SCANNED_FILES = 300
MIN_DOC_FILES = 20
MIN_ANCHORED_BLOCKS = 6
MIN_ANCHORED_FILES = 4


@dataclasses.dataclass(frozen=True)
class Block:
    """One run of prose, with the line numbers kept so a failure can name the line and not the file."""

    path: str
    lines: tuple[tuple[int, str], ...]

    @property
    def start(self) -> int:
        return self.lines[0][0]

    @property
    def text(self) -> str:
        return " ".join(line for _, line in self.lines)

    def where(self, token: str) -> int:
        """The line inside this block that carries ``token``."""
        for number, line in self.lines:
            if token in line:
                return number
        return self.start


#: The repository's marker for "this line quotes a retired number on purpose", declared by
#: ``tools/check_citations.py::LINE_MARKER`` and used in ADRs as an HTML comment. Honoured here for
#: the same reason it exists there: a page that explains *which* figure was superseded has to be
#: able to write the old one down. Skipping the line rather than the block is deliberate — the
#: sentence beside a marked one is still checked.
#:
#: This is a reuse and not a second convention. ``register/citations.py::RETIRED_CLAIMS`` is what
#: makes a stale spelling fail anywhere in the tree; this gate is about a *live* figure disagreeing
#: with `CLAIM`. Two questions, one marker.
RETIRED_LINE_MARKER = "[retired]"

#: A claim sentence is a handful of lines. Blank lines delimit prose, but a Python literal has
#: none: `RETIRED_CLAIMS` is one ~250-line tuple, so a single `observed=` string carrying the
#: anchor phrase pulled twenty-four unrelated entries' figures into one "sentence" and failed the
#: sweep on all of them. Measured 2026-08-25, when registering the pair did exactly that.
#:
#: A cap and not a smarter parser: the sweep's subject is prose, and prose that needs more than
#: this many lines to state one accuracy is not the thing this gate can speak about. The failure
#: mode a cap introduces is a *missed* claim spanning more lines, which the per-document
#: both-figures sweep still covers.
MAX_BLOCK_LINES = 12


def _blocks(path: str, text: str) -> list[Block]:
    current: list[tuple[int, str]] = []
    out: list[Block] = []

    def flush() -> None:
        if current:
            out.append(Block(path=path, lines=tuple(current)))
            current.clear()

    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            flush()
            continue
        if OWN_BLOCK.match(line) or len(current) >= MAX_BLOCK_LINES:
            flush()
        if RETIRED_LINE_MARKER in line:
            # Not appended, so its numbers are invisible to the sweep. Not a flush either: the
            # lines either side of it belong to one sentence and stay together.
            continue
        current.append((number, line.strip()))
    flush()
    return out


def _grep_exempt_paths() -> tuple[str, ...]:
    """``register/citations.py::GREP_EXEMPT_PATHS``, read as data rather than restated.

    That tuple already answers "which files quote a superseded figure on purpose" — it exists
    because `citations.py` has to hold every retired pattern it declares, and it names two sealed
    contract files whose headers forbid the ``[retired]`` line marker. This gate asks a *different*
    question (a live figure disagreeing with `CLAIM`, not a retired spelling reappearing), but the
    set of files allowed to write an old number down is the same set, so it is read and not copied.
    Learned the hard way: registering the pair on 2026-08-25 made this sweep fail on twenty-four
    unrelated entries inside `RETIRED_CLAIMS`, in the one file whose whole job is to quote them.

    ``ast`` rather than an import, for the reason ``tools/check_citations.py`` gives: the module is
    data and nothing should need it at runtime to run a gate.
    """
    import ast

    source = (ROOT / "src" / "governed_bi" / "register" / "citations.py").read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.AnnAssign) or not isinstance(node.target, ast.Name):
            continue
        if node.target.id != "GREP_EXEMPT_PATHS" or node.value is None:
            continue
        value = ast.literal_eval(node.value)
        assert isinstance(value, tuple) and value, "GREP_EXEMPT_PATHS is empty or not a tuple"
        return value
    raise AssertionError(
        "register/citations.py no longer declares GREP_EXEMPT_PATHS, so this gate cannot tell "
        "which files are allowed to quote a superseded figure. Do not drop the exemption silently."
    )


def _scanned() -> list[tuple[str, str]]:
    """Every prose file in scope, as ``(repo-relative path, text)``, with the walk's floor asserted."""
    paths = [p for p in sorted(DOC_ROOT.rglob("*.md"))]
    docs = len(paths)
    for root in CODE_ROOTS:
        paths += [p for p in sorted(root.rglob("*.py")) if "__pycache__" not in p.parts]

    assert docs >= MIN_DOC_FILES, (
        f"walked {docs} markdown files under {DOC_ROOT}, below the floor of {MIN_DOC_FILES}. The "
        "documents are the whole point of this module, so a walk that reaches almost none of them "
        "makes every assertion below vacuous — this is finding D13, not a missing document."
    )
    assert len(paths) >= MIN_SCANNED_FILES, (
        f"walked {len(paths)} files under {DOC_ROOT} and {[str(r) for r in CODE_ROOTS]}, below the "
        f"floor of {MIN_SCANNED_FILES}. A root is pointing at nothing and the sweeps below would "
        "pass over an empty set."
    )
    exempt = {(ROOT / rel).resolve() for rel in _grep_exempt_paths()} | {SELF}
    return [
        (p.relative_to(ROOT).as_posix(), p.read_text(encoding="utf-8"))
        for p in paths
        if p.resolve() not in exempt
    ]


def _claim_text() -> str:
    """``CLAIM``'s value, and the assertion that it is still one literal to take it from."""
    tree = ast.parse(CLAIM_SOURCE.read_text(encoding="utf-8"), filename=str(CLAIM_SOURCE))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == "CLAIM" for target in node.targets):
            continue
        assert isinstance(node.value, ast.Constant) and isinstance(node.value.value, str), (
            f"{CLAIM_SOURCE.name} no longer declares CLAIM as a single string literal. Every "
            "assertion in this module reads the figures out of that literal, so a computed or "
            "f-string CLAIM leaves the documents with nothing to be checked against."
        )
        return node.value.value
    raise AssertionError(
        f"no module-level CLAIM in {CLAIM_SOURCE}. It is the single source of the accuracy pair for "
        "the CLI, the review surfaces and every document that states it; if it moved, point this "
        "module at its new home rather than restating the figures here."
    )


def _tokens(pattern: re.Pattern[str], text: str) -> list[str]:
    """Matches in order, deduplicated, so a failure message reads as the prose does."""
    return list(dict.fromkeys(pattern.findall(text)))


def test_the_claim_is_one_literal_that_names_both_figures_and_both_populations() -> None:
    """The source this module measures everything against, measured first.

    Two figures and two populations: the licensed-and-tableful accuracy over the turns it was
    measured on, and the same figure over every covered turn including the ones whose gold reads no
    table. If `CLAIM` carried one figure, the anchored sweep below would accept a document that
    states one and quietly drops the other, which is the reading that made the second figure look
    like a rival number instead of the same one.
    """
    claim = _claim_text()
    figures = _tokens(FIGURE, claim)
    populations = _tokens(POPULATION, claim)

    assert len(figures) == 2, (
        f"CLAIM carries {len(figures)} accuracy figures ({', '.join(figures) or 'none'}) and the "
        "pair is two. Everything below compares prose against this set, so a set of one accepts "
        "half a claim and a set of three does not identify what the documents should say."
    )
    assert len(populations) == 2, (
        f"CLAIM carries {len(populations)} populations ({', '.join(populations) or 'none'}). A "
        "number is not a measurement until it says what it was measured over, and a figure whose "
        "denominator is not written down cannot be seen to move."
    )


def test_every_sentence_that_states_the_measured_accuracy_states_only_the_claims_numbers() -> None:
    """Anchored on the wording, so it still matches after somebody edits the digits.

    Both halves of the 2026-08-24 defect are in scope. A document that kept the old figure states a
    number `CLAIM` does not carry, and a document that kept the old `n` states a population it does
    not carry; each names its own file and line here rather than waiting for a reader to notice a
    comment contradicting the code beside it.

    The anchor is narrow on purpose. `docs/` states many other measurements in the same shape, and
    "delivered accuracy is 3.16x" in `failure-modes.md` is a live figure that has nothing to do with
    this pair. Matching the phrase this claim is always written with keeps the sweep precise; the
    cost is that a paraphrase escapes it, which is what the four-document check below is for.
    """
    claim = _claim_text()
    figures = _tokens(FIGURE, claim)
    populations = _tokens(POPULATION, claim)

    anchored = [
        block
        for path, text in _scanned()
        for block in _blocks(path, text)
        if ANCHOR.search(block.text)
    ]
    assert len(anchored) >= MIN_ANCHORED_BLOCKS, (
        f"found {len(anchored)} blocks stating a measured accuracy, below the floor of "
        f"{MIN_ANCHORED_BLOCKS}. Either the claim has been reworded out of most of the prose that "
        "carries it — in which case this anchor no longer finds the sentences it exists to check — "
        "or the walk is not reaching the files. Both make the assertion below vacuous."
    )
    found_in = sorted({block.path for block in anchored})
    assert len(found_in) >= MIN_ANCHORED_FILES, (
        f"every anchored block came from {len(found_in)} file(s) ({', '.join(found_in)}), below the "
        f"floor of {MIN_ANCHORED_FILES}. The claim is stated in the glossary, the return path, ADR "
        "0015 and the reproducer; a sweep that sees fewer files is not seeing the documents."
    )

    offenders: list[str] = []
    for block in anchored:
        for token in _tokens(FIGURE, block.text):
            if token not in figures:
                offenders.append(
                    f"{block.path}:{block.where(token)} states accuracy {token}, which CLAIM does "
                    f"not carry (CLAIM has {', '.join(figures)})"
                )
        for token in _tokens(POPULATION, block.text):
            if token not in populations:
                offenders.append(
                    f"{block.path}:{block.where(token)} states population n={token}, which CLAIM "
                    f"does not carry (CLAIM has {', '.join(populations)})"
                )
        if not any(figure in block.text for figure in figures):
            offenders.append(
                f"{block.path}:{block.start} states a measured accuracy and names none of CLAIM's "
                f"figures ({', '.join(figures)}) — a claim with its number deleted still reads like "
                "a claim"
            )

    assert not offenders, (
        "prose states an accuracy number that `tools/reproduce_observation.py::CLAIM` does not "
        "carry:\n  "
        + "\n  ".join(offenders)
        + "\n\nCLAIM is the single source. Change the figure there, then make the prose agree — do "
        "not restate a figure in this gate."
    )


def test_the_four_documents_that_state_the_pair_still_state_both_of_its_figures() -> None:
    """The positive control, and the half that catches a paraphrase falling behind.

    `docs/open-work.md` names these four as stating the pair as prose with nothing keying them to
    `CLAIM`. Requiring both figures in each file is weaker than the anchored sweep — a file can
    contain the right figure in one sentence and a stale one in another — but it is the check that
    survives rewording, and it fires on the shape the defect actually took: a site left holding only
    the number the measurement replaced. `glossary.md` is the case that needs it, stating the
    all-covered-turns figure in a sentence that does not repeat the anchor phrase.

    It is also the control for the other sweep. These paths are asserted to be in the scanned set,
    so a walk that silently stops reaching `docs/` fails here by name instead of passing green.
    """
    claim = _claim_text()
    figures = _tokens(FIGURE, claim)
    scanned = dict(_scanned())

    missing_from_scan = [path for path in STATES_THE_PAIR if path not in scanned]
    assert not missing_from_scan, (
        f"the walk did not reach {', '.join(missing_from_scan)}. These are the documents this "
        "module exists to check; if one was renamed, follow it here, and if the walk is pointing at "
        "the wrong root then every assertion in this file has been passing over nothing."
    )

    offenders: list[str] = []
    for path in STATES_THE_PAIR:
        text = scanned[path]
        for figure in figures:
            if figure not in text:
                offenders.append(f"{path} never states {figure}")

    assert not offenders, (
        "a document that states the accuracy pair is missing one of the two figures "
        f"`tools/reproduce_observation.py::CLAIM` carries ({', '.join(figures)}):\n  "
        + "\n  ".join(offenders)
        + "\n\nEither the pair moved and this document did not, or it now states only half the "
        "claim. Both are the 2026-08-24 defect."
    )
