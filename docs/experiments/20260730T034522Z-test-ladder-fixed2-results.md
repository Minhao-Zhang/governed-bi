# Test-split ladder (FIXED code + C11) — results

Run dir: `runs/datalake/20260730T034522Z-test-ladder-fixed2` · HEAD=3f599b6 +C11 · curator_phase_a=v2 · Opus-4.8 medium · test · N=1351/arm

> **Not reproducible from local history. Verified 2026-07-30.** The run's `manifest.json` records
> `git_sha: 3f599b605389c4ed8eb32c6a0b83176dc50045ad`, and that object does not exist here.
> `git cat-file -t` on it fails with "could not get object info", it is in none of the 248 commits
> reachable from any local ref, and `git ls-remote origin` publishes exactly one branch,
> `refs/heads/main` at `49536ac`. The commit was never pushed and is presumably still local to the
> machine that ran this. Consequence: the manifest's `allow_git_sha_drift: false` records a gate that
> cannot be evaluated, because there is nothing to compare the sha against. The `+C11` in the header
> says the working tree carried an uncommitted change on top of that commit anyway, so even recovering
> the commit would not recover the code that ran. Quote the numbers below as measured, not as
> reproducible.

| Arm | EX | EX_gradeable | routing_recall | cond_EX\|routed | decoy | refuse | crash |
|-----|-----|--------------|----------------|----------------|-------|--------|-------|
| baseline    | 0.392 | 0.418 | 0.859 | 0.453 | 0.1150 | 0.019 | 0.000 |
| seeded      | 0.470 | 0.499 | 0.849 | 0.551 | 0.0477 | 0.019 | 0.000 |
| curated     | 0.585 | 0.618 | 0.894 | 0.651 | 0.0007 | 0.006 | 0.000 |
| curated_sme | 0.583 | 0.618 | 0.894 | 0.650 | 0.0007 | 0.006 | 0.000 |

**Ladder: baseline 0.392 → seeded 0.470 (+7.8pp) → curated 0.585 (+11.5pp).** Total governance lift **+19.3pp EX**. decoy collapses 0.115 → 0.048 → 0.0007 (curated is near-immune to obfuscation traps).

The EX column above is `ex_lenient`, which is the BIRD-comparable denominator and not the harness's
pre-registered headline. That is `ex_no_twin` (`metrics.HEADLINE_RATE`), which runs 0.404 → 0.484 →
0.591 → 0.594 on the same four arms, a +18.7pp ladder over the 1085 of 1200 scored rows with no
structural gold twin in train. Twin stamp coverage is complete (`n_twin_unstamped: 0` on every arm),
so the +19.3pp figure is not carried by recall.

### vs pre-fix (20260729T234601Z, old step-budget + v1)
| Arm | pre-fix EX | fixed EX | Δ |
|-----|-----------|----------|-----|
| baseline | 0.395 | 0.392 | −0.3 (noise; no corpus) |
| seeded | 0.484 | 0.470 | −1.4 (noise) |
| curated | 0.571 | 0.585 | **+1.4** (uncapped/richer corpora + join-identity) |
| curated_sme | n/a (not eval — 28/57 capped) | 0.583 | first genuinely-measured SME |

### Build-health: fix verification
| | pre-fix | fixed+C11 |
|-|---------|-----------|
| recursion caps | 30/57 | **0/57** |
| curated_sme byte-identical to curated | 28/57 | **3/57** (all honest "no clarifications", incl. works_cycles which now builds the richest corpus) |
| works_cycles curated | capped, seed-only | 125 tool calls, 73 tables + 81 joins + 20 metrics + 6 terms + 5 few-shots |

### The SME result
curated_sme EX **0.583** ≈ curated **0.585** (−0.2pp, within noise). On this test split, with the curator now able to author a full corpus, **folding the clarifications ledger does not move EX** — the curated corpus already captures what the SME layer would add. This is the first time the SME arm was measurable at all (pre-fix it was byte-identical to curated on 28/57 schemas purely because the agent got capped before writing the ledger). A real SME effect, if any, is now bounded to noise on test; a larger-N or train diagnostic would tighten the bound.

## Error analysis
Full **stage-by-stage funnel** analysis (retrieval → schema-pick → table coverage → SQL logic →
delivery) for both the curator and SME rounds is in
[20260730T034522Z-curated-sme-error-analysis.md](20260730T034522Z-curated-sme-error-analysis.md).
Key findings surfaced during the deep dive:

- **Grader = BIRD's grader.** Our `hash_grade.py` normalisers are code-identical to BIRD-Obfuscation's
  `pipeline/_db.py`; we run BIRD's exact lenient/strict EX. BIRD-comparable metric excludes only the 26
  order-sensitive qids (§9.3) — **NOT** the 125 frozen `VALUES` gold (BIRD grades those; 90 are real
  misses). **BIRD-lenient EX: baseline 0.397 → seeded 0.473 → curated 0.588 → curated_sme 0.586.**
- **The entire governance lift is one stage — table coverage** — collapsing 281→58 across the ladder
  (gradeable); routing barely moves (`EX|routed` 0.453→0.650).
- **Gradeable waterfall (1200):** OK 742 · retrieval 29 · **schema-pick 78** · table 58 · SQL-shape 107
  · SQL-value 179 · refused 7.
- **Schema-pick is systematic near-twin confusion** (mondial_geo↔world 10×, simpson↔law_episode 8×,
  food_inspection↔_2 6×); 44 misroutes *overrode a better retrieval rank*. Top fix, ~+4.75pp.
- **Stage-4 errors are OVER-elaboration, not under-spec**: spurious DISTINCT (78) outnumbers missing
  (19) 4:1; lookup-join+LIKE where gold used a direct code (17) — ignoring the evidence hint.
- **SME rewrote 50% of queries (596/1200) for net +1** — high-variance zero-mean; elaboration and
  simplification are both coin-flips.

## Fixes exercised in this run
- **step budget** (079d1fe): `derive_step_budget` per schema; `--max-agent-steps` now real → 0 caps.
- **join identity** (079d1fe): `on_clause_digest` in the join id → no silent edge-collision loss.
- **failure-path trace** (079d1fe): `curator_trace.jsonl` per schema → diagnosed the 3 byte-identical SME schemas exactly.
- **C11** (local, this session): `read_corpus(todo_only=True)` now clipped to 20k → works_cycles no longer wedges.
