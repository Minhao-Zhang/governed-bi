# Governed BI — Experiment Log

An append-only, chronological record of experiment activity for this project
(setup, runs, results, decisions, anomalies). Newest entries go at the bottom.

## Formatting guidelines

- **One entry per event.** Each entry is a level-2 heading (`##`) whose text is the
  UTC timestamp of when the entry is written, in the format `YYYY-MM-DD HH:mm UTC`.
- **Always fetch a fresh timestamp** (`date -u '+%Y-%m-%d %H:%M UTC'`) at the moment
  of writing — never guess or reuse an old one.
- **Append only.** Never edit or delete past entries; if something was wrong, write a
  new entry that corrects it.
- Under each heading, write plain prose and/or bullets: what happened, the command
  run, the outcome, and any decision or follow-up.
- Keep it factual and specific — commands, counts, arms, DB/schema names, hashes,
  and file paths — so a future agent can reconstruct the state.
- **Report results as TWO tables**, so rates are always backed by raw counts:
  1. **Counts** — per arm: `n` (questions), `correct`, `correct_strict`,
     `routed_hit`, `refused`, `produced` (= n − refused). Integers.
  2. **Rates** — per arm: `EX = correct/n`, `EX_strict`, `routing_recall =
     routed_hit/n`, `refusal = refused/n`, `cond_EX = correct/produced`.
  State `n` explicitly and, for deltas, give both the rate change and the
  question-count change (e.g. "+0.193 EX = +392 questions of 2030").

---

## 2026-07-22 01:55 UTC

Experiment log created. Context at this point:

- **Scenario in focus:** datalake (schema-routing) only. Multiple experiments planned
  on it — the three arms `baseline`, `curated`, `curated_sme`.
- **Data/DB:** BIRD-Obfuscation `rename_decoy` variant on the shared Aurora cluster
  `bos-genai-rds-global-cluster-dev-us-east1` (`bird` DB, 69 schemas, us-east-1).
  pg18→pg16 migration verified clean (see `PG_MIGRATION_REPORT.md`).
- **Connection:** burned into `src/governed_bi/eval/run_datalake.py` as the default —
  resolves DSN from Secrets Manager (`bos-mlpdevdb-genai`, reader endpoint,
  `dbname=bird`). No `--pg-dsn` flag needed. Verified via offline `--skip-agent` smoke
  (2 dbs built, gold self-check agree_rate 1.0).
- **Model:** MARS proxy, Claude-Opus-4.8 (verified live). Rate limits resolved.
- **Status:** ready to run the datalake experiment.

## 2026-07-22 19:32 UTC

Rebased our work onto the updated `origin/main` (9 new upstream commits,
`a5023ef` → `e14aceb`; +6,122/−846 across 96 files — ADR 0003 governed
Notes + tri-modal retrieval, and ADR 0004 local-first run logging incl. token
usage capture).

- Branch `mars` = `origin/main` + our single MARS/Aurora commit (13 files, +1180).
- Only one real conflict: `config.py` `DataSourceConfig` docstring (both sides
  expanded prose) — resolved to keep our 3-way DSN precedence text **and**
  upstream's ADR-0003 `db` lake-identity note. All other overlaps
  (`run_datalake.py`, `langchain_client.py`, `governed_bi.toml`, `pyproject.toml`,
  `uv.lock`) auto-merged as clean unions; verified both sides' intent survived
  (proxy wiring **and** upstream `last_usage_metadata`; Aurora burn-in **and**
  upstream `usage` logging; proxy extra **and** upstream checkpoint deps).
- `uv sync --extra proxy` OK. **Full test suite: 611 passed, 11 skipped, 1 xfailed,
  0 failures.** Datalake runner imports + CLI + Aurora burn-in confirmed intact
  against the new base.
- NOTE for the experiment: upstream now provides **local token-usage logging**
  (ADR 0004 — the feature previously flagged as missing is now built in), and
  **replaced the old `skills` concept with `NoteAsset`** + retrieval triggers
  (relevant to the earlier "curator authors routing skills" question — the
  mechanism is now Notes). Both warrant a re-look before running.
- Not pushed. `main` still tracks `origin/main`.

## 2026-07-22 19:58 UTC

Pre-flight before launching the full datalake run — verified the live path and
fixed one durability problem.

- **Live smoke OK** (1 db, 1 q, real Claude-Opus-4.8): full serve path works on
  the rebased base — model call → schema-qualified SQL (`"address"."zip_data"`)
  → executed → graded → **token usage captured** (36,558 tokens, ADR-0004). The
  single question scored EX=0 (a legitimately-wrong baseline answer on a hard
  SUM/filter question — expected headroom for the curated arm), and
  routing_recall=0 is a 1-db artifact (routing only engages when the corpus spans
  >1 schema; `spans_schemas` in agent.py). Not bugs.
- **PROBLEM FOUND + FIXED — serve phase was not resumable.** `resume=True` only
  covered the corpus *build*; `_run_pool_arm` accumulated rows in memory and wrote
  `generations.<arm>.jsonl` only after the *entire* arm finished (~20 h/arm at
  ~36 s/q × 2030). Any interruption (token-refresh edge, network blip, session
  end) would lose the whole arm's model spend. Also, every invocation minted a
  NEW timestamped run dir, so a restart never replayed prior progress.
  - Fix: `_run_pool_arm` now appends+flushes each row to `generations.<arm>.jsonl`
    as it is graded, and on restart replays that file to skip already-served
    questions (`_load_generations`; tolerates a torn final line). Summary is
    recomputed from the full persisted row set (`_summarize_arm`) so a resumed run
    scores identically. Added `--run-dir` to resume an EXACT run directory.
  - Verified: crash-simulated (truncated baseline, deleted curated_sme) → resume
    via `--run-dir` reused the same dir (no new timestamp), skipped done rows,
    rebuilt only the missing ones, all arms converged to full n. Eval/router
    tests: 25 passed. (Row schema gained a `decoy_touch` bool; `usage` now
    populated from solver meta.)
- Launching the full run next: all 3 arms, all 69 dbs / 2030 test questions,
  default routing (top_k=8, LLM pick on, embedder on).

## 2026-07-22 20:16 UTC

Full run LAUNCHED and building. `run_datalake --run-dir
runs/datalake/full_20260722T195838Z` (PID tracked in /tmp/mars_datalake_pid.txt),
all 69 dbs, all 3 arms, default routing. Target: BIRD Aurora reader endpoint.

- **Confirmed the routing test is honest** (in response to a design question): the
  solver is invoked with `graph.invoke({"question": ..., "session_id": ...})` —
  the question text ONLY. The `db_id` is NEVER given to the model; it exists only
  in the harness as the answer key (`routed_hit = db in routed`). One unpinned
  connector (`schema=None`) + one merged 69-schema corpus per arm. So the model
  must discover the right db itself (shortlist top-8 → LLM-pick 1). `routing_recall`
  is therefore a real measurement, not a freebie.
- Build pace: ~3.7 min/db for all-3-arms (baseline deterministic + curated &
  curated_sme deep-agents). ~69 dbs → est. ~4 h build, then serial serve.
- **Non-fatal event (handled, no action):** the `curated` deep-agent for db
  `authors` hit `GraphRecursionError` (recursion_limit=100) and "stopped early."
  This is caught at `curator/pipeline.py:288` — the full traceback is preserved to
  the run manifest and whatever assets the agent already wrote are KEPT. `authors`
  still produced 40 yaml files (in-family with neighbors: airline 38, app_store
  45), so the corpus is thinner-but-valid, not degenerate/corrupt, and the build
  continued to the next db. No db lost. Not a bias risk; logging for the record.

## 2026-07-22 21:34 UTC

**PROBLEM (fixed) — a db was dropped from ALL arms over a train/test dataset
quirk.** At build db ~26/69 the log showed:
`*** build FAILED for 'formula_1' — dropped from pool: AssertionError: SME brief
contains test question text: 'Show me the season page of year when the race
No. 901 took p'`.

- Root cause: `formula_1` reuses ONE identical question string across the BIRD
  train and test splits (test qid 863). `build_sme_brief` includes train questions
  as "domain context"; that shared string then tripped `assert_brief_no_leakage`,
  and the harness drops a failed db from EVERY arm (not just curated_sme). Scanned
  all 69 dbs: **5 are affected** — formula_1, movie_3, regional_sales, soccer_2016,
  video_games (1 collision each). Left unfixed, the experiment would silently
  shrink 69 → 64 dbs.
- The leakage guard is CORRECT and was NOT weakened. Fix removes the leak at its
  source: `build_sme_brief` gained `exclude_questions`; the runner passes the
  held-out test questions so any byte-identical train item (and its evidence) is
  filtered before the brief is built. The assert stays as a backstop. Verified
  against the real dataset: formula_1 now PASSES, other 4 stay clean; curator/
  datalake tests 25 passed. Committed `bf79bd6`.
- **Recovery (no token waste):** build was ~29/69 done and SERVE had not started
  (0 generations rows), so nothing scored was lost. Killed the old-code process,
  removed the single scratch-only partial dir (`corpus_curated/conversation_history`,
  0 yaml — safe: yaml only lands after the atomic `bag.write`), and am resuming
  with `--run-dir` on the fixed code. formula_1 keeps its already-built baseline
  (17 yaml) + curated (79 yaml) and only rebuilds curated_sme; the other 4
  colliding dbs build fresh and pass. All 69 dbs will be in the pool.

## 2026-07-22 21:48 UTC

Provenance note on the formula_1 collision (root cause, independently verified
against the ORIGINAL BIRD source): **this is a source-level near-duplicate, NOT a
bug we introduced.** The two rows are already distinct entries in the original
BIRD `dev.json`; step 1's shuffle happened to place one in train and one in test.

- test `question_id=863` — evidence: "race number refers to raceId"
- train `question_id=875` — evidence: "the season page refers to url; race number
  refers to raceId"
- Both share the SAME question text and SAME gold SQL:
  `Show me the season page of year when the race No. 901 took place.` →
  `SELECT T2.url FROM races AS T1 INNER JOIN seasons AS T2 ON T2.year = T1.year
  WHERE T1.raceId = 901` (only the evidence string differs).

So the pair is a pre-existing source near-duplicate leakage, not a split defect on
our side. Our fix (`exclude_questions` in `build_sme_brief`) is the right handling
regardless: it keeps the held-out test string out of the SME brief so the db is
scored rather than dropped, while the leakage assert still backstops. Also
confirms why the other 4 dbs (movie_3, regional_sales, soccer_2016, video_games)
collide — same source-duplicate mechanism.

**Live confirmation:** on the resumed fixed code, `formula_1/curated_sme` built
successfully (79 yaml). All 3 arms now present for formula_1; it is in the pool.

## 2026-07-23 01:15 UTC

Paused the run (build 69/69 done; baseline serve at 190/2030) to pull an upstream
**serve-loop concurrency** feature and merge it with our work.

- Upstream added 2 commits (`e14aceb..a5f2128`): `99f517d` **configurable
  serve-loop concurrency** (a `workers` knob — per-worker connector+gateway+graph,
  `ThreadPoolExecutor`, `solve()`→`solve_with_meta()` so no meta is stashed on the
  solver, `eval/parallel.py`, a design doc, and a 326-line invariance test) and
  `a5f2128` doc sync. Default `workers=1` is byte-identical to serial.
- Rebased our 6 commits onto it. The concurrency rewrite of `_run_pool_arm`
  collided head-on with our serve-resume (same function). Reconciled into ONE
  unified `_run_pool_arm` (commit `1f562b3`) that composes both: upstream's
  concurrency (serve_workers/worker_factory/`run_ordered_pool`) AND our durable
  resume (progress_path/resume — pre-filter served qids, persist each graded row
  single-writer on the aggregating thread, reassemble in pair order). Grading
  pulled into a module-level `_grade_pair`. Also re-verified our formula_1
  `exclude_questions` fix and MARS/Aurora burn-in survived the rebase.
- **Validation:** upstream's concurrency-invariance test passes (serial ==
  4-worker, byte-for-byte). A composed smoke (workers=4 → crash-sim → `--run-dir`
  resume with workers=4) skipped served rows and converged all arms to full n.
  **Full suite: 618 passed, 11 skipped, 1 xfailed, 0 failures** (+7 = the new
  concurrency tests).
- The paused run is **resume-compatible**: its `generations.*.jsonl` rows already
  carry `decoy_touch` + every field the merged summarizer reads, and the 69/69
  built corpora are intact. It can be continued in-place with `--run-dir <dir>
  --workers N`.
- NOTE: the `workers` knob targets an unlimited-throughput box; it opens N DB
  connections and N graphs. Aurora is shared — size N to its `max_connections`
  headroom. Also, LLM rate limits are a separate axis the knob does NOT manage.

## 2026-07-23 05:20 UTC

Fresh run launched (`full_20260723T013348Z`, 8 workers, all 69 dbs / 3 arms).
Build completed 69/69 in ~3.5 h; serve began — and I caught a **durability bug in
my own merge** at serve-start, fixed it, and relaunched with 0 serve rows lost.

- **Bug:** the unified `_run_pool_arm` buffered ALL rows and wrote
  `generations.<arm>.jsonl` only after the whole batch (`run_ordered_pool` /
  the serial list-comp both fully complete before the write loop). That silently
  defeated serve durability — a crash at question N/2030 would lose every served
  row, and progress was invisible until an arm finished. Caught it because the
  file stayed absent 90 s into serve.
- **Fix (`cf8385a`):** persist each row the instant it's graded, inside the task,
  under a lock so concurrent worker-thread writes stay single-writer. File order
  becomes completion order — fine, since `_load_generations` keys by
  `question_id` and rows are re-sorted to pair order (both order-independent).
- **Validated:** invariance test still green (serial == 4-worker); a
  workers=4 offline run showed rows landing incrementally, and truncate→resume
  restored all arms. Full suite 618 passed. **Live confirmation:** relaunched
  run showed `generations.baseline.jsonl` growing during serve (3 rows at
  ~2.5 min — impossible under the old buffer-then-write).
- Recovery was free: the stopped run had written 0 serve rows and its 69/69 built
  corpora were intact, so relaunch on `--run-dir` skipped all builds and started
  serve clean.
- Serve is now live: 8-worker fan-out engaged, per-row durable, token usage
  captured per row. First rows show the routing challenge directly (e.g. a
  `db=address` question routed to `world_development_indicators` → routed_hit
  False → EX 0), which is the "route to the right db among 69" signal we want.

## 2026-07-23 08:59 UTC

**DATALAKE EXPERIMENT COMPLETE.** Run `full_20260723T013348Z`, all 69 dbs / 2030
test questions / 3 arms, one shared 69-schema Aurora lake (`schema=None`), model
Claude-Opus-4.8, 8 serve workers. Serve wall-clock ≈ 3.5 h (vs ~60 h serial —
the concurrency merge paid off). `summary.json` written; 6090 generation rows.

FINAL RESULTS — n = 2030 questions per arm (exact counts from
`generations.<arm>.jsonl`).

**Counts** (of 2030):

| arm         |    n | correct | correct_strict | routed_hit | refused | produced |
|-------------|-----:|--------:|---------------:|-----------:|--------:|---------:|
| baseline    | 2030 |     456 |            446 |       1616 |     160 |     1870 |
| curated     | 2030 |     848 |            816 |       1731 |      26 |     2004 |
| curated_sme | 2030 |     849 |            820 |       1732 |      23 |     2007 |

**Rates**:

| arm         | EX     | EX_strict | routing_recall | refusal | cond_EX |
|-------------|--------|-----------|----------------|---------|---------|
| baseline    | 0.2246 | 0.2197    | 0.7961         | 0.0788  | 0.2439  |
| curated     | 0.4177 | 0.4020    | 0.8527         | 0.0128  | 0.4232  |
| curated_sme | 0.4182 | 0.4039    | 0.8532         | 0.0113  | 0.4230  |

Deltas (rate + question count):
- **curated − baseline: +0.1931 EX = +392 questions** (456 → 848 of 2030, ~1.86×);
  routing +115 (1616 → 1731); refusals −134 (160 → 26).
- curated_sme − curated: **+0.0005 EX = +1 question** (848 → 849) — flat/noise.
  [Later found to be the SME no-op bug, not a real SME effect — see 2026-07-23
  13:17 UTC entry.]

Reading:
- **The curator moat is real and large.** The semantic layer (LLM-authored
  descriptions, reliability caveats, terms, few-shots) nearly DOUBLES EX over
  the no-semantic-layer baseline on a 69-schema lake, lifts routing recall
  +5.7 pts (0.796 → 0.853), and cuts refusals 0.079 → 0.013.
- **The Simulated-SME round added ~nothing over curated this run** (+0.05% EX).
  Most likely because the SME brief is running thin: the BIRD
  `database_description` CSVs are absent from the checkout, so the SME falls back
  to train-evidence hints only (logged earlier). A fuller brief is the obvious
  lever if we want to re-test the SME arm's value.
- **Routing is the EX ceiling and it's ~0.85 for the curated arms**: ~15% of
  questions are mis-routed among 69 schemas → auto-EX-0. `routing_recall` ==
  `schema_pick_accuracy` (LLM-pick on), so shortlist-recall is not separately
  measured — the audit's suggested split-metric remains the cleanest next
  routing diagnostic.

Token cost (this run): serve ≈ 156.8M tokens (baseline 28.1M, curated 64.4M,
curated_sme 64.3M); build ≈ 51.2M tokens (curator+SME deep-agents, in
`data/logs/runs.sqlite`). `cost_est_usd` is 0/None on the serve rows (the proxy
provider doesn't return per-call pricing) — cost must be derived from token
counts, not that field.

## 2026-07-23 13:17 UTC

**CORRECTION — the curated_sme = curated result above was a BUG, not a finding.**
While placing the real `database_description` CSVs (user copied
`database_descriptions.zip` into `applications/`), I discovered the SME
clarification round **never ran in any prior run** — including the completed
`full_20260723T013348Z` above. So the ~0 curated_sme − curated delta was NOT the
thin brief; the SME arm was a silent no-op.

- **Zip verified + placed:** the zip is correct — BIRD `database_description`
  CSVs, exact `original_column_name,column_name,column_description,...` format,
  covering all 69 experiment dbs (train 69 + dev 11 = 80; our 69 ⊂ that). Extracted
  into `BIRD-Obfuscation/data/` (git-ignored). The 11 experiment dbs that are
  BIRD *dev* dbs had CSVs only under `dev_databases/`; copied their
  `database_description/` into the `train_databases/<db>/` path the SME code reads
  so all 69 resolve.
- **Bug 1 (encoding):** 23 CSVs across ~10 dbs are Windows-1252/latin-1, not UTF-8
  → `build_sme_brief` crashed (UnicodeDecodeError) for those dbs. Fixed:
  `errors="replace"`.
- **Bug 2 (leakage false-positive):** `assert_brief_no_leakage` used `\bSELECT\b`,
  which tripped on the English verb "select" in BIRD value_descriptions
  (european_football_2). Narrowed the BRIEF guard to `SELECT ... FROM`.
- **Bug 3 (the big one — SME no-op):** `build_curated_corpus_with_sme` reads the
  clarifications ledger at `clarifications_path(curated_root)` (root), but
  `_relocate_sidecars` moves it into `<db>/_build/` right after the curated build.
  So the SME always found `ledger_source=missing` → folded nothing → curated_sme
  == curated, byte-for-byte, in every run. Confirmed in BOTH the original run and
  my first rerun (all manifests `missing/0/none`; 0 SME clarifications answered
  anywhere; corpora identical). Fix: `_restore_clarifications_ledger` copies the
  relocated ledger back to the root path just before the SME build.
- **Fix validated live on `address`:** SME manifest went
  `missing/clar_count=0/fold=none` → `agent/clar_count=5/fold=agent`;
  `sme_clarifications.jsonl` now holds 5 answered clarifications; curated_sme
  diverges from curated (e.g. SME confirmed via DB probe that
  `congress.first_name` actually holds the surname — a real reliability insight).
  Commits `8febed4` (CSV encoding + leakage guard) and `5c974ab` (ledger restore).
  Tests green.
- **Consequence:** baseline (0.225) and curated (0.418) results STAND — they don't
  use the SME. Only curated_sme must be re-run, now that it actually does work.
  Launching the full curated_sme rerun next (rich brief + real SME fold).

## 2026-07-23 15:54 UTC

curated_sme rerun (`sme_rerun_20260722...` dir `sme_rerun_20260723T123646Z`, all
69 dbs, 8 workers, real SME) — **build phase hit a hung model request at db 35
(`mondial_geo`); killed and resumed, no progress lost.**

- Symptom: build stuck at 34/69 for ~11 min. Diagnosed the worker (not the `uv`
  wrapper) in `do_poll` with ~0 CPU jiffies/60s and 3 open sockets → blocked on a
  network read that never returned (a MARS proxy request that hung past its
  client timeout — same class as the token/long-request quirks noted in memory).
- Recovery via the resume machinery: `pkill` the run, removed the in-progress
  0-yaml curated_sme dir + the stale root `clarifications.jsonl`, relaunched with
  the same `--run-dir --arms curated_sme --workers 8`. The 34 completed SME
  corpora were kept (`_has_yaml` skips them), so it continued at db 35 — zero
  rebuild, zero re-billing.
- No code change: this looked like a one-off stalled connection, not a systematic
  bug. If it recurs, same kill+resume (each resume preserves all prior progress).
  A follow-up hardening would be a wall-clock watchdog on the SME build step, but
  not worth changing mid-experiment.
- Still building; serve (8-worker) starts once all 69 SME corpora are built.
- [Update] It hung a SECOND time at db 67 (~7 min idle, same signature); same
  kill+resume recovered it. Build then completed 69/69. Two hangs, both benign
  and fully recovered via `--run-dir` resume — the durability work earned its keep.

## 2026-07-23 20:00 UTC

**CORRECTED curated_sme RESULT (SME clarification round actually ran).** Run
`sme_rerun_20260723T123646Z`: baseline + curated corpora reused from
`full_20260723T013348Z`; curated_sme rebuilt with the rich `database_description`
brief AND the ledger-restore fix, so the SME answered real clarifications
(`ledger_source=agent`) for all 69 dbs. Served the full 2030 at 8 workers.

**Counts** (n = 2030), with the other two arms (unchanged) for comparison:

| arm             |    n | correct | correct_strict | routed_hit | refused | produced |
|-----------------|-----:|--------:|---------------:|-----------:|--------:|---------:|
| baseline        | 2030 |     456 |            446 |       1616 |     160 |     1870 |
| curated         | 2030 |     848 |            816 |       1731 |      26 |     2004 |
| curated_sme OLD | 2030 |     849 |            820 |       1732 |      23 |     2007 |
| **curated_sme (fixed)** | 2030 | **835** | **813** | **1726** | **25** | **2005** |

**Rates**:

| arm             | EX     | EX_strict | routing_recall | refusal | cond_EX |
|-----------------|--------|-----------|----------------|---------|---------|
| baseline        | 0.2246 | 0.2197    | 0.7961         | 0.0788  | 0.2439  |
| curated         | 0.4177 | 0.4020    | 0.8527         | 0.0128  | 0.4232  |
| curated_sme OLD | 0.4182 | 0.4039    | 0.8532         | 0.0113  | 0.4230  |
| **curated_sme (fixed)** | **0.4113** | **0.4005** | **0.8502** | **0.0123** | **0.4165** |

**Finding: a working SME does NOT help — it slightly hurts.**
- curated_sme (fixed) − curated: **−0.0064 EX = −13 questions** (848 → 835 of 2030);
  routing −5 (1731 → 1726). The OLD no-op SME was +1 (== curated, as expected).
- So with the ledger bug fixed, the real SME clarification round costs ~13 EX vs
  curated alone. Interpretation: the SME's folded answers occasionally
  over-constrain or mislabel (e.g. flipping a reliability call the curator had
  right), and the net effect at 2030-scale is marginally negative. The curator
  layer already captures most of the available signal; the extra SME round adds
  cost (build hangs + 72.9M serve tokens) without EX gain.
- **Bottom line of the whole experiment:** curated is the winner (+0.193 EX =
  +392 questions over baseline). The SME arm, now that it genuinely runs, is not
  worth its cost on this dataset. The earlier "SME ≈ curated" was a bug artifact;
  the corrected result is "SME slightly < curated," which is a real (if modest)
  finding, not a no-op.
- Cost: corrected curated_sme serve ≈ 72.9M tokens; build re-ran only the SME
  corpora (curator/SME deep-agents, incl. 2 recovered hangs).

## 2026-07-23 20:06 UTC

Token cost broken down as **input / output** (matters for $: input tokens are
much cheaper, and this workload is ~98% input — heavy schema/context reads, tiny
SQL/prose writes). SERVE numbers are summed from each arm's
`generations.<arm>.jsonl` `usage`; BUILD from `data/logs/runs.sqlite` `token_sum`.

**SERVE** (per arm, one full 2030-question pass each):

| arm                     |         input |    output |         total | output % |
|-------------------------|--------------:|----------:|--------------:|---------:|
| baseline                |    26,658,353 | 1,440,419 |    28,098,772 |    5.1%  |
| curated                 |    63,487,695 |   904,225 |    64,391,920 |    1.4%  |
| curated_sme OLD (no-op) |    63,410,064 |   901,785 |    64,311,849 |    1.4%  |
| curated_sme (fixed)     |    72,017,085 |   929,375 |    72,946,460 |    1.3%  |

Serve reading: baseline emits the most output % (5.1%) because it retries/guesses
more SQL with no semantic layer; curated/SME are dominated by the large governed
context injected per question (input), with little generated text. The three
"real" arms to compare are baseline + curated + curated_sme(fixed); the OLD no-op
row is kept only for the record.

**BUILD** (curator + SME deep-agents; `run_log.sqlite` — accumulates ALL runs:
the original 69-db build + both SME rebuilds, so it double-counts the SME arm):

| producer | turns |        input |    output |        total | output % |
|----------|------:|-------------:|----------:|-------------:|---------:|
| curator  |   213 |   70,109,095 | 1,762,739 |   71,871,834 |    2.5%  |
| sme      |   400 |   27,040,841 |   550,480 |   27,591,321 |    2.0%  |
| build ∑  |   631 |   97,149,936 | 2,313,219 |   99,463,155 |    2.3%  |

(Serve turns log 0 tokens in `run_log.sqlite` — serve usage lives only in the
generations files. `cost_est_usd` is null/0 on all rows: the MARS proxy returns
no per-call pricing, so cost must be computed from these token counts × the
proxy's input/output rates.)

**Headline totals** (the clean 3-arm experiment = baseline + curated +
curated_sme(fixed) serve, + one 69-db build of each arm):
- Serve, 3 real arms: input ≈ 162.2M, output ≈ 3.27M, total ≈ 165.4M (out 2.0%).
- Build, per-arm-once (curator 71.9M + one SME pass ≈ 13.8M of the 27.6M
  two-run total): input-dominated, ~2–2.5% output.
- Takeaway: **~98% of all spend is input tokens.** Optimizing cost means shrinking
  the per-question governed context (curated/SME inject ~31k input tokens/question
  on average: 63–72M ÷ 2030), not output.


---

## 2026-08-01 22:28 UTC

**E2 + E3 — offline routing probes.** No chat model, no SQL, no grading. Both run
against the E1 corpus (`runs/datalake/20260731T233457Z-opus48-high-ladder/20260731T233545Z/corpus_curated`,
57 schemas) and the BIRD `test` split (1351 questions), joined to that run's
`generations.curated.jsonl`. Design write-up: `docs/plans/routing-redesign.md`.

**E2 — `scripts/pick_evidence_probe.py`** → `runs/ablation/e2-pick-evidence.json`.
Asks whether the tables the gold SQL reads are among the 15 the LLM picker is shown.
1222 usable questions (129 dropped: gold SQL is a frozen `VALUES` constant reading no
table). 9 of 57 schemas exceed `SCHEMA_PICK_MAX_TABLES = 15`; 325 questions have such
a gold schema.

| ordering of the 15 | all gold tables visible | same, gold schema > 15 tables |
|---|---:|---:|
| `alpha` (today, `physical_name` sort) | 0.840 | 0.400 |
| `rel` (BM25 over `asset_document`) | 0.948 | 0.806 |
| `rel_guard` (BM25, gated on table-description coverage) | 0.875 | 0.529 |
| `rel_desconly` (BM25 over curated prose only) | 0.951 | 0.815 |
| `rel_emb` (per-table `text-embedding-3-large` cosine) | **0.970** | **0.886** |

Per-schema extremes: `works_cycles` (73 tables) 0.077 → 0.969, `hockey` (29) 0.034 →
0.897. `rel` is the only variant that *loses* anywhere — `mondial_geo` (42 tables,
0/42 table and 0/275 column descriptions) 0.179 → 0.154, on a spurious `name`
identifier match. `rel_desconly` self-guards there (empty prose index → alphabetical);
`rel_guard` does not (it disables the two biggest wins).

Causal link NOT established: within-schema control over the 9 wide schemas gives 4
positive / 3 negative, one-sided sign test **p = 0.50**. The "small schemas attract
misroutes" asymmetry also fails: gold wider than picked in 72/106 misroutes vs 0.615
expected under uniform choice among the non-gold candidates, p = 0.104.

**E3 — `scripts/routing_fusion.py`** → `runs/ablation/e3-fusion.json`,
`runs/ablation/e3-rankings.json` (per-question per-channel ranking cache, 17 MB;
re-sweeping fusion weights or `top_k` off it is free). ~330k embedding tokens, < $0.05,
7 minutes. Fidelity vs the recorded run (`emb_large`): gold rank 0.970, rank-1 identity
0.990.

| channel | @1 | @3 | @5 | @10 | @20 |
|---|---:|---:|---:|---:|---:|
| `bm25` | 0.736 | 0.844 | 0.879 | 0.906 | 0.920 |
| `emb_large` (today) | 0.694 | 0.850 | 0.906 | 0.952 | 0.979 |
| `tblmax_large` (per-table vectors, max-pooled) | **0.730** | **0.893** | **0.939** | **0.973** | **0.991** |
| `rrf(bm25, emb_large)` | 0.733 | 0.871 | 0.898 | 0.922 | 0.943 |
| `rrf(emb_large, tblmax_large)` | 0.710 | 0.887 | 0.931 | 0.976 | 0.991 |

Negative results worth keeping: `bm25_tbl_max` (max-pooling the lexical channel)
0.870@10 < `bm25` 0.906; `assetmax` (pooling metrics/few-shots/terms in as well, 2810
documents) 0.942@10 < `tblmax_large` 0.973. Table granularity specifically.

The table index is *cheaper* than today's: 656 docs / 95,750 tokens vs 57 docs /
130,243 tokens. Per-question cosine goes 26 ms → 348 ms in pure Python (656 × 3072),
1.17 ms with numpy — numpy is currently undeclared in `pyproject.toml`.

**Confidence gate: falsified offline.** Simulating "keep rank 1 without asking the LLM
when its relative margin `(s1-s2)/s1` ≥ t" against the recorded picks, net is ≤ 0 at
every threshold on all four channels. On `emb_large` at t=0: saved 21, broken 264. The
LLM picker is +17.9pp over rank 1 (0.873 vs 0.694) — it corrects 12.6 rank-1 errors for
every one it introduces. Hedging cannot replace it either: gold in rank ≤ 2 is 0.790 and
rank ≤ 3 is 0.850, both below the picker's 0.873. Only surviving use is cost:
`tblmax_large` at t=0.20 covers 43.2% with saved 12 / broken 12 (net 0), and the router
is 25.0% of an arm's tokens (12.4M in, 9185 in / 117 out per question) → ≈ −11% arm
tokens for no measured accuracy change.

Follow-up (not run): **E4, a pick-only harness** — `shortlist_schemas` + `pick_schema`
only, graded against `db_id`. ~12.6M tokens ≈ 25% of one arm, 6% of a ladder, no serve
loop. Pre-registered strata, with today's pick accuracy as the baseline: **A** no wide
candidate, n=144, 0.938 (prompt is byte-identical under R1 — A/A control); **B** wide
distractor only, n=838, 0.885 (risk stratum: R1 also makes distractors more persuasive);
**C** wide gold, n=369, 0.821 (benefit stratum). R1 is refused if C does not rise or B's
drop eats C's gain.
