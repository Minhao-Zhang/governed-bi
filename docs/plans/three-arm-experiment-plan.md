# Engineering Plan: Three-Arm Accuracy Experiment

_Status: draft 2026-07-12 (rev. 2, after data archaeology). Implements the proof
behind D1 ("curation beats accumulation") on top of
[pipeline-design.md](../pipeline-design.md). Baseline = clean post-revert HEAD
bf68a17. Deep-agent curator from the start (per decision). Target DBs:
`cs_semester`, `ice_hockey_draft`._

## 1. Goal and the three arms

Run two BIRD databases through three configurations, score execution accuracy
(EX) on the **held-out test split**, and compare:

| Arm | Corpus the serve path sees | Measures |
|---|---|---|
| **A1 — Baseline (no layer)** | none; LLM gets raw schema (incl. injected decoy/trap columns) | plain text-to-SQL with no semantic layer |
| **A2 — Curated, no clarification** | Facts + assets a deep-agent curator inferred from **all train (question, gold SQL) pairs** | value of curating from working SQL (A2 − A1) |
| **A3 — Curated + SME** | A2 corpus + edits from a Simulated SME answering the curator's clarification questions | value of the SME round-trip (A3 − A2) |

Success **as an experiment** = three comparable EX numbers per DB with leakage
invariants (§6) intact. Hoped-for result `A1 < A2 < A3`.

## 2. What these two DBs actually test (read before running)

`cs_semester` and `ice_hockey_draft` are **English → identity rename map**
(`eval_dataset/schema_rename_map.json` maps every identifier to itself). So on the
`rename_decoy` instance their real columns keep their readable names; the only
obfuscation is **injected decoy/trap tables + columns** (populated with
corrupted-but-plausible data; `trap_manifest.json`, `trap_table_manifest.json`).

Consequence: this experiment measures the layer's ability to **steer onto the
real columns and away from the traps**, not to de-opaque renamed columns. The
`decoy-touch rate` that `eval/arms.py::run_arm` already computes is therefore a
first-class metric here, reported next to EX. A2's edge should come from the fact
that **train gold SQL never references decoy/trap columns**, so the curator can
infer they're not load-bearing; A3's edge is the SME confirming which columns are
meaningful. (If you later want to test the *rename* moat, pick a non-English DB —
but then the description CSVs in §W3 leak the de-obfuscation map and the SME brief
needs a different source.)

## 3. Environment: Postgres, not SQLite

The trapped DBs exist **only as Postgres**. Setup (in `BIRD-Data-Obfuscation`):

```
hf download minhaozhang/BIRD_Obfuscation --repo-type dataset --local-dir bird_obf_dumps
docker compose --profile decoy up -d pg_rename_decoy          # port 5435
docker compose cp  bird_obf_dumps/pg_rename_decoy.dump  pg_rename_decoy:/tmp/d.dump
docker compose exec pg_rename_decoy pg_restore -U bird -d bird --no-owner -j 4 /tmp/d.dump
```

governed-bi then connects with its **`PostgresConnector`** (D15) to
`host=127.0.0.1 port=5435 dbname=bird user=bird password=bird`, using schema
`cs_semester` or `ice_hockey_draft`. The clean SQLite originals
(`data/train/train_databases/{db}/{db}.sqlite`) have **no traps** — usable only as
a plumbing smoke test, never the headline number.

## 4. Data map (concrete paths, all in `../BIRD-Data-Obfuscation`)

| Need | Path / field |
|---|---|
| Train pairs (curator input) | `eval_dataset/train_final.jsonl`, filter `db_id`; NL = `question`; **gold SQL = `sql_rename`** (Postgres, schema-qualified); `evidence`, `question_id`, `difficulty` | 
| Test questions (serve input) | `eval_dataset/test_final.jsonl`, filter `db_id`; use `question` only (never gold) |
| Row counts | train: cs_semester=90, ice_hockey_draft=67; test: cs_semester=23, ice_hockey_draft=17 |
| Gold result hashes | `eval_dataset/gold_result_hashes_rename_decoy.jsonl` (`question_id`,`hash_lenient`,`hash_strict`,`sql_sha256`,`nrows`) — precomputed on pg_rename_decoy for every row |
| Grader (source of truth) | `pipeline/grade_offline_eval.py` + `pipeline/_db.py::hash_normalised_result{,_strict}` |
| SME brief source | `data/train/train_databases/{db}/database_description/*.csv` (cols: `original_column_name,column_name,column_description,data_format,value_description`) — safe here (identity map) |

Note: `bird_loader.load_bird_items` today reads the `sql_sqlite` field; for this
experiment it must read **`sql_rename`**. Small change (add a `gold_sql_field`
param), or a thin BIRD-Obfuscation-specific loader.

## 5. Grading: reuse the reference scorer

Do **not** re-implement EX. Two compatible options, recommend (a):

- **(a) Reference grader (recommended).** For each arm, governed-bi produces a
  `generations.jsonl` (`request_id`, `request_sha256`, `generated_sql`, `usage`,
  `latency_sec`) against a bundle built by
  `pipeline/prepare_offline_eval.py` (its private manifest holds gold, kept off
  our side). Then `pipeline/grade_offline_eval.py` executes only our SQL on
  pg_rename_decoy and scores lenient + strict EX → `eval/ablation_results.jsonl`.
  Numbers are directly comparable to the dataset's own published ablation.
- **(b) Self-contained hash compare.** governed-bi executes the model SQL on
  pg_rename_decoy, applies the *identical* normalise+SHA-256 from `_db.py`, and
  compares to `gold_result_hashes_rename_decoy.jsonl`. No gold SQL needed on our
  side, but we must match their normalization byte-for-byte. Keep as a fast
  cross-check, not the primary scorer.

## 6. Workstreams

### W0 — Stand up the environment (blocker; do first)
Restore `pg_rename_decoy` (§3); verify governed-bi `PostgresConnector` lists the
two schemas and their tables (incl. decoy columns). **Acceptance:** `SELECT`
through the gateway against schema `cs_semester` returns rows.

### W1 — No-layer baseline solver (A1)
`eval/baseline_solver.py::no_layer_solver(connector, gateway, chat)` → a `Solver`
that dumps raw schema (names + types only, decoys included, **no** curated
assets), asks the generation LLM for one `SELECT`, returns SQL or `None`. Bypasses
retrieval + corpus. **Acceptance:** produces `generations.jsonl` for the test
bundle.

### W2 — Deep-agent curator over train pairs (A2 core — main build)
Extend `curator/deep_agent.py` (deepagents 0.6.12 confirmed installed):
- **Context:** feed the batch of train `(question, sql_rename, evidence)` pairs for
  the DB into the agent.
- **Read tools (exist):** `profile_facts`, `run_probe_query`.
- **New write/propose tools** the agent calls to mutate the local corpus:
  propose `JoinAsset` (from a SQL's `JOIN..ON`), `MetricAsset`, `TermAsset`,
  `FewShotAsset`, table/column `description`, and **mark a column suspect**
  (the decoy/trap defense). Each writes Inference-tier assets with provenance.
- **Deterministic seed pass:** before/under the agent, parse each train
  `sql_rename` with sqlglot (pattern from `eval/arms.py`) to hand the agent
  candidate joins/metrics — cheap grounding so the agent verifies rather than
  invents.
- **Governance:** adversary runs as a *signal* (design §1), not a gate; the agent
  edits freely; nothing is trusted until §7 + PR.
- Orchestrator `curator/pipeline.py::build_curated_corpus(...) -> corpus_root`
  chains profile → deep-agent curation → `write_corpus` into an A2 output dir
  (fresh path; do **not** overwrite existing `../BIRD-corpus/{db}`).
- **Acceptance:** A2 corpus contains Facts + ≥1 agent-inferred join and ≥1 column
  marked suspect; `flow_solver` on it answers test questions.

### W3 — Simulated SME + clarification round-trip (A3)
- `curator/sme.py::build_sme_brief(db_description_csvs, train_items) -> str`
  (domain context from the description CSVs + train `question`/`evidence`; **no
  gold SQL, no test items**), and `SimulatedSme(chat, brief)` implementing the
  existing `Responder` (`answer(question)->str`).
- Extend `pipeline.py`: A2 assets → `emit_clarifications` →
  `resolve_clarifications(responder=SimulatedSme)` (folds via `accept_answer`) →
  optional agent re-pass → `write_corpus` to A3 dir. Synchronous SME is fine for
  eval; async persistence (design §4) is out of scope here.
- **Acceptance:** A3 ≠ A2 (≥1 clarification folded, provenance `human`); leakage
  test asserts no gold SQL / test-question text entered the SME prompt.

### W4 — Experiment runner (glue)
`eval/run_experiment.py` (+ `__main__`):
`python -m governed_bi.eval.run_experiment --db cs_semester --bird-dir ../BIRD-Data-Obfuscation --pg-dsn postgresql://bird:bird@127.0.0.1:5435/bird --out runs/`
1. Load train + test items; assert `question_id` sets disjoint.
2. Build `PostgresConnector`/`Gateway` on schema `--db`.
3. A1: `no_layer_solver`. A2: `build_curated_corpus(train)`. A3:
   `build_curated_corpus_with_sme(train)`.
4. Each arm → `generations.jsonl` over the test bundle.
5. Hand off to `pipeline/grade_offline_eval.py` (§5a); collect lenient/strict EX,
   per-`difficulty` EX, refusal rate, decoy-touch rate; write
   `runs/<ts>_<db>/{manifest.json,generations.*.jsonl,summary.json}` + a
   comparison table.
**Acceptance:** one command → three EX numbers + two deltas per DB.

### W5 — Serve config
A1 bypasses corpus; A2/A3 use `flow_solver(corpus, gateway, settings, identity)`
with the arm's corpus. Multi-schema router / LLM schema-pick node (design §5.1) is
**out of scope** (single target schema per run). Decide refusal accounting:
recommend **refusal = EX 0**, with refusal rate reported so a high-refusal arm is
visible.

## 7. Leakage invariants (assert + test)
1. Curator sees only **train** `sql_rename` (train gold is *allowed* — that's the
   premise). Test gold SQL and test questions never reach curation or the SME.
2. `run_experiment` asserts train/test `question_id` sets disjoint.
3. SME brief built from description CSVs + train evidence only; test asserts no
   `SELECT`/gold substring and no test-question text in it. (Description CSVs are
   leakage-safe here *only because* the rename map is identity for these DBs.)
4. Gold is executed only by the reference grader on its own machine/manifest;
   governed-bi's serve path never sees test gold.

## 8. Milestones
- **A — plumbing smoke (½–1 day):** W1 + W2 + W4 against a clean SQLite original
  (no traps) with a handful of questions. Goal: runner emits three numbers. Not
  signal (too easy).
- **B — real signal (the experiment):** W0 (pg_rename_decoy) + W3, both DBs. The
  headline `A1 < A2 < A3` (+ decoy-touch deltas).
- **C — optional:** more DBs; a non-English DB for the rename moat (needs a
  leakage-safe SME brief source).

## 9. Cost / logging
LLM stages: A2/A3 deep-agent curation (multi-turn, the priciest — bound
max agent steps) + per-test-question generation × 3 arms. `obs.py` logs tokens;
size one DB (≤90 train, ≤23 test) before running both.

## 10. Open decisions
1. **Confirm the two DBs test decoy/trap avoidance, not rename** (§2) — expected,
   given they're English; flagging so it's deliberate.
2. **Grader: reference `grade_offline_eval.py` (recommended) vs self-contained
   hash compare** (§5).
3. **Refusal accounting** (§5/W5) — recommend EX 0 + report refusal rate.
4. **Cross-repo work:** W0/W4 touch `BIRD-Data-Obfuscation` (env + grader);
   W1–W3 touch governed-bi. Commit per-workstream, both repos.
