# Opus-4.8 / high — full ladder (curator_phase_a=v2) — progress log

- **Run dir:** `runs/datalake/20260731T233457Z-opus48-high-ladder`
- **Code:** HEAD=e8a2633 — freshly merged `origin/main` (59 upstream commits: M2–M5 milestones — pooled data-lake harness, embedder-first routing, N18 serve refactor lifting `ServeRuntime`/rails to module level, M4b file splits, N15 auto-analysis + questions sidecar, M5 review) into `mars`. Local proxy commits preserved (proxy provider + Aurora datasource + eval-resume + embedding token-clip patch). Merge was **clean, 0 conflicts** — `mars` had already absorbed earlier `origin/main` states, so git auto-resolved the refactor.
- **Model:** `Claude-Opus-4.8` · `llm_reasoning_effort = "high"` — **this is the experiment**: the prior Opus reference was medium; the Sonnet-5 run was high. This isolates *effort* on the strongest model (Opus-4.8/med → Opus-4.8/high). It also answers the residual caveat from the Sonnet run (which confounded model+effort).
- **Prompt:** `curator_phase_a=v2` (pinned; NOT v3 — v3 made SME worse on Opus).
- **Config:** arms `baseline,seeded,curated,curated_sme` (full ladder) · split `test` (N=1351) · `--build-workers 20 --workers 20` · no replicate.
- **Merge health (pre-launch):**
  - `uv sync --extra proxy` clean (130 packages; merge bumped pyproject/uv.lock).
  - **Full test suite: 1761 passed, 16 skipped, 1 xfailed, 0 failed** (451s). Proxy+datasource tests: 27 passed.
  - API drift noted from the refactor: `LangChainChatClient.complete(system, user)` is positional (was different); `.complete_with_usage()` returns a `(text, usage_dict)` tuple. `run_datalake` reads DB only from `--pg-dsn` (unchanged). Prompt registry is code (`prompts/registry.py`), variants via `r.variants("curator_phase_a")` → `['v1','v2','v3']`; CLI `--prompt curator_phase_a=v2` parses correctly.
- **Verification (pre-launch, 2026-07-31T23:3xZ):**
  - LLM round-trip → `PONG`, `model_name: Claude-Opus-4.8`, usage `{input 25, output 5}` ✓
  - Embedding round-trip → dim 3072 (text-embedding-3-large) ✓
  - `extra_body` → `additionalModelRequestFields.output_config.effort: high`, `thinking.type: adaptive` ✓
  - DB → connects via psycopg, `current_database=bird`, 69 user schemas ✓
  - DSN cached at `/tmp/gbi_dsn.txt` (chmod 600); never logged/committed (contains live password).

## Compare-to references (same v2 code path, same split)
| Arm | Opus-4.8/med (`20260730T034522Z`) | Sonnet-5/high (`20260731T150024Z`) |
|-----|-----|-----|
| baseline    | 0.392 | 0.241 |
| seeded      | 0.470 | 0.296 |
| curated     | 0.585 | 0.489 |
| curated_sme | 0.583 | 0.484 |

The open question this run answers: **does raising Opus-4.8 from medium to high reasoning effort move the ~0.585 curated ceiling?** If high ≈ medium, the ceiling is model *capability* (not thinking budget); if high > medium, effort is a live lever the medium runs left on the table.

## Timeline (UTC)
- 2026-07-31T23:34:57Z — Merged origin/main (clean), synced venv, full suite green (1761 passed), verified Opus-4.8/high live (LLM+embed+extra_body+DB). Launching full 4-arm ladder. Corpora rebuild on Opus/high (curator is an Opus/high pass now). Serve + build both 20/20.
- 2026-07-31T23:35Z — Run PID 68525, log `data/logs/20260731T233457Z-opus48-high-ladder.log`, out `runs/datalake/20260731T233457Z-opus48-high-ladder`. Mechanical seed pass ran clean over 57 schemas (deterministic join/metric seeding; the "N failed lookup" lines are benign alias-resolution misses, same as prior runs).
- 2026-07-31T23:38Z — **Curator build live and healthy on Opus/high.** Curator calls returning HTTP 200 across schemas (books, authors, car_retails, cs_semester, codebase_comments…). No errors/400s. Build ETA ~50-60 min (curator is now an Opus/high pass — individual calls slower than medium).
- 2026-08-01T00:25Z — **BUILD COMPLETE 57/57 all arms (~50 min, 0 caps).** works_cycles (73-table BLOB tail) built clean on both curated and curated_sme — **embedding token-clip patch held, 0 embedding 400s** (the exact failure that killed the v3 curated_sme index). It was the final schema: curator ran many batches (b1…b3+) at high effort, then the SME phase-B pass, then the embed index. Twin stamping: 115/1200 gradeable have a same-schema train twin (worst: video_games, superhero, olympics — identical to prior runs). Serve starting on baseline, 20 workers/arm.
- 2026-08-01T00:27Z — **Serve live**, baseline arm, 20 workers (w1…w18 firing). SME byte-identical this run: 6 schemas (vs 10 on Sonnet-5, 3 on Opus-medium) — folded on the other 51. Serve ETA ~4-5 h across 4 arms at high effort (expect the plateau-then-burst row cadence — normal, not a stall). Per-arm EX captured to the results doc as each arm completes.
- 2026-08-01T01:0xZ — **baseline COMPLETE: EX=0.417** (gradeable 0.439, routing_recall 0.860, cond_EX|routed 0.482, decoy 0.0867, refuse 0.016, crash 0.000; 1351/1351 in 1765s, 0 crashes). **vs Opus-4.8/medium baseline 0.392 → +2.5pp**; vs Sonnet-5/high 0.241 → +17.6pp. First read: raising Opus reasoning effort med→high **does lift** the pure-capability arm (baseline carries no corpus), a small but real gain. Opus dominates Sonnet at equal (high) effort by ~18pp on raw capability. seeded arm serving (412/1351).
- 2026-08-01T01:3xZ — **seeded COMPLETE: EX=0.480** (gradeable 0.508, routing_recall 0.853, cond_EX|routed 0.562, decoy 0.0306, refuse 0.020, crash 0.000; 0 crashes). Seed lift baseline→seeded **+6.3pp**. **vs Opus-4.8/medium seeded 0.470 → +1.0pp**; vs Sonnet-5/high 0.296 → +18.4pp. The effort gain narrows from +2.5pp (baseline) to +1.0pp (seeded) — as deterministic seeding supplies join/metric knowledge, there is less for extra thinking to recover. curated arm serving (64/1351) — the arm that defines the ceiling.
- 2026-08-01T02:2xZ — **curated COMPLETE: EX=0.563** (gradeable 0.597, routing_recall 0.873, cond_EX|routed 0.641, decoy 0.0008, refuse 0.014, crash 0.000; 0 crashes). Curator lift seeded→curated **+8.3pp**. **vs Opus-4.8/medium curated 0.585 → −2.2pp** — the effort gain *reverses on the curated arm*: baseline +2.5pp → seeded +1.0pp → curated **−2.2pp**. **High reasoning effort does NOT break the ~0.585 ceiling; it lands flat-to-slightly-below.** decoy 0.0008 — governance restores near-total obfuscation immunity (matches Opus/med 0.0007), so the −2.2pp is not a routing regression. routing_recall 0.873 (Opus/med curated was 0.894 — a touch lower). curated_sme (final arm) serving next.
- 2026-08-01T~01:56Z — **INTERRUPT.** Process 68525 silently killed (no traceback) at curated_sme 1025/1351 — the recurring /tmp-wipe signature: log cuts off mid-flight at 01:55:56 (all 20 workers on HTTP 200s), the uv-managed venv Python (`/opt/spark/.local/share/uv/python/...`) was wiped (dangling symlink), and `/tmp/gbi_dsn.txt` cleared. baseline/seeded/curated fully complete + scored; only curated_sme short by 326 rows; summary.json not yet written.
- 2026-08-01T02:11Z — **Recovered + resumed.** `uv sync --extra proxy` (reinstalled boto3/botocore/etc, ~90s), rebuilt DSN from secret → `/tmp/gbi_dsn.txt` (chmod 600), verified config (Opus-4.8/high) + DB(bird) + **LLM PONG**. Corpora intact (57/57 markers all arms; newest corpus mtime 00:24, 0 files touched after 01:00 → the `corpus_content_hash -> None` resume warning is the known lazy-recompute artifact, not a content change). Resumed with `--resume-from`; kept the 1025 scored rows, serving the remaining ~326 (log `...-resume.log`). Serve resumed 02:17Z.
- 2026-08-01T02:3xZ — **RUN COMPLETE (4/4 arms). curated_sme EX=0.561** (gradeable 0.598, routing_recall 0.877, decoy 0.0015, crash 0.000). **SME delta = −0.1pp** (curated 0.563 → 0.561, −2 correct) — the same SME-flat verdict, now the third independent confirmation (Opus/med −0.2pp, Sonnet/high −0.4pp). SME fold: 51/57 folded (258 clarifications applied), 6/57 byte-identical. summary.json written.
  - **Quotable this time.** All arms crash_rate 0.0, n_re_served 0; corpus_validation 0 findings all arms; treatment_delivered=true every pair. Only asterisk: the resume splits curated_sme's rows across two invocations (`corpus_content_hash → None` lazy-recompute warning), but corpora were byte-identical across it — number is sound.

## FINAL LADDER (from summary.json)
| Arm | ex_lenient | ex_gradeable | ex_no_twin | routing_recall | decoy | crash | n_re_served |
|-----|-----|--------------|------------|----------------|-------|-------|-------------|
| baseline    | 0.4167 | 0.4392 | 0.4249 | 0.8601 | 0.0867 | 0.000 | 0 |
| seeded      | 0.4804 | 0.5083 | 0.4876 | 0.8527 | 0.0306 | 0.000 | 0 |
| curated     | 0.5625 | 0.5967 | 0.5705 | 0.8734 | 0.0008 | 0.000 | 0 |
| curated_sme | 0.5611 | 0.5975 | 0.5714 | 0.8771 | 0.0015 | 0.000 | 0 |

Deltas: seeded−baseline **+6.4pp** (86 correct) · curated−seeded **+8.2pp** (111 correct) · curated_sme−curated **−0.1pp** (−2 correct). Total governance lift baseline→curated **+14.6pp**.

## Verdict
**Med→high reasoning effort does NOT break the ~0.585 curated ceiling.** Effort gain: baseline +2.5pp → seeded +1.0pp → curated **−2.2pp** → curated_sme −2.2pp. Effort helps only where the model is un-governed; governance erases (then slightly reverses) it. Combined with Sonnet-5/high (0.489 curated, weaker model), **neither newer-tier nor higher-effort moves the governed ceiling** — it is a corpus+task property, not a model-thinking property. Opus-4.8 beats Sonnet-5 by ~7-18pp/arm at equal effort; model choice dominates effort (≤2.5pp/arm).
