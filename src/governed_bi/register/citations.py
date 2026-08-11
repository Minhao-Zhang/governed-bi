"""World-describing numbers with provenance, plus retired-literal patterns.

Rule: every number that describes the world carries an artifact path and a
date. Retired claims carry a regex :data:`RETIRED_CLAIMS` that
``tools/check_citations.py`` greps for. :attr:`Citation.artifact` is never
empty — use ``git-history:<path>`` when the producing code is gone.
:data:`GREP_EXEMPT_PATHS` is data the gate reads (this file must quote retired
patterns). Nothing imports this module at runtime.
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

#: Paths the retired-literal grep gate must skip (this file quotes every pattern).
GREP_EXEMPT_PATHS: tuple[str, ...] = (
    "src/governed_bi/register/citations.py",
    # **Sealed contract files, exempt by name and not by tier.** Both quote the retired
    # reasoning-effort figure while explaining why their test exists. Their headers say "do not edit
    # this file", so the ``[retired]`` marker is not available, and the honest record of that is an
    # exemption someone had to type — not a non-fatal root that would have swallowed the other six
    # with them. Remove either line if its file is ever unsealed.
    "tests/model/test_embedder_contract.py",
    "tests/serve/test_session_contract.py",
)


@dataclass(frozen=True, slots=True)
class Citation:
    """One measured fact, with provenance."""

    claim: str
    #: Where the measurement lives. Repo-relative path, or ``git-history:<path>``
    #: when the producing code is gone. Never empty.
    artifact: str
    #: ISO date the measurement was taken.
    measured: str
    note: str = ""


CITATIONS: tuple[Citation, ...] = (
    # ── the indexed text ────────────────────────────────────────────────────
    Citation(
        "indexing prose instead of an identifier list: gold-table coverage "
        "0.6405 -> 0.7026 (+6.21pp, +193 -117, p=1.9e-05, MDE 4.03pp); "
        "schema recall@3 0.9511 -> 0.9652 (+1.41pp, +29 -10, p=0.0034, MDE 1.30pp)",
        "runs/ablation/summary-form-1351-20260805.json", "2026-08-05",
        "All 1351 test questions, paired, one process, both deltas ABOVE their own "
        "detection floor. The 342-question screen got +6.11pp and this got +6.21pp, "
        "which is the cleanest replication in this repository. Coverage runs the real "
        "pass_two_retrieve + apply_budgets path. Two things it is not: the prose arm "
        "uses the corpus's existing machine-written `body`, not summaries authored for "
        "retrieval, so this is a LOWER bound on deliberate writing; and resolve/connect "
        "closure is excluded, so it measures retrieval alone rather than final licensing.",
    ),
    Citation(
        "the query-form question is independent of the summary-form question: "
        "rewriting facet_schema's query is null under both document forms",
        "runs/ablation/summary-form-1351-20260805.json", "2026-08-05",
        "342 questions, paired: recall@3 +0.88pp with identifier lists and -0.29pp with "
        "prose; gold-table coverage +0.64pp and 0.00pp; every p >= 0.45; interaction "
        "-1.17pp / -0.64pp. The rewriter was working -- 0 of 342 returned the question "
        "unchanged, output is real keyword soup -- so this is 'rewriting does not help', "
        "not 'the model did not rewrite'. Retires the 4.4pp claim in register/facets.py.",
    ),
    # ── retrieval channels ──────────────────────────────────────────────────
    Citation(
        "schema shortlist recall: BM25 0.736@1 / 0.844@3 / 0.906@10; "
        "embedding 0.694@1 / 0.852@3 / 0.953@10",
        "runs/ablation/e1-shortlist-curated.json", "2026-07-31",
        "57 schemas, all 1351 test questions, text-embedding-3-large. BM25 wins at @1 "
        "IN THIS ARCHITECTURE, which is a single-channel shortlist ranking the 57 "
        "schema summaries directly (channel_counts: bm25_fallback 1351) over "
        "corpus_curated -- NOT v2's five-facet route, and not this corpus. It has been "
        "read as a general claim about the served system and it is not one: measured "
        "2026-08-05 through the five-facet path on gold-semantic-layer-20260804, "
        "lexical-only reaches 0.5468@1 / 0.7018@3 while semantic-only reaches "
        "0.9064@1 / 0.9825@3. Compare it only to another shortlist.",
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
        "git-history:docs/v1/plans/measurement-and-observability.md", "2026-08-01",
        "No model, no API key, about four minutes. So 56.3% must be read against "
        "~100%, not against a lower hidden ceiling. Producing code: "
        "git-history:src/governed_bi/eval/oracle.py",
    ),
    Citation(
        "unwinnable questions: 4",
        "git-history:docs/v1/plans/measurement-and-observability.md", "2026-08-01",
        "Three retails questions over the harness row cap, plus one SELECT * gold "
        "hash defect. Replaces the retired '69' below.",
    ),

    # ── statistical power ───────────────────────────────────────────────────
    Citation(
        "McNemar discordance between adjacent arms 16-20%; MDE 3.23% at n=1351 and "
        "2.64% over the full 2030-question split",
        "git-history:docs/v1/plans/measurement-and-observability.md", "2026-08-01",
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
        "git-history:docs/v1/plans/measurement-and-observability.md", "2026-08-01",
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

    #: Regex long enough to avoid unrelated numbers, loose enough for observed spellings.
    pattern: str
    #: One spelling actually observed, so the pattern can be tested.
    observed: str
    why: str
    replaced_by: str


RETIRED_CLAIMS: tuple[RetiredClaim, ...] = (
    RetiredClaim(
        pattern=r"15\.0{1,2}\s*[,/]\s*75\.0{1,2}",
        observed='"Claude-Opus-4.8": (15.0, 75.0, 1.50)',
        why="v1's Opus 4.8 price, 3x the published 5.00/25.00/0.50 "
            "(platform.claude.com, read 2026-08-03). Left wrong by the commit that "
            "fixed the adjacent row: 4567eeb, 'fix(cost): price the cached input "
            "share, and use real prices', corrected gpt-5.6-luna in the same hunk that "
            "added Opus 4.8's cache rate without checking the other two figures. A fix "
            "can edit an adjacent copy and still leave it wrong.",
        replaced_by="nothing in this tree -- measure/price.py and its PRICE_TABLE are "
        "deleted, because a price list kept here has to track a provider's by hand",
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
        # Both spellings occur (prose as a percentage, the artifact as a fraction), so
        # a pattern covering only one misses half the reappearances.
        pattern=r"(69\.9\s*%|0\.699)|"
                r"(schema[_ ]?pick|pick[_ ]?accuracy)\D{0,20}(69\.9|0\.699)",
        observed="schema_pick_accuracy: 0.699",
        why="measured through a rate-limited embedder; re-measured at 91.0% with "
            "quota free. The degradation counter existed and no gate read it.",
        replaced_by="91.0%, with facet channel state as a quotability input",
    ),
    RetiredClaim(
        # `EX 0.049` in a table cell matched none of the prose spellings, so the figure sat
        # unmarked in docs/ while the gate reported clean. Widened to any `EX` within a few
        # characters of the number.
        pattern=r"EX[^\n]{0,12}0\.049|0\.049\s*EX",
        observed="EX was 0.049.",
        why="every absolute EX this repository produced before 2026-08-06 was measured "
            "through a grader that compared Postgres `numeric` cells as strings. "
            "`_cell`'s fallback was `return str(value)` and `Decimal` is neither `int` nor "
            "`float`, so `Decimal('100.00')` and `Decimal('100.0')` -- the same number -- "
            "graded `result_mismatch`, indistinguishable in the artifact from a wrong "
            "answer. The figure is an underestimate of unknown size, and the size is a "
            "function of the schema's numeric-column density, so the cross-schema "
            "comparisons do not hold either. Retired rather than corrected: the arm "
            "cannot be regraded without re-executing it, because the artifact kept the "
            "fingerprint and not the rows.",
        replaced_by="nothing yet. The grader is now BIRD-Obfuscation's own "
        "`normalise_result`, transcribed and fingerprint-identical, so the next ladder "
        "produces the first EX this repository has that is comparable to published BIRD. "
        "The 51.2% table coverage and 62.5% schema reachability beside it are NOT retired: "
        "they are licensing measurements and do not touch the grader.",
    ),

    # ── the pre-2026-08-05 arms ─────────────────────────────────────────────
    # Every figure below came out of a scored arm run before 2026-08-05, when crashes were
    # counted as refusals, the notes tools raised NameError, unbuilt schemas competed in the
    # router and the routing index carried PII. Eight of them were declared; the rest stayed
    # in live docstrings and were left there three times over because this gate did not
    # object. Each keeps its mechanism in the call site and loses its magnitude here.
    RetiredClaim(
        pattern=r"recall@3\W{0,4}0\.609|0\.442\s+of\s+the\s+time|worse\s+\(0\.417\)",
        observed="the gold schema 823 times (``recall@3`` 0.609), a single-component pick "
                 "reached it 0.442 of the time ... worse (0.417)",
        why="the shortlist-and-pick triple `connect_node` cites for licensing every "
            "component instead of picking one. All three rates are drawn from a population "
            "that is not the one they name. The argument does not need them: a pick caps "
            "reachability at recall@1 by construction, whatever recall@1 turns out to be.",
        replaced_by="nothing yet -- the bound is arithmetic, not a measurement",
    ),
    RetiredClaim(
        pattern=r"44%\s+of\s+questions\s+whose\s+schema|median\s+worst\s+rank\s+9",
        observed="44% of questions whose schema was routed correctly have a gold table "
                 "outside the 8-table cap, median worst rank 9",
        why="the per-type budget's miss rate, measured offline against a router that "
            "shortlisted schemas nothing had built. What survives is the shape: a cap can "
            "discard a gold table, so pass two must carry `budget_dropped` out or the miss "
            "reads as 'retrieval never found it'.",
        replaced_by="nothing yet",
    ),
    RetiredClaim(
        pattern=r"32[\s,]244|semantic channel won \*{0,2}0 times",
        observed="Measured over 32 244 documents ... the semantic channel won **0 times**",
        why="the audit that killed the `max` combine rule, run over an index carrying "
            "unbuilt schemas and PII-bearing routing text. The count is void and the "
            "conclusion is not: BM25-after-saturation runs 0.60-0.97 and cosine 0.00-0.635, "
            "so `max` is a lexical-only rule by construction.",
        replaced_by="the range comparison in retrieve/fuse.py, which needs no arm",
    ),
    RetiredClaim(
        pattern=r"0\.6316|0\.6228",
        observed="two processes, 0.6316 vs 0.6228",
        why="the coverage pair that exposed hash-seed-dependent Steiner seeding. The *gap* "
            "is the finding and it is a same-corpus, same-code contrast, so it survives; "
            "the two levels are void like every other absolute from that arm.",
        replaced_by="nothing -- `min(remaining, key=str)` removed the variance the pair "
        "measured, so there is no second number to take",
    ),
    RetiredClaim(
        pattern=r"0\.851\s+and\s+0\.877|routing recall was 0\.851",
        observed="arms whose routing recall was 0.851 and 0.877",
        why="two routing-recall arms measured with unbuilt schemas in the shortlist. Quoted "
            "only to show that scoring an absent `licensed` as zero published a 0.000 "
            "ceiling for arms that had routed well, and the KeyError prevents that at any "
            "recall.",
        replaced_by="nothing yet",
    ),
    RetiredClaim(
        pattern=r"0/57\s+on\s+a\s+corpus|corpus\s+measured\s+at\s+0\.608",
        observed="once reported 0/57 on a corpus measured at 0.608",
        why="an absent `reached_gold` read as zero, so the artifact said the run reached no "
            "gold schema at all and contradicted the corpus's own figure. Both ends of the "
            "contradiction are void; the defect -- absence scored as a measurement -- is why "
            "the field is written explicitly.",
        replaced_by="nothing yet",
    ),
    RetiredClaim(
        pattern=r"16%\s+of\s+turns\s+crash",
        observed="a run where 16% of turns crash",
        why="a crash rate taken while a crash was also being counted as a refusal, so the "
            "run disagreed with itself about what the numerator was. That a crashed turn "
            "needs `error_type` to be actionable holds at any rate above zero.",
        replaced_by="nothing yet",
    ),
    RetiredClaim(
        pattern=r"2\.5\s*pp\s+against\s+a\s+2\.3\s*pp",
        observed="moved the baseline arm +2.5pp against a 2.3pp detection threshold",
        why="v1's reasoning-effort incident, sized on a ladder whose detection threshold was "
            "itself computed over the contaminated population. It reached three places as "
            "the reason `llm_reasoning_effort` is a comparability knob; it is one because an "
            "unrecorded live config field makes two ladders hash as one experiment, which is "
            "true at any effect size.",
        replaced_by="nothing yet",
    ),
    RetiredClaim(
        pattern=r"21\s+points\s+higher",
        observed="re-measured 21 points higher with quota free",
        why="the distance between the retired 69.9% and its re-measurement: a difference "
            "taken from a void endpoint is void. The port's rule stands on its own -- a "
            "rate-limited embedder returns a degraded ranking, and a run that does not raise "
            "records it as if it were a real one.",
        replaced_by="nothing yet; quote the two figures and their conditions, not the gap",
    ),
    RetiredClaim(
        pattern=r"5%\s+of\s+answerable-but-wrong",
        observed="5% of answerable-but-wrong turns on the xhigh arm",
        why="counted on the xhigh arm through the pre-2026-08-06 grader, so the "
            "'answerable-but-wrong' denominator is built out of the same `Decimal`-as-string "
            "mismatches the numerator is meant to measure. That hashing column names made "
            "this grader stricter than BIRD is a property of the code.",
        replaced_by="nothing yet",
    ),
    RetiredClaim(
        pattern=r"EX\s+0\.667|0\.667\s+vs\.?\s+(the\s+)?agent|context\s+\(0\.667\)",
        observed="curated/curated_sme flow EX 0.667 vs. agent 0.267",
        why="ADR 0002's flow-versus-agent pair and the parity floor read off it. Absolute EX "
            "from before 2026-08-06, so the `Decimal`-as-string comparator applies to both "
            "arms and to the gap between them.",
        replaced_by="nothing yet. ADR 0002's decision -- one agentic serve path -- was taken "
        "on topology, not on this pair",
    ),
    RetiredClaim(
        pattern=r"shortlist\s+0\.952|pick\s+(accuracy\s+of\s+)?0\.873",
        observed="v1 measured shortlist 0.952 / pick 0.873 at top-10",
        why="v1's shortlist-and-pick pair, offered in ADR 0005 as the bar v2's route recall "
            "must clear. It was measured on the architecture that ADR replaces, so it is not "
            "a bar anything here can be held to even if it were sound.",
        replaced_by="the e1-shortlist-curated.json figures above, the only shortlist numbers "
        "in this tree with an artifact -- and comparable only to another shortlist",
    ),
    RetiredClaim(
        pattern=r"56\.3\s*%",
        observed="v1 spent a long time reading 56.3% against an unknown ceiling",
        why="v1's headline EX, and every absolute EX before 2026-08-06 went through the "
            "`Decimal`-as-string comparator (see the 0.049 entry). What it is quoted for -- "
            "that a score is unreadable without its ceiling -- is a point about the missing "
            "denominator and does not need this numerator.",
        replaced_by="nothing yet",
    ),
    RetiredClaim(
        pattern=r"0\.444\s+vs\.?\s+0\.503|coverage\s+ceiling\s+6-9\s*pp",
        observed="lexical and embedded runs have different ceilings (0.444 vs 0.503 at "
                 "top_n=3)",
        why="the lexical-versus-embedded coverage ceilings, measured 2026-08-04 with unbuilt "
            "schemas still in the shortlist. That the retrieval channel is an arm and "
            "belongs in the artifact name is a comparability rule; it needs no gap.",
        replaced_by="nothing yet",
    ),
)


def _assert_citations_are_sourced() -> None:
    """Import-time: no citation without an artifact and a date, and every retired
    pattern matches its own observed spelling. The rule this file enforces, enforced
    on this file.
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
        # Pattern must match its own observed spelling.
        if not rx.search(claim.observed):
            bad.append(f"{claim.pattern!r} does not match its own observed spelling")
    if bad:  # pragma: no cover - import-time guard
        raise AssertionError("retired-claim patterns are unusable: " + "; ".join(bad))


_assert_citations_are_sourced()
