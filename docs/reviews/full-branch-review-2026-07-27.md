# Full branch review: `fix/silent-failure-audit-followups`

Fixed range: `a5f2128...d9934f5` (75 commits, about 114 files, +31,180 / -1,306).
Review date: 2026-07-27.
Mode: read-only. No product code, tests, docs (except this file), config, or git state were changed by the report author.

This review asks whether the branch makes paid BIRD-scale eval results trustworthy enough to quote. It does not ask whether a generic production service would stay available. Maintainability smells are recorded, but they are not treated as runtime blockers unless they can corrupt spend, corpora, or published numbers.

**Scope limits.** No network calls, no live LLM calls, no Postgres, no BIRD data-lake load, and no paid experiments were run for this review. No effect-size claims from historical ladder runs are validated here. Offline checks only: corpus CLI and the local pytest suite (results below).

---

## Method

1. Twelve Grok 4.5 domain reviewers produced a first-wave candidate set across build/resume, stats/claims, metrics/runtime, design/tests, and docs.
2. A fresh verification wave re-inspected candidates against current source, docs, and tests; confirmed, reclassified, or demoted items.
3. A synthesis pass deduplicated the set into sixteen canonical findings, calibrated severity to quotable paid eval (not generic availability), and produced an evidence packet.
4. This document is the packet written out as a self-contained report.

**Checks already run by an independent Grok worker (not re-run for the write-up):**

- `uv run python -m governed_bi.corpus.cli`: PASS, 17 assets, 0 findings.
- `uv run pytest -q -rs`: PASS, 1347 passed, 10 skipped, 1 xfailed, 0 failed, about 76.41s; one `StarletteDeprecationWarning`.
- No ruff, mypy, or pre-commit configured in this repo for the check pass.
- Working tree remained clean for that worker.

---

## Readiness (three separate judgments)

| Lens | Verdict |
|---|---|
| Merge readiness | Conditionally ready. The branch hardens paid-eval integrity (quotability ledger, treatment delivery, twin strata, resume scope for arms/dbs/oracle/replicate, gold and build gates, ladder pricing). The offline suite is green. Remaining merge-relevant defects are operator hazards in parallel/resume build paths, not broken unit arithmetic on happy paths. |
| Run readiness | Not ready for unattended scale resume. A fresh full build with `build_workers=1` and explicit scope flags is usable. Parallel resume and SME resume after curated sidecar relocation still risk spend waste or corpus corruption. |
| Quote readiness | Not ready without a human checklist beyond `index.quotable`. That flag is ledger and artifact hygiene. Claim readiness still needs replicate, MDE, Holm, cluster, single-variable, and twin conditions from the runbook. Docs still mis-attribute few-shots to `seeded` in several places. Offline `gradeable_report` and summary `ex_gradeable` use different denominators. |

Do not treat tip `d9934f5` as safe to launch Step 2/3 and publish deltas until F1 through F4 and the few-shot / doc claim cluster are closed or operationally fenced.

---

## What problem the branch addressed

Earlier ladder and data-lake numbers were not safe to treat as paid, quotable claims. Crashes folded into refusals and EX. Arms changed more than one mechanism at a time. Resume could change what a scored row meant without a durable record. SME fold could become a no-op after clarification sidecars moved. "Quotable" was underspecified relative to what an operator needs before stating a result.

The branch exists to make failures attributable, treatments checkable, ladder steps closer to single-variable, and paid runs fail closed before or after spend when the artifact cannot support a claim.

---

## Thematic change inventory

Rough narrative of the 75 commits (subjects inspected; themes grouped):

1. **Attributable failure.** Shared Outcome/Stage vocabulary; crash separated from refusal; wrong-answer stage taxonomy; treatment fingerprints so an arm that did not deliver its intervention cannot look like a fair comparison.
2. **Ladder as single-variable steps.** `seeded` rung; optional `curated_sme_blind`; bundling labels on compound pairs; serve replicate noise floor; Holm / cluster / MDE; cost per additional correct answer; arm-order confound detection.
3. **Fail closed before or after spend.** Gold preflight; build coverage gate; unquotable on build errors, treatment failure, and resume drift; resume scope recorded for arms, dbs, oracles, and replicate.
4. **Scale mechanics.** Parallel build staging and promote; resume of generations; twin stamps with `is not None` discipline; governance rate wiring; `oracle_sql` usable under `--skip-agent` for a free Step 0.
5. **Docs and ledger alignment.** English runbook checklist and several claim fixes late in the range; Chinese alignment lagged by project policy during design and was partially caught up near the tip.

Representative subject lines from the range include fixes for twin denominators, resume scope, ledger hygiene on Windows, fabricated diagnostics, SME / curator checkpointer leaks, routing escape measured from delivered SQL, and refusal to serve a pool that mostly failed to build.

---

## Strengths and fixes the branch achieved

These are real improvements visible in source and tests. They are not a license to quote without the checklist.

- Gold that will not execute can no longer report a perfect agree rate and proceed (`_assert_gold_is_trustworthy` commentary and gates in `src/governed_bi/eval/run_datalake.py`).
- Build attrition has its own gate so a thin surviving pool is not silently scored (`_assert_build_coverage`, about lines 447-469).
- Treatment non-delivery feeds `index.quotable` / `_undelivered` (`src/governed_bi/eval/index.py`, about lines 150-190).
- Resume now records and can refuse drift on arms, oracles, replicate, and db_ids (`_build_manifest`, lines 750-763). Limits remain outside that set (F3).
- Twin strata no longer treat missing stamps as twin-free (`_summarise_rows`, lines 1867-1876). The worst unstamped-as-false bug is fixed; partial coverage remains (F7).
- Crash rows are re-served on resume by default so one transient crash does not leave the whole run unquotable (`_run_pool_arm`, lines 2961-2993), with a non-atomic rewrite hazard (F5).
- Pricing refuses unequal N for cost-per-added-correct (`price_verdict` / unpaired path, about lines 969-1004 and 1145-1158).
- Parallel build isolation, staging clear-on-start, and sidecar promote markers are documented in code after concrete incidents (SME ledger relocate, staging debris).
- Helper-level tests are dense: ladder design enumeration, leakage twins, treatment, index quotable floor, staging/promote behavior.
- Offline verification for this review: 1347 pytest passes; corpus CLI clean on 17 assets.

---

## Experiment validity analysis

What the harness can support when operators follow the English experiment runbook checklist:

- Adjacent ladder steps are labeled; non-adjacent pairs carry `bundles`.
- `single_variable: true` means ladder adjacency, not "one physical mechanism." The checklist states that `baseline -> seeded` still mixes joins, metrics, decoy/negative-space masking, and dropping baseline FK guesses (`docs/plans/experiment-runbook.md`, about lines 483-492). The English table correctly says that `seeded` has no few-shots, but its "what parsing the training SQL is worth" meaning conflicts with the checklist. Several other docs also assign few-shots to this rung (F10).
- `index.quotable` does not encode replicate, MDE, Holm, cluster, or twin conditions. Those live in the runbook checklist (F8).
- Serve-replicate noise floor bounds serve-side sampling, not curated build variance. The runbook discloses this (about lines 469-472). Quoting a curated delta as "resolvable" against that floor overstates certainty (F11).
- Summary `ex_gradeable` excludes frozen and order-sensitive gold. Offline `gradeable_report` excludes frozen only (F9). Publishing the analysis field as if it were the summary headline mixes denominators.
- Cost-per-added-correct remains unpaired on row counts; equal N with different question IDs can still price (F12). Prefer paired comparisons in `comparisons[]`.
- Holm adjustment includes bundled / non-adjacent pairs in the family. That is conservative and coherent; the checklist says not to quote bundles.
- SME-blind omission and serve-only floor are disclosed limitations, not coding defects.
- Twin rate for claims should use gradeable 182/1627 style denominators; historical unfiltered 246/2030 and a "247" typo still appear in prose (F15).

No paid run was executed for this review, so none of the above is an empirical claim about current EX deltas.

---

## Finding classification map

| Class | Findings |
|---|---|
| Confirmed defects (can corrupt spend, corpora, or silent scope) | F1, F2, F3, F4, F5, F7 (residual), F9, F14 |
| Process / docs defects (invite wrong claims or wrong operator commands) | F8, F10, F13, F15 |
| Known / disclosed limitations (accepted unless over-claimed) | F11; SME-blind opt-in; Holm over bundled pairs; stuck-sidecar discard after marker with fail-closed gate; concurrency knobs not resume-fatal by design; oracle governance stamps as counterfactual |
| Speculative risks omitted from the canonical sixteen | Shared model/embedder thread safety under serve workers; ordered-map durability lag as intentional tradeoff |
| Maintainability / test debt (not runtime blockers) | F6 (warn-then-spend UX), F12 (pricing shape), F16 |

---

## Canonical findings (16)

Severity is calibrated to trustworthy paid eval and quotable results. Confidence is the synthesis judgment after verification against current tree.

### F1. Partial YAML treated as complete on resume, then promoted

- Severity: Blocker (run / quote)
- Confidence: High
- Evidence: `src/governed_bi/eval/run_datalake.py:175-177` (`_has_yaml`); `:284-305` (`_stage_roots`); `:2283-2284` (skip entire db when all wanted arms "have yaml"); `:413-444` (`_promote_build`)
- Mechanism / impact: `_has_yaml` is true if any `*.yaml` exists under the db directory. On resume with `build_workers > 1`, staging is seeded from the shared root, the build can skip because yaml exists, and promote moves that tree into shared. A kill mid-build leaves a partial corpus that a later resume can adopt and promote. Paid serve then scores incomplete corpora as finished.
- Counter-evidence: Staging roots are cleared at the start of each build, which avoids mistaking failed-staging debris for a finished corpus. That does not protect a partial tree already in the shared arm root.
- Recommendation: Require an explicit completeness marker (per-db build manifest plus expected asset set or checksum). Never treat "any yaml" as done.

### F2. Resumed SME build reads root clarifications after relocation to `<db>/_build`

- Severity: Blocker (SME quote / spend)
- Confidence: High
- Evidence: `src/governed_bi/eval/run_datalake.py:2295-2304`, `:2450-2452` (deferred relocate within one db build); `src/governed_bi/curator/pipeline.py:829-830`, `:904-905` (ledger load and empty-ledger fold)
- Mechanism / impact: Sidecars move to `<db>/_build/`. A later resume that builds SME after curated already relocated reads `clarifications_path(curated_root)` at the arm root. An empty ledger yields a paid no-op or, under skip-agent scaffolding, a synthetic seed fold. `curated_sme` can equal `curated` by construction after spend.
- Counter-evidence: Downstream `sme_noop` / treatment checks can mark the run unquotable after the money is spent. Deferred relocate fixes the within-process ordering bug that caused the original multi-week incident; it does not fix cross-resume path resolution.
- Recommendation: Resolve the Phase A ledger from the relocated `_build` path (or delay relocate until all SME arms for that db finish across resumes).

### F3. `limit` and `limit_dbs` absent from manifest and resume checks

- Severity: High
- Confidence: High
- Evidence: `_build_manifest` in `src/governed_bi/eval/run_datalake.py:692-764` (records arms, oracles, replicate, db_ids; not limits); CLI `:4078-4079`; application `:3368-3369`, `:3491`
- Mechanism / impact: A capped smoke directory resumed without the same caps can widen to the full split (or the reverse). Spend and denominators change relative to operator intent. Arms/dbs/oracle/replicate are now guarded; per-db question caps and db count caps are not.
- Counter-evidence: Narrower `--limit` / `--dbs` on serve replay excludes out-of-pool rows from the scored summary while leaving them in the JSONL (reported on stdout). That is not a substitute for recording the original caps.
- Recommendation: Persist `limit` and `limit_dbs` (or a hash of the effective question-id set) and refuse resume drift the same way arms/dbs are refused.

### F4. Promote deletes destination then moves; kill can destroy a good corpus

- Severity: High
- Confidence: High
- Evidence: `src/governed_bi/eval/run_datalake.py:429-435`
- Mechanism / impact: `_promote_build` calls `shutil.rmtree(dest_schema)` when the destination exists, then `shutil.move` from staging. A failure or kill between delete and successful move removes the last good promoted corpus for that arm/db.
- Counter-evidence: Promotion is lock-serialized across workers, which reduces races between two promotes, not the delete-before-replace durability hole.
- Recommendation: Promote to a temporary name under the shared root, then replace atomically. Do not delete the live destination until the new tree is durable.

### F5. Crash-row JSONL rewrite is in place and non-atomic

- Severity: Medium
- Confidence: High
- Evidence: `src/governed_bi/eval/run_datalake.py:2974-2988`; `src/governed_bi/eval/run_experiment.py:215-219` (`_write_jsonl` opens with `"w"`)
- Mechanism / impact: On resume, crashed rows are stripped by rewriting the generations file. A kill mid-write truncates `generations.<arm>.jsonl` and can lose scored rows.
- Counter-evidence: Re-serving crashes by default is the right quotability policy (one crash should not freeze a bad rate into the ledger). The defect is the rewrite durability, not the policy.
- Recommendation: Write to a temp file and `Path.replace` onto the destination.

### F6. `git_sha` drift warns before spend and makes the ledger unquotable later

- Severity: Medium
- Confidence: High
- Evidence: resume knob derivation `src/governed_bi/eval/run_datalake.py:675-689`; `quotable` in `src/governed_bi/eval/index.py:421-427`
- Mechanism / impact: Resuming after a code edit warns, then continues. Rows span more than one configuration while the manifest reflects the first. The ledger later marks the run unquotable. Spend is wasted; a clean quote is not silently minted.
- Counter-evidence: Fail-closed at index time is correct for claims. The gap is operator UX for paid profiles.
- Recommendation: Offer a paid profile where resume drift is fatal unless `--force-drift` (or equivalent) is set. Keep warn-and-continue for smoke.

### F7. Partial twin stamps: residual after the unstamped-as-false fix

- Severity: Medium (residual)
- Confidence: High
- Evidence: `src/governed_bi/eval/run_datalake.py:1867-1893`; comparison gating around `:1263-1342`
- Mechanism / impact: Missing `gold_twin_in_train` is no longer treated as false, so resumed rows cannot silently inflate `ex_no_twin`. The comparison gate checks whether each arm has any stamped row, not whether every gradeable row is stamped. A partially stamped file can therefore emit `comparisons[].no_twin` from only the stamped subset while pooled metrics still include unstamped rows.
- Counter-evidence: The worst bug (unstamped counted as twin-free) is fixed. Stamp coverage is visible if someone reads the unstamped count.
- Recommendation: Refuse twin strata and `comparisons[].no_twin` unless stamp coverage is 100% of scored rows.

### F8. `index.quotable` is ledger hygiene, not claim readiness

- Severity: High (process)
- Confidence: High
- Evidence: `MIN_QUOTABLE_QUESTIONS` and `quotable` in `src/governed_bi/eval/index.py:350-427`; checklist in `docs/plans/experiment-runbook.md:460-492`
- Mechanism / impact: Quotable fails closed on missing crash data, skip-agent, train split, tiny N, crashes, resume drift, build errors, and undelivered treatment. It does not require replicate, MDE clearance, Holm, cluster agreement, single-variable, or twin strata. `MIN_QUOTABLE_QUESTIONS = 8` is the default four-arm family arithmetic floor, not a sufficiency test for five arms / ten tests (comment at `:350-354`). Operators can read `quotable: true` as "publishable."
- Counter-evidence: The English runbook checklist states the stronger conditions clearly.
- Recommendation: Rename operator-facing copy toward `ledger_ok` / `hygiene_ok`. Keep the checklist as the gate for quotes. Surface family size vs floor in the index row.

### F9. Summary vs analysis `ex_gradeable` denominators disagree

- Severity: High (offline claim)
- Confidence: High
- Evidence: summary path `src/governed_bi/eval/run_datalake.py:1813-1860` (excludes frozen and order-sensitive); `gradeable_report` in `src/governed_bi/eval/analysis.py:508-540` (frozen only)
- Mechanism / impact: The same field name appears in `summary.json` and `analysis.json` with different denominators. Offline analysis can disagree with the run headline on `ex_gradeable`.
- Counter-evidence: Comments in both places acknowledge the need for shared names to mean the same thing at edges; the order-sensitive half of the rule did not land in `gradeable_report`.
- Recommendation: Align `gradeable_report` with the summary rule, or rename the analysis field.

### F10. Docs misstate what `baseline -> seeded` changes

- Severity: High (claim narrative)
- Confidence: High
- Evidence: Code `src/governed_bi/curator/seed.py:236-260` (joins and metrics only); English runbook table `docs/plans/experiment-runbook.md:434` ("No LLM, no few-shots"); stale prose in `docs/architecture.md:204`, `docs/glossary.md:71`, and related measurement / ZH ladder wording
- Mechanism / impact: Readers can infer that `baseline -> seeded` measures few-shot value or the value of parsing training SQL alone. Few-shots are authored on the curated agent path, and the rung also changes joins, metrics, decoy marking, and baseline FK behavior. The runbook checklist describes the multi-mechanism / negative-space interpretation, while its table meaning and several sibling docs teach narrower claims.
- Counter-evidence: The English experiment-runbook table correctly says "No LLM, no few-shots," and its checklist contains the stronger causal caveat. The table's meaning column and the other cited docs remain inconsistent with that caveat.
- Recommendation: One English pass over the runbook table, glossary, architecture, and measurement; then align Chinese. Do not quote `baseline -> seeded` as few-shot lift or parsing-only lift until that pass lands.

### F11. One serve-replicate floor drives all pair MDE / resolvable readings

- Severity: Medium (disclosed limitation)
- Confidence: High
- Evidence: `src/governed_bi/eval/power.py` (`serve_replicate` source); runbook `docs/plans/experiment-runbook.md:469-472`
- Mechanism / impact: The measured floor bounds serve-side sampling noise for a fixed corpus. On `curated` and later steps it is a floor on the wrong quantity for build variance. Resolvable deltas can look safer than they are.
- Counter-evidence: Disclosed in the checklist. Not a silent code defect.
- Recommendation: Keep the disclosure next to every quoted MDE. Do not "fix" without a build-side replicate design.

### F12. Cost-per-added-correct is an unpaired all-row delta

- Severity: Medium
- Confidence: High
- Evidence: `price_verdict` and delta block in `src/governed_bi/eval/run_datalake.py:969+`, `:1145-1220`
- Mechanism / impact: Unlike McNemar comparisons (intersect on `question_id`), pricing uses arm-level counts. Unequal N is refused (`unpaired_n`). Equal N with different IDs can still produce a dollar-per-added-correct figure.
- Counter-evidence: Stdout labels unpaired marginal deltas as secondary to paired comparisons (`:4302` area). Coverage flags distinguish incomplete pricing from measured zero.
- Recommendation: Price only on paired discordant gains, or require identical question-id sets before emitting the field.

### F13. Dual `by_failed_stage` meanings; `execution_error` missing from measurement cascade prose

- Severity: Medium
- Confidence: High
- Evidence: Live serve summary `src/governed_bi/eval/run_datalake.py:1693-1698` (Outcome/Stage from `classify_row`); offline taxonomy `src/governed_bi/eval/error_taxonomy.py:76`, `:137`, `:525`; cascade prose `docs/measurement.md:366-383` and class table about `:450-471` (omits `execution_error`)
- Mechanism / impact: The same key name means live refusal/crash stage attribution in one artifact and offline wrong-answer cascade buckets in another. The documented cascade skips `execution_error` even though the enum and stage mapping exist. Debugging and secondary reports can mix the two.
- Counter-evidence: Taxonomy code and live classify paths are individually coherent.
- Recommendation: Rename the offline key (for example `by_error_stage`). Add `execution_error` to the measurement cascade and table.

### F14. Unresolved `tables_used` IDs silently disappear; routing escape becomes `None`

- Severity: Medium
- Confidence: Medium-High
- Evidence: `_schema_of_assets` `src/governed_bi/eval/run_datalake.py:1622-1635`; `_routing_escaped` `:1594-1619`
- Mechanism / impact: Asset ids that do not resolve to a table in the served corpus are dropped. Empty `used_schemas` makes routing escape unscored (`None`) rather than positive. Escape rate can undercount.
- Counter-evidence: Dropping unresolved ids is deliberate versus heuristic string splits on underscored schema names (documented in the helper).
- Recommendation: Count unresolved ids. Put non-empty unresolved sets in an escape or unknown bucket instead of pretending there was nothing to judge.

### F15. Stale operator docs: resume, DSN, twin figures, backlog C7, routing toml example

- Severity: Medium (cluster)
- Confidence: High
- Evidence:
  - `docs/plans/datalake-run.md:20-23`, `:184`, `:191`, and `:310` use the old three-arm ladder or a bare resume command. The English experiment-runbook `:497` already requires original arms/dbs/oracle/replicate, and the default ladder now includes `seeded`.
  - Runbook still cites `PG_RENAME_DECOY_DSN` as required in places; CLI uses `--pg-dsn` with a default (`src/governed_bi/eval/run_datalake.py:4043`) and does not read that env var in the driver.
  - Twin prose mixes gradeable 182/1627 with historical unfiltered 246/2030 and a "247" typo (`docs/plans/experiment-runbook.md:532-543`).
  - `docs/plans/eval-audit-backlog-2026-07-22.md` C7 still marked Open (about line 38) while order-sensitive exclusions are wired (`run_datalake.py:1816`, `:3555`).
  - `governed_bi.toml` has no `[routing]` example; loader supports it (`src/governed_bi/config.py:551-568`).
- Mechanism / impact: Operators can plan the wrong arm count and cost, resume with the wrong scope, point at the wrong DSN habit, quote the wrong twin rate, think order-sensitive exclusions are still missing, or fail to pin eval routing in product config.
- Counter-evidence: English experiment-runbook resume and twin gradeable denominator text are largely corrected; the cluster is residual drift across sibling docs and backlog status.
- Recommendation: Doc sweep before any external quote. Mark C7 fixed. Add a commented `[routing]` block to `governed_bi.toml`. Align the arm list, sizing, and resume command in `datalake-run.md` with the experiment-runbook.

### F16. Maintainability and driver-level test debt (not runtime blockers)

- Severity: Low to Medium
- Confidence: High
- Evidence: `src/governed_bi/eval/run_datalake.py` is about 4k lines and imports private helpers from `run_experiment` (including `_write_jsonl`); dual grade / summary paths; `treatment_reasons` vs `index._undelivered` overlap; two incompatible `McNemarResult` shapes (`src/governed_bi/eval/analysis.py:427-433` vs `src/governed_bi/eval/power.py:61-86`); driver-level gaps remain for build-coverage composition, arm-order behavior, twin_report denominator, single-db ledger/treatment, oracle outer wiring, and retrieval cache outer handoff, while helpers and enumerative tests cover much of the logic
- Mechanism / impact: Future edits can reintroduce silent divergence between drivers or between helper truth and driver wiring. That is how several of the fixed incidents started. It is not, by itself, a current silent failure on the green offline suite.
- Counter-evidence: Many compositions were extracted specifically so they could be tested without Postgres (`run_build_phase`, `_stage_roots`, `price_verdict` enumeration).
- Recommendation: Extract promote / resume / completeness modules; converge on one McNemar type; add thin driver smoke tests for the named seams. Do not block quotes on this item alone.

---

## Redundancy and design analysis

`run_datalake.py` is the divergent-change magnet: build, promote, gold, serve, summarize, compare, price, and CLI live in one module, with private imports from `run_experiment`. That concentration explains both the density of incident comments and the remaining driver-level test gaps (F16).

Treatment delivery is checked in more than one place (`treatment.treatment_reasons` and `index._undelivered`). The duplication is intentional fail-closed layering, but string reasons can drift.

Two McNemar APIs coexist with incompatible field names (`n_paired` / `a_only` vs `n_shared` / `n_a_only`). Low runtime risk today; real API debt for offline notebooks.

Eval vs product routing defaults remain a disclosed configuration problem: the loader accepts `[routing]`, the shipped `governed_bi.toml` does not illustrate it (F15), and comments in `config.py:551-557` state why that gap made benchmark claims hard to falsify in deployment.

Oracle SQL governance stamps are disclosed counterfactuals: useful as ceilings, misleading if read as product governance rates. Keep them labeled diagnostic.

Stuck sidecar bytes after a promote marker are discarded by design; the unpromoted marker keeps the gate fail-closed. That is a known limitation, not a new defect in this tip.

---

## Documentation and reproducibility analysis

English experiment-runbook is the best operator surface at tip: size before spend, resume with original scope flags, quote checklist, twin gradeable denominator, and `single_variable` caveats. Sibling docs lag.

Reproducibility of a *claimed* result still depends on:

1. Manifest knobs (model, prompts hash, routing, embedder, skip_agent, scope).
2. Human checklist beyond `quotable`.
3. Matching `limit` / `limit_dbs` (currently not in resume guards: F3).
4. Matching docs to code for what `seeded` contains (F10).
5. Not using `datalake-run.md`'s bare `--resume-from` line as the source of truth (F15).

Chinese docs were intentionally allowed to drift during design (project policy). Several ZH files still carry older few-shot / meaning-column / DSN wording. Align them only after English is finalized for a quote.

Backlog C7 status is stale relative to wired order-sensitive exclusions (F15). Anyone triage-planning from the backlog alone will redo finished work or distrust the harness incorrectly.

---

## Verification appendix

| Check | Result |
|---|---|
| Corpus CLI | PASS, 17 assets, 0 findings |
| `pytest -q -rs` | PASS, 1347 passed, 10 skipped, 1 xfailed, 0 failed, ~76.41s |
| Static type / lint gates | Not configured for this pass |
| Network / LLM / Postgres / BIRD / paid runs | Not run |
| Effect sizes / EX deltas | Not validated by this review |
| Working tree (check worker) | Remained clean |

Source inspection for this report covered the loci cited in F1-F16. Commit subjects across `a5f2128..d9934f5` were used for the thematic inventory only.

---

## Prioritized remediation

1. **Before any paid parallel or resume build:** F1 completeness marker; F2 SME ledger path across relocate; F4 atomic promote; F5 atomic JSONL rewrite.
2. **Before scale resume as an operator habit:** F3 persist and guard `limit` / `limit_dbs`.
3. **Before quoting any ladder delta:** F9 align gradeable denominators; F10 / F15 English (then ZH) claim and command sweep; treat F8 checklist as mandatory, not optional commentary on `quotable`.
4. **Before twin headlines on mixed-resume artifacts:** F7 require full twin stamp coverage.
5. **Next hygiene pass:** F13 rename / document `execution_error`; F14 unresolved `tables_used` accounting; F6 fatal drift option for paid profiles; F12 paired-only pricing.
6. **Debt track (non-blocking for quotes):** F16 extract modules and add thin driver smokes.

**Do not quote until:** F1-F4 operationally fenced or fixed; F9 and F10 corrected or explicitly overridden in the claim text; runbook checklist items (replicate, MDE, Holm, cluster, single-variable, twin) all satisfied for the specific sentence being published.
