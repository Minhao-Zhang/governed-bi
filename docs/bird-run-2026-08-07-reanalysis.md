# Re-analysis of the 2026-08-07 BIRD run: most of the gap is retrieval

Written 2026-08-07 against the run artifacts of the same day. The run's own report
(`temp_result.md`, untracked at the repo root) is accurate on every figure it prints and wrong
in the one inference it draws from them. This document records the correction and the evidence
for it.

**Nothing here is quotable.** Two quotability gates failed on this run and a third could not be
evaluated (§7). These are diagnostic figures. They say where to look next; they are not results.

Not an ADR. ADRs record decisions and are never edited to match later reality; this records a
measurement and is meant to be superseded by a clean run. It sits beside
[`v2-postmortem-and-v3-brief.md`](v2-postmortem-and-v3-brief.md), the other dated analysis in
this directory.

---

## 1. What was run

| | |
|---|---|
| Date | 2026-08-07 |
| Engine | `7ce3a9d` (branch `v2`) |
| Dataset | BIRD-Obfuscation `22fe2a6`, `test_final.jsonl`, N=1351 |
| Agent model | Claude-Opus-4.8, reasoning effort `high` |
| Utility model | Claude-Sonnet-5 |
| Both governed-bi arms | `--embed --top-n 10 --workers 10` |
| Corpus A | rich summaries. **Not in version control** — see §8 |
| Corpus B | bare facts, = `BIRD-corpus @ 05fb31a` |
| Comparison system | WrenAI @ `06e4c42`, one LLM call per question, up to 3 train question→gold-SQL few-shots |

Headline EX as reported: WrenAI **0.679**, corpus A **0.585**, corpus B **0.532**.

**`--top-n 10` did not take effect.** Every turn of both arms shows exactly three routed
schemas (A `{0: 5, 3: 1346}`, B `{0: 24, 3: 1327}`), the register default. The driver wrote the
override into `question["knobs_resolved"]` and printed it in its header; `Session.turn` then
replaced it and `_run_one` never re-applied it. So the stated configuration and the served one
diverged, and `knobs_resolved` — the field that would have shown it — was absent from all 1351
rows. Every recall figure below is therefore at `route_top_n = 3`, and the wider window this run
meant to test was never measured. Both halves are fixed; neither was at the time.

**Token totals, chat only, embeddings excluded.** WrenAI 14,787,274 input / 192,428 output over
1,350 calls. Corpus A 52,198,986 / 1,192,320 over 6,730 calls; corpus B 52,326,797 / 1,287,980
over 6,635. Not like-for-like: one call per question against an agentic loop plus four facet
rewriters. Read as the cost of a different architecture, not as the same task done dearer — and
note the attempt cap did not terminate a capped turn at the time, so an unknown share of the
agent-side total is round trips against a cap that could not be cleared.

**One governance signal exists only on the comparison side.** WrenAI's grader flags
`decoy_touch` — the prediction references a renamed trap column — at 66/1351 (0.0489), of which
40 were still correct. governed-bi emits no per-row equivalent; its governance surfaces instead
as guardrail refusals (0 errors on both arms) and the licensing gate. That asymmetry is worth
closing before the two are compared on anything but EX.

## 2. The correction

The run report attributes the WrenAI gap chiefly to WrenAI's train few-shots — a generation-side
advantage. Reconstructed per row, the funnel does not support that as the main term.

Over the 1224 table-reading questions (§3), corpus A licensed every gold table on 1108 and
failed on 116. Lifting those 116 to the arm's own success rate given retrieval — 0.675 — moves
EX from **0.615 to 0.675**. That is about **6 of the 9.6 points** separating corpus A from
WrenAI on the same population (0.615 vs 0.711).

**Retrieval is roughly 62% of the gap. Generation is the rest.** Few-shots remain a real
advantage; they are the smaller half.

The same holds for the corpus lever. Rich summaries buy **+4.5 points of retrieval success**
and **+2.8 points of conditional EX** (§5), so the A-vs-B difference also acts mostly through
retrieval rather than through what the agent does with what it got.

The report's facts were right; the inference from them was not. And the funnel was
reconstructible at all only because `gold_sql`, `licensed` and `schemas` happen to sit on every
row of the artifact — nothing in the harness was designed to make this question answerable.

## 3. 127 questions have degenerate gold

127 of 1351 (9.4%) have a gold SQL that reads no table: the answer is a constant, e.g.
`SELECT v.c0 FROM (VALUES ('india')) AS v(c0)`.

| population | n | corpus A | corpus B | WrenAI |
|---|---:|---:|---:|---:|
| degenerate gold | 127 | 0.276 | 0.228 | 0.354 |
| table-reading | 1224 | 0.615 | 0.561 | 0.711 |

Excluding them lifts every arm about 3 points and changes neither the ranking nor the gap. They
are excluded from §4–§6 because a question with no gold table has no retrieval funnel.

## 4. The funnel, corpus A, over the 1224 table-reading questions

| stage | count | rate |
|---|---:|---:|
| gold schema routed | 1148/1224 | 0.938 |
| all gold tables licensed | 1108/1224 | 0.905 |
| EX where retrieval succeeded | 748/1108 | **0.675** |
| EX where retrieval failed | 5/116 | **0.043** |
| EX overall | 753/1224 | 0.615 |

A retrieval failure is close to fatal: 0.043 against 0.675, a factor of about 16. There is no
meaningful recovery path once a gold table is not licensed, which is why the 116 rows dominate
the arithmetic in §2 despite being 9.5% of the population.

## 5. Corpus A vs corpus B, same 1224 questions

| arm | retrieval OK | EX given retrieval OK |
|---|---:|---:|
| A (rich summaries) | 1108/1224 = 0.905 | 0.675 |
| B (bare facts) | 1052/1224 = 0.860 | 0.647 |

Both terms move in the same direction: richer text is found more often (+4.5pp) and is also
somewhat more useful once found (+2.8pp). The first term is the larger one.

## 6. Head to head against WrenAI, same 1224 questions

| | count |
|---|---:|
| both correct | 669 |
| governed-bi A only | 84 |
| WrenAI only | 201 |
| neither | 270 |

Of the 201 WrenAI-only wins, retrieval was **fine on 140 (69.7%)** and **failed on 61 (30.3%)**.

| by outcome | n | | by grade_detail | n |
|---|---:|---|---|---:|
| answered | 157 | | result_mismatch | 142 |
| capped | 35 | | capped | 35 |
| refused | 5 | | missing_prediction | 15 |
| clarification | 2 | | refused | 5 |
| crashed | 2 | | clarification / crashed | 2 / 2 |

The 140 with sound retrieval are the generation half of §2's split: the engine had the tables
and still produced the wrong rows.

**The attempt cap is a separate, cheaper target.** 59 turns capped on corpus A over the
table-reading population; WrenAI answered 35 of them correctly. Those are questions the
benchmark says are answerable and this arm ran out of attempts on. §9 notes that the cap
default has since changed, so 59 is a property of this run and not of the engine today.

### Reconstruction tolerance

An independent recount over the same artifact, extracting gold tables by regex over `gold_sql`
and comparing against `licensed`, reproduces every figure in §3–§6 exactly except the routed and
licensed counts, which land within 3 rows of 1224 (≤0.3pp). The slack is in gold-table
extraction, not in the grading. No conclusion above turns on it.

## 7. Two instrument defects on this run

**`facet_schema.semantic` reported `failed` on every turn of both arms** (1346 A / 1327 B). The
schema facet — the one that decides which schemas are in play — ran lexical-only for the entire
run, on both corpora. Being equal across arms, it does not confound A-vs-B; it does bound the
absolute numbers, and it sits directly upstream of the 0.938 routing rate in §4.

Two things about the report of it:

- The root cause was traced in code, not logged. Schema-type assets appear to have no vectors
  available to the channel, so it had nothing to search with.
- **The channel state is conflated.** In `serve/nodes/facets.py::_channels_for`, a declared
  channel that was not consulted is written as `failed`, and `retrieve/semantic.py`'s
  `semantic_search` declines whenever the embedder, the vector store, or the query vector is
  missing. A runtime "nothing indexed for this type" and a genuine error are therefore
  indistinguishable in the artifact. Which one this was cannot be read off the run.

**`knobs_resolved` is absent from all 1351 rows of both arms** — the key is missing, not empty.
The run cannot be joined to its own configuration. Every comparability claim about it rests on
the command line someone typed, recorded in prose.

## 8. Why none of this is quotable

| gate | corpus A | corpus B |
|---|---|---|
| `outcome` (crash-free) | **fail** — 3 crashed | **fail** — 1 crashed |
| `facet_channels` | **fail** — §7 | **fail** — §7 |
| `knobs_resolved` | `cannot_evaluate` — §7 | `cannot_evaluate` |

By this project's own rule the gate refuses rather than warns. Two failures and one
`cannot_evaluate` means these are diagnostic figures until a clean run exists.

**Corpus A is not in version control.** The committed `BIRD-corpus @ 05fb31a` is corpus B, the
weaker arm, and its commit message describes a replacement it did not perform. So the stronger
number has no treatment identity anyone else can pin: `corpus_content_hash` digests a tree that
exists on one machine. A separate effort is landing corpus A on a branch; until it merges,
0.585 names a corpus that cannot be checked out.

## 9. Why new numbers will not compare to these

The engine has moved past `7ce3a9d`. Three of the changes break comparability with this run,
all bearing on `run_query_attempt_cap`, a `Role.comparability` knob:

| commit | change | effect on the numbers above |
|---|---|---|
| `ef6a8da` | the attempt cap now actually terminates the turn — it previously returned a `ToolMessage` nothing consumed | turns that ran past the cap here do not exist afterwards |
| `efeffc2` | a capped turn that had one passing attempt is no longer recorded as `answered` | **re-grading will lower EX.** That is the honest direction |
| `040817b` | cap default 3 → 5 | a different arm, by declaration |

So the 59 capped turns in §6 and every EX in this document belong to the arm as it stood at
`7ce3a9d`. Do not difference them against anything measured later.

## 10. What follows

1. Fix `facet_schema`'s semantic channel and split `failed` from "nothing to search with", so
   the next run can tell an error from an empty index.
2. Stamp `knobs_resolved`, so the next run can be joined to its configuration.
3. Land corpus A in the corpus repository so the stronger arm has a commit.
4. Re-run. Then, and only then, is there a number to quote — and the ordering above says the
   first lever to measure is retrieval, not prompting.

*Artifacts, server-side, one JSONL per arm under `runs/eval/` plus
`preds_full_usage_graded.jsonl` for the comparison system. Filenames are not reproduced here;
they carry an internal deployment codename. Every figure above was recomputed from those three
files rather than copied from the run's own summary.*
