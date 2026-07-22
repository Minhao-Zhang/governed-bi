# Implementation plan: ADR 0003 (governed notes) + ADR 0004 (local-first run logging)

_Proposed build plan, 2026-07-22. Source ADRs: [0003](../adr/0003-governed-notes-tri-modal-retrieval.md), [0004](../adr/0004-local-first-conversation-run-logging.md); decisions [D17/D18](../design-decisions.md). Gated on the M0 decisions in section 2; no code has started._

## 1. Build order at a glance

```
M0  decisions (C2,Q2,C1,H1,H11) + SPIKE-1 + SPIKE-2      [blocks everything gated]
        |
        +------------------------- can start TODAY in parallel: ---------------+
        |                                                                      |
        v                                                                      v
M1  shared foundation                                              (ADR-0004 metadata track
    X1 provenance.py  (turn/run/thread ids, serve_config_hash,      is decision-INDEPENDENT;
       corpus_release_hash stub, Producer/DataSplit enums,          L1 ledger-stamp needs no
       db + checkpointer/DSN config)   [UNBLOCKED now]              decision, see §3)
        |
        v
M2  logging metadata track (ADR 0004)          M3  notes schema Phase 1 (ADR 0003)
    L1 ledger duration+ts   [no dep]               N1  NoteAsset (3-field) + delete Skill types
    L2 checkpointer deps+config (needs SPIKE-2)    N2  ids.py rule->note, drop skill
    L3 attach durable saver (SPIKE-2)              N3  loader notes dir, delete skill path
    L4 token capture pre-coercion (SPIKE-1)        N6  config.db identity
    X4/L5 shared finalize_and_log seam             N4  validate: sentinels+drift+budget
    L6 metadata-only append + X8 idempotency       N5  serialize drop dump_skill
    L7 eval usage/cost wiring                       N7  context: kill SkillView, repoint inject
        |                                          N8  rvgd index NoteAsset
        |   (M2 and M3 touch nearly disjoint       N9  schema_router drop skills filter
        |    files; only collide in X1 + base_     N10 presenter drop SkillView/n_skills
        |    provenance; parallelize after X1)     N11 api: drop /skills, flip AssetTypeFilter
        |                                          N12 __init__/cli sweep
        |                                          N13 corpus content migration
        |                                          N14/N15 tests + docs
        v                                              |
M4  notes retrieval+governance (ADR 0003 Ph2-7)  <----+  (needs P1 = M3 done)
    R2 injection resolver (5 scope kinds, on_match path, must_honour/advisory render)
    R3 always-budget + precedence CI cap
    R4 no-EX-regression eval arm
    R5 read_notes/grep_notes (own audited path, ReDoS bound)
    R6 C5 content-scan validator (PII-prose structural fix)
    R7 trigger-PIN (keyword) into shortlist (dev-only/unranked)
    R8 certified-gates-PIN (publication_status tiebreak)  [ships WITH R7 authority, H2]
    R10 held-out routing gates (recall + adversarial-wrong-note)
    R9 deferred max-pool vector (only if R10 shows recall caps EX)
    R11 adversary.refute() for notes
        v
M5  full-content + deep-agent logging (ADR 0004 gated)
    F0 H11 policy amendment  [gates F1]
    F1 full-content tier + LEDGER STRIP (the real metadata-only guard)
    F2 file perms + retention prune
    F3 shared deep-agent record + make_durable_checkpointer factory
    F6 deep-agent token/cost callback
    F4 curator run logging   F5 SME run logging   F7 durable clarify_checkpointer (H10)
```

**Parallelism:** After X1 lands, M2 (logging) and M3 (notes schema) run in parallel: they touch disjoint files (`0004`: agent/governance/middleware/config/graph_app; `0003`: schemas/context/retrieval/tools). M4 is serial after M3 (needs the renamed `NoteAsset`). M5 is serial after M2 (needs the finalize seam + append).

---

## 2. M0: resolve-first (nothing gated starts until these land)

Record all resolutions in `docs/design-decisions.md` (D17 for 0003, D18 for 0004) + mark the ADR Open Questions resolved.

### Gating decisions

**C2 (kind vs enforcement): adopt THREE fields.** `kind` (descriptive taxonomy) + `activation` (`always`|`on_match`) + `normative_force` (`must_honour`|`advisory`), with a `model_validator(mode="after")` that defaults `activation`/`normative_force` from `kind` but leaves both overridable.
*Rationale:* the ADR's own Open Q C2 (lines 403-409) states a single derived `enforcement` is "`kind` relabeled" and blocks a keyword-triggered `business_rule` (`on_match`+`must_honour`); M4's render step (R2) needs `normative_force` to pick the "must honour" vs "advisory" section header. Writing the schema once in Phase 1 avoids a second `NoteAsset` migration at Phase 2.
*Note the dissent:* the `notes-datamodel-phase1` track argued two fields (`kind`+`enforcement`) with the third deferred as YAGNI. Overruled because Phase 2 needs `normative_force` immediately and greenfield schema-churn is cheapest done once. **N1 builds the three-field shape.**

**Q2 (scope encoding): sentinel strings now.** `scope: list[str]`; asset ids never contain `:`; `schema:<name>` resolves against `list_schemas` (`schema_router.py:36-38`), `db:<name>` against the new `DataSourceConfig.db` identity, `[]` = global.
*Rationale:* the string form is a strict subset of a structured `ScopeTarget`, so a later upgrade needs zero data migration; only `validate.py:151-153` `require()` and the N2 resolver need to learn two prefixes.

**C1 (publication_status): plain serve-visible Inference-tier field** `publication_status: Literal["proposed","draft","certified"]="proposed"` on `NoteAsset`, surviving `for_analyst` (which nulls only `audit`, `loader.py:105-107`). Add a `validate.py` cross-check: when `audit.provenance` is present, `publication_status` must equal `audit.provenance.status`.
*Rationale:* R8's PIN tiebreak and the zero-authority rule read it at serve time, where `Audit` is already null; sourcing from `Audit` would make it invisible.

**H1: always-note budget + precedence.** Config-driven cap: max 8 global (`scope=[]`) `always` notes AND ≤2000 chars total injected notes section (start conservative). Precedence tuple for overflow/conflict: (1) `publication_status` certified>draft>proposed, (2) `normative_force` must_honour>advisory, (3) confidence desc, (4) scope specificity asset>schema>db>global, (5) id asc. Genuinely contradictory `must_honour` notes on one scope are both rendered (first client of R11 `refute()`), never silently dropped.
*Rationale:* prevents the every-prompt bloat this ADR fixes from returning relabeled as notes; deterministic tie resolution is required before R2 renders >1 note.

**H11 (log privacy/retention): metadata-only default-on now; full content gated.** Three tiers (A metadata always / B verbatim text under `log_full_content` / C row previews under `log_row_previews` AND `log_full_content`); 30-day TTL on B/C; store file 0600 in 0700 dir on POSIX with an explicit win32 single-operator caveat (win32 `os.chmod` cannot restrict group/other, document, don't pretend); `log_full_content` refuses to enable when `environment==prod` unless `log_full_content_prod_ack=True` (fail loud, mirroring `stack.py` single-access guard).
*Rationale:* unblocks the metadata track immediately while keeping verbatim exposure a deliberate opt-in. **Also fix the stale D18 line (~608-609) that claims full content is "stored now"**. It contradicts the corrected ADR 0004; the ADR is authoritative.

### Spikes (pass/fail)

**SPIKE-1: token state-write mechanism** (`middleware.py:159,189-216`). Confirmed from reading: `_coerce_single_tool_call` rebuilds the `AIMessage` from only `content/tool_calls[:1]/id/additional_kwargs` (lines 189-196), dropping `usage_metadata`. `wrap_model_call` returns a `ModelResponse` (result, structured_response), NOT the `Command(update=)` path the ledger uses.
- **PASS:** a kept unit test proves (a) current coercion drops `usage_metadata` on a 2-tool-call `AIMessage`, and (b) a subclass `after_model(state, runtime) -> dict` return lands in an `Annotated[list, operator.add]` channel on `GovState` after `agent.stream`. Mechanism = preserve `usage_metadata`+`response_metadata` through coercion, read `state["messages"][-1].usage_metadata` in `after_model`.
- **FAIL** (if `after_model` isn't available in the installed LangChain 1.3.x): fall back to reading usage off returned `AIMessages` in a custom post-model hook; do NOT attempt a `Command(update=)` from `wrap_model_call`.

**SPIKE-2: durable vs ephemeral persistence** (`langgraph.json`, `graph_app.py:99-107,181`). Grounded expectation: `langgraph.json` pins `langgraph-cli[inmem]`, so `langgraph dev` injects an ephemeral in-memory saver (lost on restart); `build_chat_graph` compiles checkpointer-less trusting runtime injection.
- **PASS:** written verdict on (1) does `langgraph dev` persist across restart (expected **no**); (2) which deploy target injects durable Postgres; (3) whether L3 attaches the saver on the standalone-compile path, `make_graph`, or a REST route.
- **Decision rule:** if dev is `inmem`, L3 **must** attach the saver on the standalone path (ADR 0004 §1's "runtime injects durable persistence" is false for dev).

---

## 3. The unblocked start: begin TODAY

Two things need **no decision and no spike** and are the concrete "start here Monday":

1. **X1: `src/governed_bi/provenance.py`** (M1). Dependency-free (stdlib + config only). Owns: turn_id = `f"{thread_id}:{n_human}"` (aligned with `graph_app.py:131` `clarify_thread`, stable across resume), run_id per invoke, `serve_config_hash(settings, routing_knobs)`, `corpus_release_hash()` git-SHA stub (term has zero src occurrences; real identity deferred to D11), `Producer`/`DataSplit` enums, `export_allow`. Adds the `db` field + checkpointer/DSN fields to `DataSourceConfig`/`Settings` (config has neither today; `corpus_pin` defaults `beer_factory`, never `main`). **Both ADRs import this.** Build it first so M2 and M3 parallelize cleanly.

2. **L1: ledger `duration_ms`+`ts` stamp** (`middleware.py:219-354`). No dependency at all. Add `perf_counter` deltas + `datetime.now(timezone.utc).isoformat()` to every ledger dict (sample_rows err :236, cap :264, block :280, cross-schema :309, execute-error :334, pass :347). Keep keys optional-tolerant so `result_from_ledger`/`render_result` don't break.

The whole **M2 metadata track** (L1→L7) is decision-independent and runs the near-critical path in parallel with M0's C2/Q2 resolution.

---

## 4. Milestones

### M1: shared foundation
- **X1** (M) provenance.py + config `db`/checkpointer fields. Entry: none. Exit: `tests/test_provenance_ids.py` (ids deterministic+unique, turn_id matches `graph_app.py:131`, `serve_config_hash` stable/changes on top_k/RRF/threshold change, imports from analyst+corpus+curator with no cycle).

### M2: logging metadata track (ADR 0004), parallel with M3
Entry gate: SPIKE-1 + SPIKE-2 verdicts (L2/L3/L4 gated), X1.
| id | title | eff |
|---|---|---|
| L1 | ledger duration_ms+ts (started in §3) | S |
| L2 | add `langgraph-checkpoint-sqlite`+`-postgres`; `[logging]` config table (kind/path/dsn_env), DSN only from env | M |
| L3 | attach durable `conversation_checkpointer` on standalone `build_chat_graph(stack, checkpointer=)` (distinct from `clarify_checkpointer` `stack.py:172-178`); server entry per SPIKE-2 | M |
| L4 | token capture: `token_usage` reducer channel on GovState; preserve usage through `_coerce_single_tool_call`; `after_model` read; router (`agent.py:394`)+narrator (`agent.py:855`) usage into provenance extras; failed-call fallback | M |
| X4/L5 | single `finalize_and_log(answer, *, ctx)` seam hooking `GovEventStream.final`: routes all ~10 terminal outcomes (refuse_gate `agent.py:357`, cache `:470`, missing_edge `:419`, agent_core branches `:684/705/739/800/826/852`, hard-stop, recursion); rolls tokens/cost/latency/two-axis stamp/ids/hashes onto `Answer.provenance` via `dataclasses.replace` | L |
| L6/X8 | metadata-only SQLite append (JSONL selectable); at-least-once + idempotent UPSERT keyed by turn_id; try/except log-not-raise | M |
| L7 | eval wiring: `arms.py:179-196` `last_solve_meta` add usage+cost; `run_experiment.py:213` replace `usage: None`; `run_datalake.py:336` add cost | S |

Exit gate: `test_token_capture`, `test_run_log`, `test_governance_invariants`, `test_eval_usage` green; a refusal + block + cache-hit + success all carry identical metadata keys on provenance; one record per turn, replay-idempotent (row stays 1); **records carry NO verbatim question/SQL/rows** (metadata-only assertion).

### M3: notes schema Phase 1 (ADR 0003), parallel with M2
Entry gate: C2, Q2, C1, H1 resolved (M0). CI gate throughout: `python -m governed_bi.corpus.cli` + full pytest green.
| id | title | eff |
|---|---|---|
| N1 | `NoteAsset` (three-field kind+activation+normative_force, `Trigger`, `publication_status`, `governance` block) + validator; delete `RuleAsset`/`RuleKind`/`SkillKind`/`SkillFrontmatter`/`parse_skill_frontmatter`; update Asset union (`schemas.py:403-414`) | M |
| N2 | `ids.py:30,43` rule_→note_, delete skill pattern | S |
| N3 | `loader.py` `notes/` dir; delete Skill dataclass, `Corpus.skills`, skills glob (`146-150`), `_split_frontmatter` | M |
| N6 | `config.py` `DataSourceConfig.db="main"` backing identity for `db:` scopes | S |
| N4 | `validate.py:151-153` NoteAsset branch + `schema:`/`db:` sentinel resolution + publication_status drift check + always-budget finding | M |
| N5 | `serialize.py` drop `dump_skill`+skills branch; round-trip preserves `activation`/`normative_force` (exclude_none must not drop kind-default) | S |
| N7 | `context.py` delete `SkillView`+`## Skills` render (`273-276,403-408`); repoint injection (`290-297`) at `NoteAsset` gated on `activation=='always'` | M |
| N8 | `rvgd.py:102-103` index NoteAsset = title+statement | S |
| N9 | `schema_router.py:355-360` drop skills filter | S |
| N10 | `presenter.py` drop `SkillView`/`skill_views`/`n_skills` | S |
| N11 | `api`: delete `GET /skills` (`app.py:296-299`), `SkillResponse`, `HealthResponse.n_skills`; `AssetTypeFilter` rule→note (`schemas.py:270`) | M |
| N12 | `__init__`/`cli`/curator+adversary docstring sweep; grep `Skill\|RuleAsset\|RuleKind\|n_skills\|corpus.skills` = clean | S |
| N13 | corpus: `git mv rules→notes`; edit `note_boolean_flags.yaml` (activation=always); author `note_beer_factory_routing.yaml` as **activation=always scoped to table/metric ids** (NOT `schema:`, resolver matches table ids only in P1); delete `skills/routing.md` (CreditCardNumber line dropped, already covered by `governance.excluded`) | M |
| N14 | fix 5 test files; add NoteAsset round-trip, sentinel-scope, drift-check, budget-cap, on_match-non-injection tests | M |
| N15 | docs: `asset-schemas.md` NoteAsset section; finalize D17; zh + qu-ai-wei | M |

Exit gate: `corpus.cli` green with sentinel scopes; full `pytest -q` green; `/skills`→404; `?type=note` accepted / `?type=rule` 422s; no `corpus/<schema>/skills/` remains.

### M4: notes retrieval + governance (ADR 0003 Phases 2-7)
Entry gate: **P1 = M3 complete.** First step is a guard, not a feature.
| id | title | eff |
|---|---|---|
| R1 | GUARD: confirm semantic own-vector is free: NoteAsset budget (`note_k`) + `NoteAsset→note_ids` partition branch in `retrieve()` (`rvgd.py:324-330,382-393`); if P1 dropped these, notes get budget 0 and vanish silently. Assert `schema_documents` still excludes note text. | S |
| R2 | injection resolver: all 5 scope kinds (tbl/col/metric/join/schema:/db:/[]); split always (scope-inject) vs on_match (inject iff id in `retrieval.note_ids`/`triggered_note_ids`); render must_honour vs advisory sections | M |
| R3 | always-budget + precedence tuple + prompt-size CI cap (config knobs) | S |
| R4 | no-EX-regression eval arm (offline recall@k proxy in CI; live EX ON-vs-OFF documented manual gate) | M |
| R5 | `read_notes`/`grep_notes` on own audited-read path, NOT in `_GOVERNED_TOOLS` (`middleware.py:40`); honor `_is_excluded`; ReDoS-bound + output cap; reading note naming X does NOT license X | M |
| R6 | C5 content-scan validator over `NoteAsset.statement` for excluded identifiers (structural PII-prose fix); wire into R5 tools | M |
| R7 | `retrieval/triggers.py::fire_triggers` (keyword-only); PIN into shortlist (`schema_router.py:130-180`, cap ≤3, never into RRF) + `selected` (`rvgd.py:354-372`); dev-only/unranked | M |
| R8 | certified-gates-PIN: `publication_status` tiebreak + dev/prod graduation config. **Ships together with R7 authority (H2).** | M |
| R10 | held-out routing gates: GATE-RECALL (recall@3 ≥ pre-PIN baseline) + GATE-ADV-WRONG-NOTE (certified wrong-schema PIN leaves recall@3 unchanged); offline HashingEmbedder | M |
| R9 | deferred max-pool per-schema note vector: build ONLY if R10 shows recall still caps EX | L |
| R11 | `adversary.refute()` for notes (replace `adversary.py:104` NotImplementedError) + `review()` note branch; offline = review() only, LLM refute model-gated | M |

Exit gate: R2 all-5-scope tests + on_match non-injection; R3 prompt-size cap; R10 both gates green offline **before any live PIN authority ships**.

### M5: full-content + deep-agent logging (ADR 0004 gated)
Entry gate: M2 complete (finalize seam + append), H11 resolved.
| id | title | eff |
|---|---|---|
| F0 | H11 policy amendment in ADR 0004 (EN+zh) + D18 fix | S |
| F1 | full-content tier + **CRITICAL ledger strip**: with tiers off, remove `sql`+`result` from every ledger entry (pass entries embed verbatim SQL+rows `middleware.py:347-354`, merged to provenance `governance.py:598-599`). This is the real "metadata-only" guard; prod-ack fail-loud at build | M |
| F2 | store perms 0600/0700 (POSIX) + win32 caveat; `prune_full_content(ttl_days)` keeps Tier A | M |
| F3 | `make_durable_checkpointer(settings)` factory + `emit_run_record` (one mechanism, three producers); deep agents invoked via `.invoke()` get NO server injection: must be handed an explicit checkpointer | M |
| F6 | deep-agent token/cost via `UsageMetadataCallbackHandler` appended to `tracing_callbacks()` (`pipeline.py:265-268`, `sme.py:219-221`); failed-invoke fallback record | M |
| F4 | curator run logging: checkpointer + run_id (from out_root) + record at both invoke sites (`pipeline.py:531,695`) | M |
| F5 | SME run logging: producer='sme'; both deep-agent + single-shot fallback; log sanitized answer only | S |
| F7 | H10: durable `clarify_checkpointer` via `make_durable_checkpointer` with distinct thread namespace; InMemorySaver fallback for offline | S |

Exit gate: with `log_full_content=False` a governed turn's record has NO question/SQL/answer text and every ledger entry has `sql`+`result` absent (regression guard); prod+full-content+no-ack raises at `build_stack`; curator/SME emit one record per invoke incl. error path.

---

## 5. CI / eval gates

**Offline unit (`ci.yml`, no key):**
- `corpus.cli` green, but `validate.py:151-153` must accept `schema:`/`db:` sentinels first (Q2), else a scoped note reddens CI (hard dep of N4).
- `test_provenance_ids` (X1), `test_config`, `test_corpus`, `test_context`, `test_api`, `test_presenter`, `test_schema_router`, `test_retrieval`.
- scope-sentinel test: `schema:`/`db:` resolve; genuine dangling ref still reddens.
- prompt-size cap: `scope=[]` always-notes ≤ H1 budget (count + chars).
- token-nonzero: `run_experiment` no longer hard-codes `usage: None` (`run_experiment.py:213`).
- ledger duration/ts present on pass+block+cap.

**FakeListChatModel harness (`src/governed_bi/llm/fake.py`, offline):**
- log-coverage: each of ~10 terminal outcomes → exactly one portable record, stable ids, no double-`final`.
- token capture survives 2-tool-call coercion (SPIKE-1 regression lock).
- migrated always/on_match notes actually reach rendered prompt (0003 honest-limit #3).
- `read_notes`/`grep_notes` honor `governance.excluded` and grant no license.
- log idempotency: clarify interrupt/resume yields one record.
- full-content-OFF → no verbatim in record (F1 regression guard).

**Live / gated eval ladder (manual, single-seed → replicate):**
- no-EX-regression: notes ON must not drop EX vs OFF on pooled BIRD (R4).
- routing recall@3 held-out (`eval/retrieval_eval.py` + `run_datalake.py` `routing_recall`): GATE-RECALL.
- GATE-ADV-WRONG-NOTE: certified wrong-schema PIN leaves recall@3 unchanged: **must be green before live PIN authority (R7/R8) ships to prod.**

---

## 6. Risk register (top 7)

1. **"Metadata-only" append silently ships verbatim SQL+rows.** Ledger `pass` entries embed `sql`+full `result` (`middleware.py:347-354`), merged into provenance (`governance.py:598-599`). If L6 lands before F1's strip, the metadata log leaks content. **Mitigation:** the ledger-strip must be in the shared `finalize_and_log` helper from day one; the full-content-OFF regression test is the guard. Sequence F1's strip logic conceptually into L6 even though F1 formalizes tiers.
2. **Notes get budget 0 and vanish (0003).** `retrieve()` budgets keyed by asset TYPE (`rvgd.py:324-330`); if the N1 rename drops the NoteAsset budget + `note_ids` partition, "semantic own-vector is free" is false. **Mitigation:** R1 is the first M4 step and a regression test.
3. **Silent content loss on note migration (honest-limit #3).** Migrating always-on routing prose as `on_match` before R2's injection path exists drops it from prompts. **Mitigation:** N13 migrates routing prose as `activation=always` scoped to **table/metric ids** (resolver matches table ids only in P1, honest-limit #4), NOT `schema:`; FakeListChatModel harness asserts injection.
4. **Token drop on parallel tool calls.** `_coerce_single_tool_call` discards `usage_metadata` (confirmed lines 189-196). **Mitigation:** SPIKE-1 + L4 preserve pre-coercion; 2-tool-call test.
5. **Wrong note evicts correct schema from top_k=3.** A single uncertified PIN can drop the true schema. **Mitigation:** certified-gates-PIN (R8) + GATE-ADV-WRONG-NOTE green before any live PIN authority.
6. **Durable persistence not injected on server (SPIKE-2).** `build_chat_graph` compiles checkpointer-less trusting runtime; `langgraph dev` is likely ephemeral. **Mitigation:** SPIKE-2 verdict; L3 attaches saver on standalone path; `clarify_checkpointer` stays non-durable until F7.
7. **`create_deep_agent` may not forward a checkpointer.** F3/F4/F5 assume it does. **Mitigation:** verify early (deep-agent analogue of SPIKE-2); if not, reach into the compiled graph or wrap the invoke. Also: durable checkpointing over 69-schema curator batches is non-trivial I/O: keep curator checkpointing opt-in, always-on only for the portable record.

Cross-cutting caveat: all EX/recall/adversarial numbers are single-seed (per MEMORY, SME +0.174 broke the pattern on one seed). Treat every eval gate as provisional until replicated; document seed in gate output; do not gate prod on one run.

---

## 7. Recommended first two weeks

**Week 1: foundation + unblocked logging (no decisions needed to start):**
- Day 1: Write M0 decision resolutions into D17/D18 (C2=three fields, Q2=sentinels, C1=serve-visible field, H1=budget, H11=metadata-first). Fix stale D18 line. Run **SPIKE-1** (`after_model` + coercion drop) and **SPIKE-2** (`langgraph dev` durability): both are ½-day probes; record verdicts.
- Day 1-2: **X1 `provenance.py`** + config `db`/checkpointer fields → `test_provenance_ids` green.
- Day 2: **L1** ledger `duration_ms`+`ts` (independent, ships immediately).
- Day 3-4: **L2** checkpoint deps + `[logging]` config; **L4** token capture channel + preserve-through-coercion + `after_model` (uses SPIKE-1).
- Day 5: **X4/L5** shared `finalize_and_log` seam wired through all terminal outcomes; **L3** attach durable saver on standalone path.

**Week 2: close the metadata loop + start notes schema in parallel:**
- Day 6-7: **L6/X8** metadata-only SQLite append + idempotent UPSERT; **L7** eval usage/cost wiring (`usage: None` gone). M2 exit gate green (this is a shippable, honest slice: durable conversations + per-turn token/cost/latency metadata, zero verbatim content).
- Day 6-10 (parallel, different files): begin **M3 notes schema**: **N1** (three-field NoteAsset + delete Skill types), **N2/N3/N6** (ids, loader, config.db), **N4** (validate sentinels+drift+budget). These touch `schemas/loader/validate/config`, disjoint from the M2 files, so a second person or context can run them concurrently once X1 exists.

This slice is honest: it ships the decision-independent ADR 0004 metadata value end-to-end (the "start here today" path) while de-risking the two spikes and standing up the shared id model both ADRs depend on, and it begins the notes rename without touching any gated retrieval behavior.

**Key files touched this fortnight:** `src/governed_bi/provenance.py` (new), `analyst/middleware.py`, `analyst/agent.py`, `analyst/governance.py`, `analyst/run_log.py` (new), `api/stack.py`, `api/graph_app.py`, `config.py`, `eval/{arms,run_experiment,run_datalake}.py`, `pyproject.toml`, `langgraph.json` (read), plus M3's `corpus/{schemas,ids,loader,validate,config}.py`.