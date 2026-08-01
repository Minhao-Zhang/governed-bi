# Opus-4.8 / high — full ladder (curator_phase_a=v2) — results

Run dir: `runs/datalake/20260731T233457Z-opus48-high-ladder/<inner>` · HEAD=e8a2633 (freshly merged origin/main, 59 upstream commits; local proxy + Aurora + embedding token-clip patch preserved; full suite 1761 passed) · curator_phase_a=**v2** · **Claude-Opus-4.8 / reasoning_effort=high** · test split · N=1351/arm · 20/20 workers.

> **The experiment:** isolate *reasoning effort* on the strongest model. The reference Opus run was **medium**; this is **high**, same v2 code path and data. It answers (a) whether raising effort moves the ~0.585 curated ceiling, and (b) the residual caveat from the Sonnet-5/high run, which confounded model+effort — pairing this with Opus-medium isolates effort, and pairing with Sonnet-5/high isolates model-at-high-effort.

## The ladder

*(populated as each arm completes)*

| Arm | ex_lenient | ex_gradeable | routing_recall | cond_EX\|routed | decoy | refuse | crash |
|-----|-----|--------------|----------------|----------------|-------|--------|-------|
| baseline    | 0.417 | 0.439 | 0.860 | 0.482 | 0.0867 | — | 0.000 |
| seeded      | 0.480 | 0.508 | 0.853 | 0.562 | 0.0306 | — | 0.000 |
| curated     | 0.563 | 0.597 | 0.873 | 0.641 | 0.0008 | — | 0.000 |
| curated_sme | 0.561 | 0.598 | 0.877 | 0.637 | 0.0015 | — | 0.000 |

**Ladder (ex_lenient): baseline 0.417 → seeded 0.480 (+6.4pp) → curated 0.563 (+8.2pp) → curated_sme 0.561 (−0.1pp).** Total governance lift baseline→curated **+14.6pp**. ex_no_twin (headline, excludes same-schema train twins): 0.425 → 0.488 → 0.571 → 0.571. Deltas from summary.json: seeded−baseline +86 correct, curated−seeded +111 correct, curated_sme−curated **−2 correct**. All arms crash_rate 0.0, n_re_served 0. decoy collapses 0.087 → 0.031 → 0.0008 (governance restores near-total obfuscation immunity — matches Opus/med's 0.0007).

## Reference ladders (same v2 code path + data + split)

| Arm | Opus-4.8/**med** (`20260730T034522Z`) | Sonnet-5/**high** (`20260731T150024Z`) | Opus-4.8/**high** (this run) |
|-----|------|------|------|
| baseline    | 0.392 | 0.241 | **0.417** (+2.5pp vs med) |
| seeded      | 0.470 | 0.296 | **0.480** (+1.0pp vs med) |
| curated     | 0.585 | 0.489 | **0.563** (−2.2pp vs med) |
| curated_sme | 0.583 | 0.484 | **0.561** (−2.2pp vs med) |

## Findings

1. **Raising Opus-4.8 from medium to high reasoning effort does NOT break the ~0.585 curated ceiling — it lands slightly below it (0.563, −2.2pp).** The effort gain is real but shrinks and then reverses as the corpus fills in the knowledge: **baseline +2.5pp → seeded +1.0pp → curated −2.2pp → curated_sme −2.2pp**. On the bare-capability arm (baseline, no corpus) extra thinking recovers a little (0.392→0.417); once the governed corpus supplies the joins/metrics/schema semantics the model would otherwise reason toward, extra thinking has nothing left to add and, on the curated arm, is marginally *worse* than medium. **The ~0.585 ceiling is not a thinking-budget ceiling.** Combined with the Sonnet-5/high result (0.489 curated, a *weaker* model), neither of the two obvious "turn up the model" levers — newer tier, or more reasoning effort — moves the governed ceiling. The ceiling is a property of the governed-corpus + task, not of how hard the model thinks.

2. **The effort×governance interaction is monotone and interpretable.** Effort helps most exactly where the model is most on its own (baseline), and the benefit decays to zero (then slightly negative) as governance substitutes for reasoning. This is the mirror image of the Sonnet finding (where *governance* helped a weak model more): here *effort* helps the un-governed arm more. Both say the same thing — governance and raw model effort are partial substitutes, not complements, on this benchmark.

3. **SME is flat again — the third independent confirmation.** curated_sme 0.561 vs curated 0.563 = **−0.1pp** (−2 correct), inside noise. This matches Opus/med v2 (−0.2pp) and Sonnet/high (−0.4pp). Folding the clarifications ledger does not move EX on any (model, effort) cell tested. This run folded on 51/57 schemas (258 clarifications applied), byte-identical on 6/57 — the most-folded SME arm of the three, and still flat. The SME null result is now robust across two models and two effort levels.

## Effort isolation (this run's contribution)

The Sonnet-5/high run left a confound: its ladder differed from Opus/med by *both* model and effort. This run resolves it by holding the model fixed (Opus-4.8) and varying only effort:

| | baseline | seeded | curated | curated_sme |
|--|--|--|--|--|
| Opus-4.8 / **medium** | 0.392 | 0.470 | 0.585 | 0.583 |
| Opus-4.8 / **high**   | 0.417 | 0.480 | 0.563 | 0.561 |
| **Δ (high − med)**    | **+2.5pp** | **+1.0pp** | **−2.2pp** | **−2.2pp** |

Effort-alone is a small positive on raw capability that governance erases. Pairing this table with Sonnet-5/high (curated 0.489, −9.6pp vs Opus/med even at high effort) isolates the other axis: **model choice dominates effort** — Opus-4.8 beats Sonnet-5 by ~7-18pp per arm at equal (high) effort, while med→high moves any single arm by ≤2.5pp.

## Build health

- **57/57 schemas built, 0 caps** (~50 min). works_cycles (73-table BLOB-bearing tail) built clean on curated and curated_sme — the embedding token-clip patch (`mars_proxy._sanitize_embedding_inputs`, clip to 8000 tokens via tiktoken cl100k_base) held, **0 embedding 400s**.
- Merge health: origin/main merged clean (0 conflicts); `uv sync` clean; full test suite **1761 passed, 16 skipped, 1 xfailed, 0 failed**; proxy+datasource tests 27 passed. Pre-launch live verification: LLM PONG (`Claude-Opus-4.8`, usage reported), embeddings 3072-dim, `extra_body.output_config.effort=high` + adaptive thinking, DB `bird`/69 schemas.

## Caveats

- **Quotability:** unlike the Sonnet-5 run, this run's arms all have **crash_rate = 0.0** and **n_re_served = 0**, so the harness's unequal-crash void does not apply; `corpus_validation` is 0 findings on all four arms and `treatment_delivered = true` for every arm pair. The one asterisk is the **resume**: the run was interrupted by a /tmp wipe at curated_sme 1025/1351 and resumed via `--resume-from`. curated_sme's rows are therefore scored across two process invocations, and the harness emitted `resuming with changed knobs (corpus_content_hash → None)`. That warning is the known lazy-recompute artifact — the corpora were verified byte-identical across the resume (57/57 build markers intact, newest corpus mtime 00:24, **zero files modified after 01:00**; the wipe hit /tmp and the venv, not the run dir). So the curated_sme number is sound as measured; the −0.1pp SME delta is well inside the noise floor regardless.
- The med↔high comparison is same-model, same-v2-code, same-split, same-seeded-layer — a clean effort isolation. The one thing it does *not* control: the curator corpora were rebuilt on Opus/high (the curator is itself an Opus/high pass here vs Opus/med in the reference run), so "curated at high" bundles *serve-effort* and *curator-effort*. The baseline/seeded arms (no curator) are pure serve-effort reads and both show the +1 to +2.5pp effort gain; the reversal appears only once the high-effort curator corpus is in play. If anything that means high-effort *curation* is what erases the serve-effort gain — a curator-quality question, not a serve-reasoning one.
- No USD: `Claude-Opus-4.8` and `text-embedding-3-large` are absent from the price table, so cost metrics are null (token counts only, by design).
