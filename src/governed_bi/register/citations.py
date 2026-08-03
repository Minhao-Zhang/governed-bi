"""Every number in this codebase that describes the world, with where it came from.

**The defect this file prevents: a number describing the world, written as a
literal, pinned to nothing.** v1's instances —

* ``recall@3 drops 0.70 -> 0.35`` spread to five places in ``src/`` including an
  operator-facing warning, a summary print and four comments, **and a test asserted
  it**, which is how it survived. The repo's own artifact measures 0.844 vs 0.852
  at @3, and at @1 BM25 is *ahead*. Wrong by 2.4x.
* A price-table entry matching neither the new price nor the old, overstating a
  measured run **nine-fold**.
* "69 unwinnable questions", verified as **4**. Overstated 17x.
* "+46 points of headroom", revised to "3–5" with nothing in between to justify
  either.

And why it kept happening: **the fix landed where it was found and never reached
the adjacent copies.** So the rule is mechanical — every world-describing number
carries an artifact and a date, and the retired ones carry a pattern a grep gate
can fail on.

**Two properties this module needs that are easy to get wrong.**

*Every citation has an artifact.* :attr:`Citation.artifact` is a plain ``str``,
never optional. When the producing code was deleted in the v1 removal the artifact
is written ``git-history:<path>``, which is a locatable reference — ``git show
main:<path>`` retrieves it. An optional field here would make "the artifact is git
history" indistinguishable from "no artifact recorded", for exactly the numbers
most likely to be re-quoted.

*The grep gate must not fail on this file.* It necessarily contains every retired
pattern, so :data:`GREP_EXEMPT_PATHS` declares the exemption rather than leaving
the checker to hard-code one. A checker with a built-in exemption is a checker
whose exemption nobody reviews.

``tools/check_citations.py`` reads this module. **That tool does not exist yet** —
see ``docs/plans/v2-implementation-decisions.md``. Nothing imports this module at
runtime; it is a test surface, deliberately.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "Citation",
    "CITATIONS",
    "RetiredClaim",
    "RETIRED_CLAIMS",
    "GREP_EXEMPT_PATHS",
]

#: Paths the retired-literal grep gate must skip.
#:
#: This module and the lessons document both quote the retired claims in order to
#: retire them. Declared here so the exemption is data the gate reads, not a
#: special case inside the gate.
GREP_EXEMPT_PATHS: tuple[str, ...] = (
    "src/governed_bi/register/citations.py",
    "docs/lessons-from-v1.md",
    "docs/plans/v2-implementation-decisions.md",
)


@dataclass(frozen=True, slots=True)
class Citation:
    """One measured fact, with provenance."""

    claim: str
    #: Where the measurement lives. A repo-relative path, or ``git-history:<path>``
    #: when the producing code was deleted in the v1 removal. **Never empty** — see
    #: the module docstring.
    artifact: str
    #: ISO date the measurement was taken.
    measured: str
    note: str = ""


CITATIONS: tuple[Citation, ...] = (
    # ── retrieval channels ──────────────────────────────────────────────────
    Citation(
        "schema shortlist recall: BM25 0.736@1 / 0.844@3 / 0.906@10; "
        "embedding 0.694@1 / 0.852@3 / 0.953@10",
        "runs/ablation/e1-shortlist-curated.json", "2026-07-31",
        "57 schemas, all 1351 test questions, text-embedding-3-large. BM25 WINS at "
        "@1. Falsifies the retired claim below by 2.4x.",
    ),
    Citation(
        "RRF fusion: 0.733@1 / 0.871@3 / 0.922@10",
        "runs/ablation/e3-fusion.json", "2026-07-31",
        "Wins at @1 and @3, loses at @10. So 'do not fuse' is right at top_k=10 and "
        "wrong at a tight one — and v2 routes at top-3.",
    ),
    Citation(
        "BIRD obfuscation is translation, not randomisation: German, French and "
        "Spanish physical names plus paired decoy columns",
        "runs/datalake/20260731T233457Z-opus48-high-ladder/20260731T233545Z",
        "2026-08-02",
        "Why BM25 wins at @1: physical names carry real semantics in the wrong "
        "language, which is exact for BM25 and weak for cross-lingual embedding.",
    ),

    # ── the grader and the ceiling ──────────────────────────────────────────
    Citation(
        "grader ceiling 1347/1351 = 0.9970 on gold SQL submitted directly",
        "docs/plans/measurement-and-observability.md", "2026-08-01",
        "No model, no API key, about four minutes. So 56.3% must be read against "
        "~100%, not against a lower hidden ceiling. Producing code: "
        "git-history:src/governed_bi/eval/oracle.py",
    ),
    Citation(
        "unwinnable questions: 4",
        "docs/plans/measurement-and-observability.md", "2026-08-01",
        "Three retails questions over the harness row cap, plus one SELECT * gold "
        "hash defect. Replaces the retired '69' below.",
    ),

    # ── statistical power ───────────────────────────────────────────────────
    Citation(
        "McNemar discordance between adjacent arms 16-20%; MDE 3.23% at n=1351 and "
        "2.64% over the full 2030-question split",
        "docs/plans/measurement-and-observability.md", "2026-08-01",
        "The interventions under test move 1-2pp, so EX cannot resolve them at any "
        "price. Ladder cost spans $16 to $4,065 for the same effect size.",
    ),
    Citation(
        "curated -> curated_sme is a DELIVERED null: -0.1pp, discordance 9.0%, "
        "MDE 2.29pp",
        "runs/datalake/20260731T233457Z-opus48-high-ladder/20260731T233545Z",
        "2026-08-02",
        "Treatment was delivered (802/1351 turns had a note injected; corpora "
        "differ), unlike the earlier null where a path bug made the corpora "
        "byte-identical. The apparent +5.6pp for injected turns is selection: the "
        "curated arm, which has no notes at all, shows the same 6.7pp split on the "
        "same questions.",
    ),

    # ── cost and caching ───────────────────────────────────────────────────
    Citation(
        "prompt caching on the Anthropic path: cache_read 0 across 49,401,157 "
        "input tokens",
        "runs/datalake/20260731T233457Z-opus48-high-ladder/20260731T233545Z",
        "2026-08-02",
        "No cache_control breakpoint existed anywhere in v1's source. "
        "OpenAI-compatible providers cache automatically (v1 measured 55-58% hit "
        "rates); Anthropic requires the explicit marker.",
    ),
    Citation(
        "median context 17,782 chars; median per-turn input 30,923 tokens",
        "runs/datalake/20260731T233457Z-opus48-high-ladder/20260731T233545Z",
        "2026-08-02",
        "The gap is accumulated tool returns, which is therefore the prefix worth "
        "caching — not the context block.",
    ),
    Citation(
        "curator averages ~293k tokens per turn (58.1M input over 198 turns)",
        "docs/plans/measurement-and-observability.md", "2026-08-01",
        "Against a ~500k TPM local quota, so a full ladder rate-limits even at one "
        "build worker. One ladder is roughly 30 hours locally.",
    ),

    # ── the curator ─────────────────────────────────────────────────────────
    Citation(
        "the curator agent wrote nothing on 6 of 57 schemas; the distribution is a "
        "cliff, 0 or >=24",
        "runs/datalake/20260731T233457Z-opus48-high-ladder/20260731T233545Z",
        "2026-08-02",
        "codebase_community, disney, donor, formula_1, legislator, mondial_geo. "
        "Their descriptions are empty and the physical names are obfuscated.",
    ),
    Citation(
        "not a budget problem: median utilisation 33%, 0/57 exhausted, "
        "budget-to-question correlation -0.353",
        "runs/datalake/20260731T233457Z-opus48-high-ladder/20260731T233545Z",
        "2026-08-02",
        "works_cycles spent 1,583 tool calls at a budget of 339 and asked nothing.",
    ),
    Citation(
        "mondial_geo: 0 of 42 table and 0 of 275 column descriptions",
        "git-history:scripts/pick_evidence_probe.py", "2026-08-01",
        "One of the six zero-write schemas, measured independently by a routing "
        "probe whose finding was that the LLM picker's 15 candidate tables are "
        "filled alphabetically.",
    ),
    Citation(
        "join identity without an ON digest: 33 of 57 schemas lost at least one "
        "gold-derived edge before the agent ran (soccer_2016 kept 32/54, "
        "mondial_geo 67/87)",
        "git-history:tests/test_curator_join_identity.py", "2026-07-29",
        "Two relationships between the same table pair collapsed and the last write "
        "won, with no error and no validator finding.",
    ),

    # ── dataset properties ─────────────────────────────────────────────────
    Citation(
        "structural train/test twins: 246 of 2030 test questions (12.1%), up to 46% "
        "in one schema",
        "git-history:src/governed_bi/eval/leakage.py", "2026-07-30",
        "Id-level disjointness says nothing about a test question whose gold SQL "
        "statement already exists in train.",
    ),
    Citation(
        "the dataset ships order_sensitive_qids.json: 25 of 2030 questions (1.23%) "
        "it tells you to exclude",
        "git-history:src/governed_bi/eval/leakage.py", "2026-07-30",
        "Never opened by v1, so each was scored wrong for every arm — uniform "
        "across arms, harmless to a delta, silently depressing every absolute EX.",
    ),

    # ── v1 scale, for the rewrite's arithmetic ──────────────────────────────
    Citation(
        "v1 was 86,746 lines: src 42,131 (eval 18,704, analyst 6,751, curator "
        "4,724, retrieval 1,958), tests 40,804, scripts 3,811",
        "git-history:src", "2026-08-02",
        "17 files over 1,000 lines, one at 5,085, and 30% of all code in files over "
        "1,000 lines. Recoverable via `git show main:<path>`.",
    ),
)


@dataclass(frozen=True, slots=True)
class RetiredClaim:
    """A falsified number, and a pattern a grep gate can fail on."""

    #: A regular expression. Long enough not to false-positive on an unrelated
    #: number — a bare ``0\\.35`` would match anything — and loose enough to catch
    #: the spellings the claim actually appeared in, since v1 had it in five places
    #: with different wording.
    pattern: str
    #: One spelling actually observed in v1, so the pattern can be tested against
    #: something real rather than against itself.
    observed: str
    why: str
    replaced_by: str


RETIRED_CLAIMS: tuple[RetiredClaim, ...] = (
    RetiredClaim(
        pattern=r"15\.0{1,2}\s*[,/]\s*75\.0{1,2}",
        observed='"Claude-Opus-4.8": (15.0, 75.0, 1.50)',
        why="v1's Opus 4.8 price, 3x the published 5.00/25.00/0.50 "
            "(platform.claude.com, read 2026-08-03). The reason it is here rather "
            "than filed with the other stale prices: it was left wrong BY THE COMMIT "
            "THAT FIXED THE ADJACENT ROW. 4567eeb, 'fix(cost): price the cached input "
            "share, and use real prices', corrected gpt-5.6-luna from (2.0, 8.0) to "
            "(0.20, 1.20, 0.02) and in the same hunk changed Opus 4.8 from "
            "(15.0, 75.0) to (15.0, 75.0, 1.50) -- it added the cache rate and did "
            "not check the other two. So this is a stronger instance than "
            "check_imports' 'the fix never reached the adjacent copies': the fix "
            "*edited* the adjacent copy and still left it 3x over, under a commit "
            "message asserting real prices. Found 2026-08-03 while sourcing the v2 "
            "price table, by an agent that went looking for the incident behind the "
            "rule it was implementing.",
        replaced_by="measure/price.py's PRICE_TABLE, where every row carries the date "
                    "it was observed and the URL it was read from",
    ),
    RetiredClaim(
        pattern=r"0\.70.{0,40}0\.35|0\.35.{0,40}0\.70",
        observed="recall@3 0.70 vs BM25 0.35",
        why="routing recall@3 for embedding vs BM25. Wrong by 2.4x, and it was the "
            "STATED REASON for not fusing the two channels. It reached five places "
            "in src/ including an operator warning, and a test asserted it.",
        replaced_by="the e1-shortlist-curated.json figures above",
    ),
    RetiredClaim(
        pattern=r"halv\w*\s+(the\s+)?routing\s+recall",
        observed="degradation halves routing recall",
        why="the prose form of the same retired claim.",
        replaced_by="BM25 is 0.8pp behind at @3 and AHEAD at @1",
    ),
    RetiredClaim(
        pattern=r"69\s+unwinnable|69\s+gold_unusable",
        observed="69 unwinnable questions",
        why="overstated 17x.",
        replaced_by="4",
    ),
    RetiredClaim(
        pattern=r"\+\s*46\s+points|46\s+points\s+of\s+headroom",
        observed="+46 points available",
        why="headroom derived by summing per-class error counts, which double-counts "
            "every query wrong along more than one dimension — 61% of them.",
        replaced_by="3-5, from a counterfactual oracle arm rather than a sum",
    ),
    RetiredClaim(
        pattern=r"\(\s*2\.0\s*,\s*8\.0\s*\)",
        observed='"gpt-5.6-luna": (2.0, 8.0)',
        why="a price tuple matching neither the new price nor the old, overstating a "
            "measured run nine-fold.",
        replaced_by="a dated price table returning unmeasured for an unknown model",
    ),
    RetiredClaim(
        # Both spellings occur: prose quoted it as a percentage, the artifact as a
        # fraction. A pattern covering only one is a gate that misses half the
        # reappearances — which the import-time guard below caught on this exact
        # entry.
        pattern=r"(69\.9\s*%|0\.699)|"
                r"(schema[_ ]?pick|pick[_ ]?accuracy)\D{0,20}(69\.9|0\.699)",
        observed="schema_pick_accuracy: 0.699",
        why="measured through a rate-limited embedder; re-measured at 91.0% with "
            "quota free. The degradation counter existed and no gate read it.",
        replaced_by="91.0%, with facet channel state as a quotability input",
    ),
)


def _assert_citations_are_sourced() -> None:
    """Import-time invariant: no citation without an artifact and a date.

    The rule this file exists to enforce, enforced on this file.
    """
    unsourced = [c.claim[:60] for c in CITATIONS if not c.artifact or not c.measured]
    if unsourced:  # pragma: no cover - import-time guard
        raise AssertionError(f"citations with no artifact or date: {unsourced}")

    import re

    bad = []
    for claim in RETIRED_CLAIMS:
        try:
            rx = re.compile(claim.pattern)
        except re.error as err:  # pragma: no cover - import-time guard
            bad.append(f"{claim.pattern!r}: {err}")
            continue
        # A pattern that does not match the spelling actually observed in v1 is a
        # gate that catches nothing — v1's own retired-literal test had exactly that
        # shape for one entry.
        if not rx.search(claim.observed):
            bad.append(f"{claim.pattern!r} does not match its own observed spelling")
    if bad:  # pragma: no cover - import-time guard
        raise AssertionError("retired-claim patterns are unusable: " + "; ".join(bad))


_assert_citations_are_sourced()
