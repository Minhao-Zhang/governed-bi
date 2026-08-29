# 0005: The v2 memory layer and faceted retrieval

- **Status:** Accepted (2026-08-03). `register/`, `corpus/`, `measure/`, `retrieve/`,
  `serve/` and `eval/` are on `main`. The curator is still absent from `src/`.
- **Deciders:** project owner + design session (2026-08-02)
- **Scope:** the memory layer (asset schema, corpus) and the retrieval + serve
  graph. **Execution-time governance — the guardrail layers, the function
  allowlist, identifier canonicalisation, `guard`'s rule set — is
  [ADR 0006](0006-execution-time-governance.md)** and is a hard dependency of
  this one.
- **Related:**
  - **`lessons-from-v1.md` (deleted with v1)** — what v1's failures cost.
    Cited below as **L§n** (thematic section) and **L-R#** (the five top-level
    rules). Read it before writing v2 code.
  - [0002](0002-governed-agentic-serve-runtime.md) — the agentic serve core.
    Its *shape* survives; its safety spine is superseded by 0006.
  - [0004](0004-local-first-conversation-run-logging.md) — the write-only run
    log; superseded in part by [0014](0014-one-conversation-store.md).
  - [design-decisions.md](../design-decisions.md) — index into the ADRs.
- **Supersedes:** ADR 0003 in full (`NoteAsset`, `NoteKind`, `NoteActivation`,
  `NormativeForce`, `Trigger`/PIN, the "tri-modal" framing); the `RVGD` name;
  the `description` field name.
- **Amended by** (added 2026-08-22 — four later ADRs declared amendments to this one and none
  was pointed at from here):
  - [0008](0008-identifiers-end-to-end.md) at **§1.2 / §2.8.2** — D1's key/name split, and D4
    withdrawing §2.8.2's "a physical name, bare or qualified" reference kind.
  - [0011](0011-two-model-split-and-facet-query-rewriting.md) at **§2.3** — four of the five
    facets call a model; only `facet_schema` searches the raw question.
  - [0012](0012-access-seam-principal-and-authorization.md) §3 — `licensed` is narrowed to
    "what retrieval found this turn", with authorization as a second, independent question, so a
    licensed table can still be refused `r_table_not_authorized`. It reaches **§3.2 / §3.5**
    here, where `licensed` is declared and the tool bounds are stated. (0012's own header cites
    the target as "0005 §8"; this ADR has no §8 — §8 is ADR 0006's tool-bounds section.) 0012
    was cited nowhere in this file before this line.
  - [0014](0014-one-conversation-store.md) at **§4** — which fields a record carries, and the
    `turns` channel as the place a whole conversation survives.

> **This repository does not produce the served corpus.** The curator is absent from
> `src/`, and there is no rebuild kit. The semantic layer is a **sibling checkout named by
> `GOVERNED_BI_CORPUS_DIR`**; this engine only loads it. Versioned is not
> reproducible-from-this-tree.
>
> > **Corrected 2026-08-22.** This used to say the semantic layer *is* the sibling
> > [BIRD-corpus](https://github.com/Minhao-Zhang/BIRD-corpus) checkout, present tense. It is
> > not what the tree loads today: `.env` sets
> > `GOVERNED_BI_CORPUS_DIR=../MS Fabric Facilities/corpus` with the BIRD line commented out,
> > and `GOVERNED_BI_PG_DSN` points at the facilities warehouse — the corpus directory and the
> > DSN are one switch and must move together. Read `.env` for what is served. Measurements
> > below that name their corpus hash stay quotable *on the corpus they name*; none of them
> > describes the default configuration.
>
> The §3.1 topology has drifted from `serve/graph.py`. Use
> [architecture.md](../architecture.md) or the graph itself: `abstain`, `reflect`,
> `narrate`, `accept`, `fanout` and `record` run and the diagram does not draw them.

---

## Context

### The problem in one sentence

Table and column retrieval — what decides which schema and which tables a
question reaches — runs on a single field, `description`, written by exactly
one actor: the curator agent, which on 6 of 57 schemas wrote nothing at all.

### The evidence, condensed

Full detail and provenance in `lessons-from-v1.md` (deleted with v1).
Figures from `runs/datalake/20260731T233457Z-opus48-high-ladder/20260731T233545Z`
(57 schemas, 1,351 questions/arm, 5,404 rows) unless another artifact is named.

| finding | evidence |
|---|---|
| `description` has one producer, and it sometimes produces nothing | 0% filled on baseline/seeded (by contract, `curator/profile.py:59`); 71.8%/71.0% on curated; **zero writes on 6 of 57 schemas**, distribution is a cliff (0 or ≥24). `mondial_geo` measured at 0/42 tables, 0/275 columns |
| It is not a budget problem | median utilisation 33%, 0/57 exhausted, budget↔question correlation **−0.353**; `works_cycles` spent 1,583 tool calls at budget 339 and asked nothing |
| `NoteAsset` never did its job | 139 notes, all `kind=context`, all `advisory`, **zero `must_honour` ever** (structurally unreachable). The 398/401 summary lengths are `_clip_words(answer, 400)`, not model behaviour |
| The reason we had for not fusing channels was false | real numbers: recall@1 **BM25 0.736 > embedding 0.694**; @3 0.844 vs 0.852; @10 0.906 vs 0.953 (`runs/ablation/e1-shortlist-curated.json`). Re-measured RRF wins @1 and @3. The retired "0.35" was repeated in six places and a test asserted it |
| Why BM25 wins | **BIRD obfuscation is translation, not randomisation** — German/French/Spanish physical names plus paired decoys. Real semantics in the wrong language: exact for BM25, weak for cross-lingual embedding |
| Document shape is broken independent of channel | `asset_document()` concatenates a table + all its columns; routing concatenates all of a schema's assets. `works_cycles` = 73 tables in one vector |
| Columns are never ranked | no per-column document; `column_ids` derives from selected tables (`rvgd.py:795-801`) |
| `NegativeExampleAsset` is unreachable | 0 in all 22 corpora; no budget entry (`budgets.get(cls, 0)`); the keyword/Jaccard gate fired 0 times in 5,404 rows |
| Prompt caching never used on Anthropic | `cache_read = 0` across 49.4M input tokens; no `cache_control` anywhere. OpenAI caches automatically (55–58% measured); Anthropic needs the marker. Context sits in the **system** prompt |

### Why this cannot be patched

Each finding has a local fix and they fight: capping `description` improves the
index and starves the prompt; adding notes to routing improves note recall and
worsens schema recall; a column index competes with the table document that
still contains those columns. **One field serves two consumers with opposite
requirements, and the index's unit of text is inconsistent.** Both are
schema-level facts.

---

## Decision

### 0. Four invariants

> **I1. `summary` is the only field that enters the retrieval index.**
>
> **I2. `body` is what the system uses once the asset is hit.**
>
> **I3. Structural fields always render** — physical name, logical type, role,
> `reliability.suspect` — for every asset in context, whether or not it has a
> `body`.
>
> **I4. Everything delivered to the model in a turn is hashed.**
> Not just `assemble`'s output: tool returns that carry corpus text are hashed
> too. See §3.6.

Consequences:

- **`summary` does not enter the prompt.** Every `body` must therefore be
  self-contained — the model never sees the summary. This is why
  `FewShotAsset.body` repeats its question.
- **`body` is never embedded, contributes to no score.** Length is
  unconstrained; it costs prompt tokens, not recall.
- I2 says "what the system uses", not "what goes to the agent", so
  `NegativeExampleAsset` is not an exception: its body renders a refusal.
- I3 exists because a `body`-less column would otherwise be **invisible** — the
  model would not know it exists. It also guarantees no budget can delete a
  `suspect` warning (L§2: under obfuscation a decoy column is *designed* to
  rank low, so it is exactly what a relevance cap removes; deleting the warning
  while leaving the column reachable is strictly worse than not capping).
- I4 exists because the treatment this ADR introduces — curated `body` text —
  reaches the model partly through a tool, and a hash taken at `assemble` alone
  would be blind to the thing the arms differ in (**L-R2**).

### 1. Asset specification

#### 1.1 Common shape

```python
class XxxAsset:
    asset_type: Literal["xxx"]
    id: str

    summary: str              # ≤ 250 chars, non-empty. The only indexed text.
    body: str | None          # unbounded. Used on hit.

    governance: Governance    # D6 — on EVERY asset (§1.5)
    confidence: float | None        # curation-time belief, NOT an outcome score
    audit: Audit | None
```

`confidence` is called out because the first thing a feedback loop will want is
to write a hit rate into it. It must not: curation-time belief and
outcome-derived score are different quantities with different lifetimes.

**Validation lives in the Pydantic model**, not at the tool boundary, so every
writer is covered — tool calls, the seed, hand-edited YAML, the loader.

> **Corrected 2026-08-22, and the correction inverts the point.** There is no Pydantic model.
> The asset types are frozen `dataclasses` — `corpus/schema.py` imports `dataclasses` only, and
> its own module docstring says "Validation is `.validate`, not construction." The rules in the
> table below are enforced at **load time**: `corpus/validate.py::problems_with` plus
> `corpus/identity.py::validate_asset_id`, reached from `corpus/store.py` and
> `corpus/seed.py`. So "every writer is covered" holds only for writers that go *through the
> store or the seed*. An asset constructed in memory and handed straight to a consumer is
> **not** validated — which is exactly the gap the sentence was written to deny. Moving
> validation into construction, or keeping it at load and saying so, is an open choice.

| rule | applies to |
|---|---|
| `1 <= len(summary) <= 250` | all eight |
| `identifier_field()` value appears in `summary`, when the type declares one | see table |

**Not every asset has a physical identifier.** A single blanket rule would be
silently per-type-skipped, which is the shape of v1's vacuous tests (L§7). Each
type declares one explicitly:

| asset | `identifier_field()` |
|---|---|
| `SchemaAsset` | `name` |
| `TableAsset` | `physical_name` |
| `ColumnAsset` | `physical_name` (bare — qualification would spend the 250-char budget on text the tag rule already establishes) |
| `JoinAsset` | `{left_table}` and `{right_table}` (both) |
| `MetricAsset` | `None` — business concept |
| `TermAsset` | `None` — business phrase |
| `FewShotAsset` | `None` — summary *is* the question |
| `NegativeExampleAsset` | `None` — summary *is* the question class |

The non-empty rule applies to all eight, because a blank document is a live
production hazard: OpenAI returns a vector for it that pollutes the ranking,
**Bedrock Titan rejects it and kills the turn** (L§2).

#### 1.2 The eight assets

##### `SchemaAsset` — new

| field | content |
|---|---|
| `summary` | What this database is for. **The schema-routing signal.** |
| `body` | Business background, cross-table conventions |
| `rules: list[str]` | Schema-level hard rules, injected every turn |
| `name` | The namespace — what is today a `schema` string on every asset |

Three problems collapse into this asset: cross-table hard rules get a home;
schema routing stops concatenating every table's text; and the two retrieval
levels each get a first-class asset instead of level 2 being synthesised from
level 1. ADR 0003 noted "`db` and `schema` are not assets" and did nothing.

##### `TableAsset`

`summary` (what this table is, containing its physical name) · `body`
(self-contained: what it is, time coverage, grain caveats, which table it is
confused with, known traps) · `rules` · `grain`, `schema`, `physical_name`,
`row_count`, `columns` unchanged.

##### `ColumnAsset` — renamed from `Column`, still inline

Stored inline; **id derived by the loader** (`derive_column_id(table_id,
physical_name)`, which folds the name through `corpus/identity.py::slug` — ADR 0008 D1;
see the correction under §3.5) — columns carry no `id` in YAML. Renamed for consistency: it
has a derived id, governance, audit and reliability. (D9 lists `column` among
eight YAML asset types; that inconsistency predates this ADR and is resolved in
favour of inline storage.)

`summary` (containing `table.column`) · `body` (**value domain / code table**,
units, format, how it differs from its siblings) · `reliability` unchanged,
long reasons in `body`.

No `rules` — column-level normative force is `reliability.suspect`, which has
its own render path and works (median 27 caveats/turn).

> Correction to draft 1: the agent-authored `annotate_column(note=…)` path was
> **never** capped — the same run has reliability notes up to 619 chars. The
> 200-char cap applied only to the mechanical SME backstop. Moving long reasons
> to `body` is tidiness, not a recovered capability.

##### `JoinAsset`

`summary` (what relationship this edge is) · `body` (when to take this edge
rather than another, fan-out risk, soft-delete filters) · `left_table`,
`right_table`, `on`, `cardinality`, `cost` unchanged.

**The id must include a normalised digest of the ON clause:**

```
join_id = f"join_{schema}_{left}_{right}_{on_digest(on)[:8]}"

on_digest(on):
  parse with sqlglot → set of equality predicates
  each predicate → frozenset({lower(qualified_left), lower(qualified_right)})
  whole clause  → frozenset of those frozensets      # conjunct order irrelevant
  sha256 of the canonical sorted repr
```

Operands unordered within a predicate, conjuncts unordered within a clause,
case- and whitespace-insensitive: it identifies the **relationship**, not the
text. Without it, two relationships between the same table pair collapse and
the last write wins — `soccer_2016` kept 32 of 54 gold-derived edges,
`mondial_geo` 67 of 87, **33 of 57 schemas lost at least one edge before the
curator ever ran** (L§3).

**Joins enter the index** (v1's `asset_document()` returned `""` for them).

##### `MetricAsset` — tightened

> Always a **business metric**: a numeric quantity and a calculation formula.

`summary` · `body` (full definition, boundary conditions, common
miscalculations) · `expression` **required** · `name`, `base_table`,
`dimensions` unchanged. ~~`rules: list[MetricRule]`~~ deleted (§1.4).

##### `TermAsset` — tightened

> Always **explanatory**: a phrase, and what it refers to.

`summary` (one sentence **including all of its aliases**) · `body` (full
explanation, ambiguity, departmental variation) · `name`, `synonyms`,
`binding`, `related_terms` unchanged as structured data.

The alias requirement is load-bearing: under I1 only `summary` is indexed, so
synonyms living only in `synonyms` or `body` would sever the term→asset bridge —
and that bridge is why every *other* summary may be precise instead of
keyword-stuffed.

> `summary: "GMV (gross merchandise value, total transaction value): the total
> value of goods transacted in a period."`

**Overflow rule:** a term whose aliases do not fit in 250 chars **splits into
two `TermAsset`s sharing a binding**, rather than raising. Aliases are the
point of the asset; truncating them is worse than duplicating the definition.

##### `FewShotAsset`

`summary` = the question · `body` = question **and** SQL (question repeated —
bodies are self-contained) · `sql`, `bound_terms`, `complexity`, `schema`
unchanged.

This asset already had the right shape. Similarity between a natural-language
question and SQL text is noise (`SELECT`, `JOIN` and table names appear in
every example).

##### `NegativeExampleAsset`

`summary` = **one sentence, one entry** (the question class) · `body` = why it
cannot be answered + who to contact · **`schema: SchemaName | None`** — `None`
means system-wide.

Kept deliberately despite being empty today: BIRD questions are all answerable
by construction, but real deployments have unanswerable questions. It enters the
unified index and is matched by `semantic`; the keyword/Jaccard matcher is
deleted. It is the only asset whose hit is a **decision** rather than a
**ranking** — §2.7.

#### 1.3 `rules`

`rules: list[str]` on `SchemaAsset` and `TableAsset` only. One sentence each,
rendered under `## Must honour`.

**No `normative_force` enum. Field position is the semantics:** text in `rules`
binds, text in `body` describes. An enum can be written wrongly; a field
position cannot.

**Injection is conditional, and the ADR says so rather than implying
otherwise.** `SchemaAsset.rules` render for every selected schema, every turn.
`TableAsset.rules` render **only if that table entered context** — exactly as
retrieval-dependent as the PIN mechanism they replace. A rule that must always
appear belongs on the schema.

**`rules` is the container of last resort.** If a rule can be an asset, it must
be one:

> "Always use `amount_usd`, never `amount`" is not a rule. It is a `TermAsset`
> ("amount") bound to a `ColumnAsset` (`amount_usd`) — retrievable, verifiable,
> usable by grounding. The prose form is none of those.

A corpus with many `rules` is one whose curation failed to assetise. CI check,
not guideline.

> **Corrected 2026-08-22: the CI check does not exist.** `tools/check_corpus_conformance.py`
> declares seventeen gates, `V0`-`V16`, and none of them counts or caps `rules`. The field's
> text is *read* by `V10` (no disclosure of how an unreliable column was made) and by `V13`'s
> body cap, but nothing measures how much of the corpus lives in it. So this is a guideline
> until the gate is written.

`JoinAsset`/`MetricAsset`/`TermAsset` have no `rules` because they are already
normative: a metric's `expression` **is** the definition; a term's `binding`
**is** a mandatory mapping.

#### 1.4 Deleted

```
NoteAsset       NoteKind (7)     NoteActivation      NormativeForce
_NOTE_DEFAULTS  Trigger / PIN    propose_note        read_notes / grep_notes
note_inject.py  MetricRule       description         search_corpus
_mark_columns_absent_from_gold   (must not return — L§3)
```

**PIN.** Its two uses have better homes: "must always appear" → `SchemaAsset.rules`
(no retrieval involved); "this word must find this asset" → write it into
`summary`. `pin_triggers_enabled` always defaulted off, so it was never
validated. Removing it leaves two channels and no back door.

**`MetricRule`.** Declares only `kind` and `note` under `extra="allow"` — free
text — and collides with the new `rules` name while meaning something else (a
filter clause, i.e. part of the definition). Content goes to `body`.

#### 1.5 Tiers, governance, and the model-authored surface

The tier model is **three tiers plus Governance**: Facts, Inference, Audit — tabulated in
[`docs/corpus-format.md`](../corpus-format.md), with `corpus/schema.py::Governance` still
carrying the "outside the three tiers (D6)" clause in its docstring. Note the overload —
`Governance` and `Audit` are both tier names and class names. Tiers answer "who said
this"; `summary`/`body` answers "which pipeline does this text enter". Orthogonal.

**`governance` moves into the common shape.** In v1 only `TableAsset` and
`Column` carried it, so the five types now entering the index had **no D6
mechanism at all**.

Four enforcement points, all required:

1. **The filtered analyst corpus is authoritative and the index is derived from
   it** — one filter, one place it can be wrong. v1 had two definitions of
   "excluded" (the picker summary filtered, the ranking index did not) and
   shipped PII column names into the routing index.
2. **`resolve` and `connect` operate on the filtered corpus**, so an excluded
   column cannot return as a table's child and an excluded table cannot return
   as a Steiner point.
3. **Every tool reads through the same filtered view** (§3.5).
4. **CI asserts all three.**

`Corpus.for_analyst()` stays the single boundary function, and the
analyst-visible corpus is a **type**, not a convention — v1's caller contract
("callers are documented as passing `for_analyst()`") was unenforced and was
breached by the pooled driver.

**Exclusion is human-only, enforced by the absence of a tool.** Reliability is
AI-authorable (`suspect=True`); `governance.excluded` has no tool. *"Suspect
argues against a column and the analyst still sees it; excluded removes it,
which is a decision a person signs for."* **If v2 generates tools from a
schema, this boundary is violated by construction.**

**But absence of a tool is not sufficient, because the model owns files.** v1's
agent minted certified human facts by writing `clarifications.jsonl` directly
(L§3) — *"the prompt telling the agent to write `status: open` is not a control.
This is."* v2 widens the forgeable surface by putting `governance` and `audit`
on all eight types. Therefore: **a phase-boundary code guard strips and
re-stamps every model-authored `governance` and `audit` block**, on all eight
types, before any write reaches the corpus. Not a prompt instruction. Code.

> **Amended 2026-08-06.** That guard was built — `corpus/provenance.py`'s
> `restamp_model_authored` — and it had **zero callers**, ever (audit §10). So a
> reader of the paragraph above came away believing a boundary check ran, which is
> the same failure the paragraph itself is about: *"the prompt telling the agent to
> write `status: open` is not a control. This is."* An uncalled control is not one
> either.
>
> It is deleted, because the boundary it guarded does not exist in this tree and the
> control that does exist is stronger. `tools/graft_corpus_fields.py` is the only
> path that writes authored fields from a model-produced corpus, and it **refuses
> the whole `governance` field** rather than sanitising it — along with
> `reliability` (a softened decoy caveat is worse than none) and `summary` (the only
> indexed text, so copying it is a corpus swap wearing a field graft). A refusal
> cannot be forged past; a re-stamp can be forgotten, and was.
>
> **What is owed.** The moment a curator writes assets from model output, it owes
> this guard, and it owes it as code at its own write boundary — not as a shared
> function that the next author has to remember to call. `tests/corpus/
> test_analyst_view.py::test_no_tool_can_write_governance_onto_an_asset` pins the
> refusal so that adding `governance` to the graftable set fails a test.

#### 1.6 The trust boundary — and why there is no corpus sanitization

**Superseded 2026-08-03.** Earlier drafts specified a default-deny sanitizer over
every prose field. It was built, and then deleted. The reasoning that produced it
was inherited rather than derived: v1's finding was that *only notes were
sanitized, so a column description was the cheaper poisoning vector*, and each
draft widened the coverage without ever asking whether the control belonged in this
layer at all.

**The boundary, stated once, because several other decisions depend on it:**

> **The corpus is trusted. The incoming question is not.**
>
> Corpus content is authored by this team's data engineers — directly, or by a
> curator whose output they review before it is pinned. Internal artifacts are not
> treated as an attack surface. Injection is checked **once**, at the analyst's
> input, by ADR 0006's `guard`, and a poisoned question is **rejected** rather than
> edited. Its blast radius is that one conversation; it cannot alter the corpus,
> the index, or another caller's turn.

Three consequences, each replacing a piece of what the sanitizer was doing:

| what the sanitizer was for | where it actually belongs |
|---|---|
| prose that reads like an instruction to the model | **nowhere.** Governance is topology (ADR 0002): a fully persuaded analyst still reaches the database only through `check()`, so the worst case is a wrong answer, not an exfiltration. A bounded phrase list cannot beat a paraphrase anyway |
| PII or secrets in corpus text | the routing index's exclusion of governance-excluded columns (ADR 0006 B10), and `check()`'s COLUMNS layer at execution time. **Not** the durable sink: the record schema has no redaction column, and ADR 0006 §11 says the log is verbatim by design — a local-first tool writing the user's own transcript to the user's own disk |
| a newline escaping a field's indentation and opening a top-level prompt section | **render time**, in `serve/context.py`, as **lossless escaping** — done where the prompt format is known and reversible, not as a lossy edit in the store |

**What is kept, with its reason corrected.** Identifier fields that become path
components or filenames — and `physical_name` on tables/columns — are validated
against `\A[A-Za-z0-9_]+\Z` (ADR 0006 §9). That is **not** an anti-poisoning
measure. It is accident prevention on values that name directories or that a
trusted writer might mistype. **v2 has no HTTP corpus write** (`POST /corpus/edit`
is not a deliverable): corpus writes go through `CorpusStore` / CLI / the curator
after review. (The `CorpusStore` port was deleted 2026-08-25 as a seam nothing typed
against; the write path it named is `corpus/store.py::write`, unchanged. ADR 0006 B8
carries the amendment.) A validator that refuses is cheap and cannot silently change
meaning; editing an identifier would produce a name the database does not have.

**The specific defect the sanitizer introduced**, recorded because it is the shape
this project keeps meeting: sanitization ran on `load`, so it altered what reached
the model while `corpus_content_hash` — computed over the files on disk — did not
move, and the phrase list was not a knob. **Editing that list would have changed
every arm's delivered context while two runs continued to compare as the same
treatment.** An identity that fails to identify, which is L-R2 and the
`corpus_content_hash == "unknown"` defect in a new costume.

**If the trust boundary ever changes** — a corpus fed by an external source, a
tenant-authored corpus, an unreviewed automated writer — this section is void and
the sanitizer question reopens. That is the trigger to watch for, and it is why the
assumption is written here rather than left implicit.

#### 1.7 What the seed must produce

The seed writes a **deterministic, non-empty `summary` for every asset**, so no
asset is un-indexable and implementation steps 6–9 are measurable without a
single curator run.

```
schema: "beer_factory — 9 tables: betriebsstandorte, geoposition, kunden, …"
table:  "betriebsstandorte (7 columns: betrieb_id, bezeichnung, strassenadresse, …)"
column: "betriebsstandorte.bezeichnung (text)"
join:   "betriebsstandorte ⋈ geoposition on betrieb_id = standort_id"
```

**A `SchemaAsset` template is required, not optional.** Without one the `schema`
facet contributes zero and the model-free measurement in steps 6–9 measures a
four-facet system that never ships.

Column names fill a table's summary up to the 250-char budget and stop —
**column searchability is guaranteed by each column's own entry**, so
truncating the list loses nothing.

The curator's task becomes **rewriting**, which makes completion measurable:
"how many summaries are still verbatim seed output" is a direct query, and the
six zero-write schemas would have surfaced immediately. This structurally
defeats v1's `write_total: 0` reported over a half-authored corpus.

**The seed must not author `reliability`.** "BIRD never queried this column" is
not evidence a column is unreliable, and where gold SQL was defective v1's mask
**banned columns the generator needed**. Any deterministic backstop must be
evidence-based (a probe, an SME verdict), never absence-based.

**`sample_values` must be sampled deterministically.** Postgres defaults
`synchronize_seqscans` ON, so an unordered `LIMIT n` returns different rows
depending on concurrent activity — v1 observed the same column profiled as
`2018/8/5` and `2018/8/1` in two runs. Since sample values render into context,
non-deterministic sampling makes `context_hash` differ between arms whose
corpora are **byte-identical**, turning the L-R2 delivery gate into a rubber
stamp. Set `synchronize_seqscans = off`, add an `ORDER BY`, and profile once per
schema shared across arms.

---

### 2. Retrieval

#### 2.1 Terminology

```
facet      A parallel retrieval branch: one query-construction strategy,
           one set of target asset types, one channel configuration.
lexical    BM25 with saturating normalisation. Strong on rare proper nouns.
semantic   Embedding cosine. Range-bounded. Strong on paraphrase.
hybrid     Weighted sum of the two. The ranking key.
resolve    Reference closure over hit assets. Deterministic, parameterless.
connect    Steiner connectivity over the join graph. Parameterised; introduces
           assets nothing hit; can pick the wrong path.
route      Aggregate hybrid by schema, select top-N, re-retrieve within.
```

**`RVGD` is retired** — it encoded two dimensions in four letters (R and V are
channels, G is a post-retrieval step, D is a facet).

#### 2.2 The unified index

**One index. One entry per asset. Each entry is that asset's `summary`, ≤250
chars.** Sized at decision time from the v1 corpus: 57 schema + 656 table + 5,947 column +
875 join + 1,050 metric + 523 term + 582 few_shot ≈ **9,690 entries**.

> **Measured on the shipped corpus** (`../BIRD-corpus` at `30872d3b`, 57 schemas):
> **13,304 entries** — 57 schema, 656 table, 5,947 column, 706 join, 478 metric, 603 term,
> 4,857 few_shot, 0 negative_example. The design estimate and this are not the same corpus
> and do not compare; the rebuild of 2026-08-09 is the treatment identity every current
> number is measured against. The two counts that moved most are `few_shot` (582 → 4,857)
> and `metric` (1,050 → 478), so any argument below that turns on a *ratio* — §2.5's "the
> `term` facet is 523 of 9,690 entries" is the one — should be re-derived before it is
> leaned on.

What this buys:

**(a) Comparable document length.** Every entry within a small constant factor
of every other, so BM25's length normalisation and the embedding's information
density degrade the same way corpus-wide. This is the precondition for fusion
being worth doing at all.

**(b) Columns become independent retrieval units.** On obfuscated schemas this
is the difference between finding a table and not.

**(c) No second index.** v1 embedded the same text twice (per-asset for
retrieval, concatenated-per-schema for routing). Every summary is embedded
exactly once, and **IDF is global** — one document-frequency table, valid for
every query.

**The index is built from an explicit schema manifest**, never from directory
contents. v1's shared corpus root was a cross-run contamination channel: a
schema dropped from one attempt left its YAML behind and competed as a router
candidate for **every other schema's questions**, silently changing the routing
problem's difficulty between two runs of the same set (L§5). Build fails on
mismatch between manifest and tree.

**Index-time schema tags.** Every entry carries the schema it votes for in
`route`. Derivation is not uniform and is a declared table:

| asset | tag |
|---|---|
| `SchemaAsset` | itself |
| `TableAsset`, `FewShotAsset` | its `schema` |
| `ColumnAsset` | parent table's schema |
| `MetricAsset` | `base_table`'s schema |
| `TermAsset` | `binding` target's schema; **unbound → untagged** |
| `JoinAsset` | `left_table`'s schema; a cross-schema join votes **once** |
| `NegativeExampleAsset` | its own `schema`, or untagged if system-wide |

Computed at build, not query time — this is what lets `route` precede
`resolve`.

**Untagged ≠ unreachable.** An untagged asset does not vote in `route`, but it
**is** carried forward into pass two unconditionally (§2.5) and is subject to
budgets like anything else. Without that rule an unbound term could hit in pass
one and be silently deleted.

**Index build is a process-level, content-keyed, lock-free-on-the-network
operation.** v1's per-caller embedding took down a run — 24 workers × ~118k
tokens ≈ 2.8M against a 1M-per-minute account limit, killing that run and a
co-running one. Also required (all L§2):

- cache keys include **model + dimensions** — `cosine` returns 0.0 on a width
  mismatch instead of raising, so a cross-model hit silently degrades routing to
  "nothing scores"
- **content-keyed, never id-keyed** across corpus variants — curation rewrites
  summaries in place under the same id, so an id key would score the curated arm
  against the baseline arm's vectors
- vectors are shared immutable objects (v1 held ~1.7 GB of redundant copies).
  **Satisfied since 2026-08-04 by a different mechanism, and the requirement should be
  read as the property rather than the implementation.** They are not Python objects at
  all now: `retrieve/vectors.py` holds them in a LanceDB column, so the index and the
  cache share storage rather than sharing references. The file-backed cache that
  preceded it satisfied this clause literally — one `list[float]` object referenced from
  two dicts — and still cost **21.7 s to parse and 1,685 MB resident at every server
  start**, because "not copied" and "not expensive" are different claims
- the analyst view is computed **once per corpus** (v1's per-question deep copy
  was 55% of non-model CPU and, being GIL-bound, capped the concurrency knob
  itself)
- **never embed a blank document** — guaranteed upstream by §1.1's non-empty
  rule, asserted again here
- **the question is embedded once per turn** and the vector passed down, not
  re-derived per consumer (v1 embedded it twice; v2 has more consumers)

#### 2.3 Facets

| facet | queries from | target types | lexical | semantic | model |
|---|---|---|---|---|---|
| `schema` | raw question | `SchemaAsset` | ✓ | ✓ | no |
| `term` | LLM extraction | `TermAsset` | ✓ | ✓ | yes |
| `metric` | LLM extraction | `MetricAsset` | ✓ | ✓ | yes |
| `entity` | LLM extraction | `ColumnAsset`, `TableAsset`, `JoinAsset` | ✓ | ✓ | yes |
| `example` | raw question | `FewShotAsset` | ✗ | ✓ | no |

> **Amended by [ADR 0011](0011-two-model-split-and-facet-query-rewriting.md).** The `model`
> column now reads **yes for four facets, no for `schema`** — not three and two.
> `register/facets.py::FACET_EXTRACTS` holds `term`, `metric`, `entity` and `example`;
> `facet_schema` is deliberately absent because rewriting bought nothing measurable there,
> and its prompt stays in `PROMPT_REGISTRY` as an unsent baseline. So `example` no longer
> queries from the raw question. The channel columns are unchanged
> (`register/facets.py::FACET_CHANNELS` matches this table exactly), and so is the reason
> below.

`example` skips `lexical` because term-frequency matching between two
natural-language questions rewards shared function words.

`negative` is **not** a facet — it is a pre-fan-out gate (§2.7).

`column`, `table` and `join` are one facet because in a real question they
arrive together ("which customers have the highest order amount" is a customer
table, an order table, an amount column and the join between them, as one
thought). Splitting produces three highly overlapping extraction calls.

**A phrase list would be one query each.** `["customer", "order", "amount"]` would run three
retrievals: concatenation looks for one asset containing all three words
(usually nonexistent), separate queries find the customer table, the order table
and the amount column. What ships instead is one rewritten string per facet
(ADR 0011 §7), so `queries` holds exactly one element and there is **no
`max_queries_per_facet` bound** — a knob bounding a fan-out the code does not have
is a comparability key describing nothing, and it was deleted rather than wired
(`tests/serve/test_comparability_knobs.py`). If extraction ever returns a list,
the bound comes back with it: extraction is model-controlled and an unbounded
phrase list is an unbounded network fan-out (v1's analogue: the 40-pair slice that
"was never a size bound either").

**Extraction is a better query, not a replacement for retrieval.** Phrases go
into the same hybrid index, never into a lookup — if the model extracts
"customer churn rate" and the corpus says "churn", exact matching fails. That is
the failure mode of the reference book's ILIKE-only term channel.

**Degradation is per channel, and three-valued.** `FacetResult` records each
channel's `ChannelState` — `ran` / `not_configured` / `failed` — never inferred
from scores. Only `failed` is degradation.

A boolean cannot carry this, and the reason is concrete: **`example` has no
`lexical` channel by design**, so `lexical_ran=False` there is *correct*, while
the same `False` on `entity` means the BM25 index died and that arm is now
running on one channel. Under a boolean the gate
`facet_degradation_rate == 0` either fails on every run or acquires a special
case exempting `example` — and that special case is where the next silent
degradation hides.

`not_configured` is asserted against a **declared** channel table
(`FACET_CHANNELS`), never taken on the producer's word. That closes a second
hole: a channel that silently stops being configured reports `not_configured`
and would otherwise be excused by the very gate meant to catch it.

**v1's incident was exactly this class and there was no field for it at all** —
schema-pick accuracy of 69.9% published under a rate-limited embedder, <!-- [retired] -->
re-measured at 91.0% with quota free.

**This is a quotability input, not a diagnostic.** A run where extraction failed
on every turn completes normally, grades normally, and *is* v1's single-pass
retrieval wearing v2's name. §4.1 makes a non-zero degradation rate refuse the
comparison.

#### 2.4 Scoring and fusion

```python
class Hit(TypedDict):
    asset_id: str
    asset_type: str
    facet: str                      # which facet produced it
    queries: list[str]              # every query of that facet that hit it
    lexical: float | None           # None = channel not run for this facet
    semantic: float | None          # None = channel not run
    score: float                    # hybrid — the ranking key
```

**Both channels score every candidate.** The two channels retrieve a candidate
set each; their **union** is scored, and the channel that did not retrieve an
asset **backfills its true score for it** — the cosine is a dot product already
held, and BM25 for a known document is cheap.

**This forbids a top-k vector query, and the prohibition is easy to violate by
accident.** `retrieve/vectors.py::search` takes `limit(len(candidates))` and builds no
vector index, so every candidate is scored and the backfill is free. `limit(k)` for any
smaller k would return a plausible ranking in which the unscored tail reads as absent —
the same defect as draft 2's `lexical: 0.0`, arriving through the storage layer instead
of the fusion step. LanceDB's `limit` is mandatory and defaults to **10**, so "forgot to
set it" and "set it to the candidate count" are one line apart.

This is not an optimisation, it is a correctness requirement. In draft 2,
`lexical: 0.0` meant "did not retrieve", so an asset ranked #1 lexically and
#51 semantically scored `0.5 × 1.0 + 0.5 × 0.0 = 0.5` while a mediocre
both-channels asset scored 0.6 — **the ranking key was dominated by candidate
truncation rather than relevance.** It also made Open Question 5 unanswerable
by the very field added to answer it.

`None` is reserved for **channel not run at all** for that facet (`example` has
no lexical) or channel degraded (§2.3). It is never a score.

| channel | normalisation | why |
|---|---|---|
| `semantic` | cosine as-is | bounded, comparable across queries |
| `lexical` | `s / (s + k)` saturating | BM25 has no upper bound. Min-max makes every query produce a 1.0, meaningless across queries; the saturating form is **absolute**, so a threshold survives |

```
active   = channels that ran for this facet
hybrid   = Σ w_c · score_c  over active,  ÷  Σ w_c  over active
```

**Renormalising by active weight is required, not cosmetic.** Without it the
`example` facet — single-channel by design — would max out at 0.5 while every
other facet maxes at 1.0, so few-shot evidence would vote at half strength by
accident. Defaults `w_lexical = w_semantic = 0.5` (`register/knobs.py`), so a two-channel
facet is unchanged and a single-channel facet is scaled to the same range.

**RRF is not used.** It encodes rank only, discarding similarity, and `negative`
needs an absolute threshold.

**Dedup.** One asset may be hit by several queries of the same facet, and by
several facets. The rule, applied before `route`:

```
within a facet:  merge to one Hit per asset.
                 score  = max over queries
                 queries = union of the queries that hit it
                 lexical/semantic = the components of the max-scoring query
across facets:   NOT merged — a Hit stays attributed to its facet,
                 because route counts facets, not assets.
```

Keeping the components from the max-scoring query (rather than maxing each
component independently) avoids a chimera whose parts came from different
queries.

#### 2.5 Two-pass retrieval and budgets

```
[facet ×5]   pass one: global, top-50 per query WITHIN the facet's target types
     ▼
[route]      ① aggregate by schema tag → select top-N (§2.6)
             ② pass two: re-run the same queries, restricted to the selected
                schemas, with GLOBAL IDF
             ③ carry forward untagged pass-one hits unconditionally
             ④ dedup (§2.4), then per-type budgets
     ▼
[resolve] → [connect] → [assemble]
```

**"top-50 per query" means within the facet's target types.** A global top-50
then filtered would give the `term` facet (523 of 9,690 entries at design time; 603 of
13,304 on the shipped corpus — §2.2) an empty result on most queries. Type scoping is a
filter applied to one index, not a second index: the postings are shared, **IDF stays
global**.

**Pass two uses global IDF.** Draft 2 proposed recomputing IDF over the selected
schemas, on the reasoning that discriminating 73 sibling tables needs
within-schema statistics. That was withdrawn: it is a per-question BM25
statistics rebuild (v1 measured per-question index rebuilds at 25 min / 97% CPU
offline), it is a second index by another name, and — decisively — **it would
make `lexical` depend on which schemas this question selected**, destroying the
absoluteness that §2.4's saturating normalisation exists to provide, along with
the cross-question comparability of every recorded `Hit.lexical`. Within-schema
IDF becomes Open Question 6: a measurable hypothesis, not a design commitment.

**What pass two is actually for, now that IDF is out of it: retrieval depth
inside the selected schemas.** Pass one spends its top-50 per query across all
57 schemas, so a schema that ends up selected may have contributed only two or
three of those slots. Re-running the same queries against three schemas spends
the whole depth there. This must be stated, because an implementer could
reasonably build pass two as a *filter over pass one* — satisfying every other
sentence in this section — and lose the entire benefit.

The two properties below are consequences of **ordering `route` before budgets**,
not of re-running the queries; they hold either way and are listed because they
are what the ordering buys:

**① `route` sees complete evidence.** Candidates are global top-50 per query,
not a budget-truncated residue. Had budgets applied first, `route` would
aggregate over a few dozen survivors of 9,690 and a schema whose evidence is
diffuse could never win.

**② Budgets have an unambiguous position:** after pass two, **per asset type**
(not per facet — `entity` targets three), on the deduped set.

| type | budget |
|---|---|
| schema | **all selected** — never budgeted; `SchemaAsset` + its `rules` render for every selected schema |
| table | 8 |
| column | 30 |
| join | 5 |
| metric | 5 |
| term | 5 |
| few_shot | 3 |
| negative | **n/a** — consumed by the gate, never by context |

**All eight types appear.** v1's `budgets.get(cls, 0)` silently dropped
unbudgeted types, which is why `NegativeExampleAsset` was structurally
unreachable — reproducing that mechanism while citing it as the reason for
having budgets would be absurd. The budget map is keyed off the same declared
asset-type register as the index and the tag table, and a test asserts every
declared type has an entry.

Assets pulled in by `resolve`/`connect` do **not** consume budget, but they are
subject to the render rules in §3.6.

#### 2.6 `route`

```
score(schema) = Σ over facets  max( hits of that facet tagged with this schema )
```

**Each facet casts one vote per schema, worth its strongest hit there.**

| property | |
|---|---|
| Size bias | **sharply reduced, not eliminated.** A max over 703 column entries is stochastically larger than a max over 42 for the same underlying relevance; max-pooling reduces the N-dependence, it does not remove it |
| Multi-evidence agreement | **rewarded** — a schema hit by `term` and `entity` sums both |
| Single-facet volume | **worthless** — 30 hits and 1 hit in one facet contribute identically |
| Range | facet count × 1.0, comparable across schemas (given §2.4's renormalisation) |

"Several different angles point here" is evidence; "there is a lot here" is not.
An independent v1 probe reached the same correction from the other direction
(max-pooling over per-table vectors instead of mean-by-concatenation).

**Edge behaviour, all defined:**

```
max over an empty hit set        = 0.0 (the facet contributes nothing, is not dropped)
schemas eligible                 = those with score > 0
selection                        = top-N of eligible, ties broken by schema name asc
fewer than N eligible            = return fewer; never pad
zero eligible                    = route to [decline] with reason "no_schema_matched"
```

**`route` and `connect` both have an edge to `[decline]`.** Draft 2 made the two
terminal-refusal nodes reachable only from `guard` and `negative_gate`, so "no
schema matched" and "required tables are not connected" had nowhere to go.
Determinism here is a prerequisite for `context_hash` and the byte-golden.

Facet weights are all **1.0**. `SchemaAsset`'s vote arguably deserves more — it
is a direct statement of what the database is for — but no data supports a
multiplier. **Gate on step 8, not "calibration target":** route recall@3 across
57 schemas, **grouped by schema-size decile**. If large schemas still win
systematically, the formula is still wrong. Note the check is itself
underpowered and clustered (~6 schemas per decile) — read it as a screen, not a
proof.

#### 2.7 `negative_gate` — a decision, not a ranking

Every facet is a ranking: 8th vs 9th affects whether an asset enters context,
not correctness. `negative` is a judgement: a hit means refusal.

A rank-based rule fails degenerately — with few negative examples one is always
ranked first, so "top-1 ⇒ refuse" refuses everything.

```
refuse iff  semantic(question, negative.summary) ≥ τ
            and (negative.schema is None or negative.schema in candidate schemas)
```

τ is on `semantic`, not `hybrid`, not rank. The schema clause exists because a
lake-wide gate would let one schema's "we cannot answer attrition questions"
refuse attrition questions aimed at the other 56. Since the gate runs before
`route`, "candidate schemas" is the set with any pass-one hit; a system-wide
negative example (`schema is None`) always applies.

**The verdict is total**, because `Hit | None` cannot express fail-open:

```python
class NegativeVerdict(TypedDict):
    outcome: Literal["hit", "clear", "disabled", "error_failed_open"]
    tau: float | None
    top_score: float | None
    matched_id: str | None
```

`error_failed_open` is counted at run level and is a quotability input.

**τ cannot be calibrated on BIRD**, whose questions are all answerable by
construction. Until a negative corpus exists, **`negative_gate` ships
disabled** (`outcome="disabled"`, written on every turn, never absent) — an
uncalibrated refusal gate is worse than none.

> This retires ADR 0002's safety-spine invariant 1 ("refuse-gate runs before the
> agent"). ADR 0006 records the amendment.

#### 2.8 `resolve` — reference closure

Complete every hit's references until fixpoint. Deterministic, no parameters,
**introduces only assets reachable by reference closure from a hit**.

| trigger | pulls in |
|---|---|
| `ColumnAsset` hit | its `TableAsset` |
| `TableAsset` in the set | all of its columns |
| `TermAsset` hit | its `binding` target |
| `MetricAsset` hit | its `base_table` |
| `FewShotAsset` hit | the tables its SQL references |
| `JoinAsset` hit | both endpoint tables |
| **two tables in the set** | **every `JoinAsset` whose both endpoints are in the set** — *but see 2.8.1: this row does not run here* |

**The last row is load-bearing and was missing from draft 2.** In v1 joins
reached context through a render-time rule — every `JoinAsset` with both
endpoints licensed — which is where the median injection of 13 comes from. Draft
2 replaced it with entity-facet ranking capped at 5 and only closed
`join → endpoints`, never the reverse, so **a four-table question could reach
the model with none of its join keys.** These joins are `pulled_in` and exempt
from the join budget.

The distinction from `connect` is **not** "introduces nothing new" — it clearly
does. It is that **closure is a total function of the hit set**, while Steiner
is a *choice among paths*.

`FewShotAsset` closure needs a SQL parser (sqlglot, datasource dialect). A parse
failure resolves nothing for that few-shot and is **recorded**, never silently
dropped.

##### 2.8.1 Join completion runs *after* `connect`, not inside `resolve`

*Amendment, 2026-08-03. The rule above is unchanged; only its position moves.*

Two reasons, and the first is mechanical.

**`resolve`'s closure cannot express it.** `resolve(ids, references)` is a fixpoint
over `Mapping[id, set[id]]`, so every edge is **disjunctive**: any one id present
pulls in all of its references. The last row of the table above is
**conjunctive** — *both* endpoints present pulls in the join. Encoding it as
`table → joins touching it` would pull a join in from **one** endpoint, and that
join would then pull in its other endpoint: FK-neighbourhood expansion by one hop
from every hit table. §2.9 sets `expand_hops = 0` and records that v1's default of
1 was wrong, so the naive encoding silently switches on the thing §2.9 switched
off. That is not a refactor away — the closure is the wrong shape for a
conjunctive rule, and giving it a second parameter to carry hyperedges would make
a total function parameterised, which is the one property §2.8 has that §2.9 does
not.

**Placing it before `connect` provably misses the keys it exists to supply.**
`connect` adds Steiner points, and a Steiner point's whole purpose is to sit on a
join path — so the table pairs that most need their `on` clause in the prompt are
exactly the ones created *after* `resolve` has run. Completing joins before
`connect` reproduces the draft-2 failure the row was written against, one step
later in the pipeline: a multi-hop question reaching the model with none of the
keys for the hops `connect` chose.

So: **endpoint closure (`join → its two tables`) stays in `resolve`**, because it
expands the terminal set that `connect` then has to connect. **Join completion
(`both tables → the join`) runs once after `connect`, over the final `licensed`
set.** It remains total, idempotent, and a function of a set — it just runs over
a later set. Joins added this way are `pulled_in` and exempt from the join budget,
exactly as above.

#### 2.8.2 The corpus structure projection

*Added 2026-08-03, because none of the above was reachable.*

> **Amended by [ADR 0008](0008-identifiers-end-to-end.md), pointer added 2026-08-22.** 0008's
> header declares that it amends this section and §1.2, and until now nothing here said so.
> Two parts:
>
> **D1 — a key is not a name.** Ids fold the physical name through
> `corpus/identity.py::slug`; `table_id` and `derive_column_id` are the single spellings. That
> shipped, and the paragraph on `table_id` below already cites it.
>
> **D4 — the "physical name, bare or qualified" reference kind is withdrawn.** 0008 D4 names
> the paragraph below by that phrase, calls it "a third kind" of identifier-bearing field,
> records that it cost 25% of the corpus's joins, and rules that normalisation happens once in
> the loader with `structure.py` doing exact lookups only. **That withdrawal is decided and not
> built.** What shipped is the loader half: 0008's own header reports the reference *fields*
> normalised in `../BIRD-corpus` (all 5,947 `parent_table`s, all 1,412 join endpoints, all 478
> metric `base_table`s, 602 of 603 term bindings are asset ids). What did not ship is the
> tolerance's removal: `retrieve/structure.py::table_lookup` still documents and builds **four
> spellings per table** — the asset id, `table_id(schema, physical_name)`, the bare
> `physical_name`, and the engine spelling `{schema}.{physical_name}` — and `bind_endpoint`
> resolves against any of them. So the section below still describes live behaviour; it just no
> longer describes the decision.

`resolve` and `connect` are both total functions of data neither of them has.
On 2026-08-03 `serve/state.py` declared five inputs for them — `join_edges`, `references`,
`asset_types`, `table_schemas`, `schema_tags` — under the comment *"F1 test /
wiring hooks (optional)"*, and **all five were read and none was ever written**, by
`src/`, by `tests/`, or by the eval harness. So `connect` ran on an empty edge
set on every turn that had ever executed, and declined `missing_join_path`
whenever a turn licensed more than one table. Single-table turns answered, which is
why a green suite and a live eval both missed it. **The five keys are gone from
`serve/state.py`; what replaced them is below and in §2.8.2.2.**

These five are not five hooks. They are **one projection of the asset set**, they
are pure functions of it, and they hold no per-turn information. So:

- **One module builds all five**, beside the index and at the same time. §2.2
  already establishes that precedent for schema tags — *"computed at build, not
  query time — this is what lets `route` precede `resolve`"* — and the argument is
  the same one: recomputing per turn is not merely waste, it is a place where two
  turns can disagree about the shape of the corpus.
- **It is carried on `configurable` next to `index`**, and `connect_node` gains a
  `config` parameter. It had none when this was written (`wrap.py` forwards
  `RunnableConfig` only to nodes that declare it), which is *why* the hook shape was
  reached for. Both shipped: `Session.configurable()` writes `structure`, and
  `route_node` / `resolve_node` / `connect_node` all take `config`.
- **It returns `(structure, problems)`**, per the loader's rule: a corpus that
  lost half its edges must not be indistinguishable from a corpus that is small.

**Node identity is the whole difficulty, and guessing is prohibited.** `connect`'s
nodes must be the identifiers in `licensed` — asset ids, `{schema}.{physical}`.
`JoinAsset` carries `left_table` / `right_table` as **physical names, bare or
qualified**, and `corpus/validate.py` explicitly declines to settle which
(`_bare()` accepts both). Reconciling them is therefore a lookup, and it has three
outcomes:

| the endpoint resolves to | then |
|---|---|
| exactly one table asset | bind the edge |
| **more than one** | **drop the edge and record a problem** |
| none | drop the edge and record a problem |

The ambiguous case is routine, not hypothetical: one physical name in two schemas
is the normal shape of a pooled lake, and it is the case where a guess is not a
lost edge but a **licensing leak** — a Steiner point in the wrong schema, licensed,
and `crossings` accounted against the wrong pair. Left-most or first-match
resolution fails *open*. Dropping fails closed but silently, hence the recorded
problem: an unresolvable endpoint is a curation defect, and it must surface where
the corpus is built rather than as a decline three layers away.

**`table_id` must become a declared function.** `derive_column_id` and `join_id`
were singletons in `corpus/identity.py` while the table id was a bare f-string in
`corpus/seed.py`. The reconciliation above depends on that convention, so a second
hand-written copy of it is the two-`LOW_CONFIDENCE_JOIN`-constants defect in the
one place that would silently mis-license a table. **Done:**
`corpus/identity.py::table_id` is now the single spelling, shared by the seed, join
reconciliation and licensed lookups, and it folds the physical name through
`corpus/identity.py::slug` (ADR 0008 D1) rather than interpolating it raw.

##### 2.8.2.1 Three rules the build had to settle, added after implementing it

*2026-08-03. None of these was specified above; all three are now behaviour.*

**A bare reference from an asset that declares a schema resolves inside that schema
first.** `ColumnAsset.parent_table` and a `FewShotAsset`'s SQL carry bare names, but
those assets have a `schema` field — so `sales_a` + `customers` is a *fully
qualified statement the corpus made*, and resolving it globally would report an
ambiguity its author did not have. In a pooled lake that is one false ambiguity per
column of every repeated table name, which would bury the join problems that
matter. `JoinAsset` has no `schema` field, deliberately (§1.2), so it gets no scope
and its bare endpoints are genuinely ambiguous — the asymmetry is a consequence of
§1.2, not an exception to it.

**Completed joins go in `pulled_in`, not `licensed`.** §2.8 and §2.8.1 say a
completed join reaches context; neither says through which field. It cannot be
`licensed`: that is governance's table allowlist, every entry is normalised as a
table key, and a join id there is a table key naming no table. `pulled_in` is
already what the render and the delivery record read.

**Self-joins are excluded from the edge set and kept in the join index.** A loop
makes a terminal look adjacent to itself, and `connect` decides disconnection by
asking whether a terminal appears in the adjacency map — so a self-join would hide
an isolated table behind a false edge and turn a refusal into a wrong path. Its ON
clause is still needed in the prompt, so it stays indexed for completion.

##### 2.8.2.2 Resolved: the session is where the corpus enters the process

*Opened 2026-08-03 as the text below; resolved the same day. The original statement of
the gap is kept because it is the evidence for the seam chosen here.*

**The seam is run-constant versus per-turn, and that is the distinction the five hooks got
wrong.** `index`, `structure`, `assets_by_id`, the analyst corpus, the connector, the
policy, the model, the resolved knobs, `corpus_content_hash` and `prompt_set_hash` are all
constant for every turn of a run. Putting any of them in per-turn state — which is what
`state.py`'s "optional wiring hooks" did — creates a place where two turns of one run can
disagree, and every retired number in this project came from exactly that.

So one object holds the run constants, and it is built once:

```
Session          index, structure, assets_by_id, corpus, connector,
(frozen)         policy, agent_model, knobs_resolved, corpus_content_hash,
                 prompt_set_hash, problems
   .configurable()   -> the mapping the graph's nodes read
   .turn(question)   -> a turn dict with every required record field present
```

Small interface, and everything a caller currently assembles by hand disappears behind it.
The two methods are the only ways in, so a node cannot be handed a half-wired config.

**Index entries and structure are built from one resolution, not two.** An index entry's
`schema_tag` for a `JoinAsset` is *`left_table`'s schema* (§2.2), and `left_table` is a
physical name that must be reconciled to a table asset — the same lookup, with the same
three outcomes, that §2.8.2 specifies for edge endpoints. Two independent resolutions would
let a join's `schema_tag` disagree with its edge's endpoints, and nothing would raise: the
join would vote for one schema while connecting two tables in another. So the builder
resolves once and returns both, and `retrieve/`'s asset→`IndexEntry` mapping stops living
only in `tests/`.

**Two corpus sources, because one adapter is a hypothetical seam.**

| source | for |
|---|---|
| `store.load(root)` | the curated corpus — YAML on disk |
| `seed(connector.introspect(schema), schema)` | a live schema, no curation |

`Session` takes assets and does not know which it got. That is what keeps its interface
small, and having both from the start is what makes the seam real rather than declared.

**`problems` finally have a caller who can fail loudly.** Both sources return them, the
builder returns them, and `Session` carries them. **The entry point refuses to serve when
any problem is fatal, and prints all of them either way** — this is the requirement §2.8.2
stated and could not satisfy, because until now nothing downstream of a corpus load was in
a position to exit non-zero.

**The entry point's job is to make the record checkable, not to be a server.**
`python -m governed_bi.serve` serves one question and prints the answer and the record.
It **exits non-zero when `missing_required(record)` is non-empty.** That is the whole
point of it: 346 green tests and nine fixed defects are still evidence from contract tests
and a scripted model, and one real turn through a real model against a real database is a
different kind of evidence. A skeleton that printed an answer and said nothing about the
record would produce the reassurance without the evidence.

**`llm_model` becomes a knob here.** `knobs.py` declares `llm_temperature`,
`llm_reasoning_effort` and `embedding_model` as `Role.comparability` and its own docstring
condemns model identity going unrecorded — yet the chat model is not a knob, so two runs on
different models compare as one experiment. The session is where the model is chosen, so it
is where the knob is resolved. This is also what unblocks the `usage` gate condition in
decision #45(a).

> **Names corrected 2026-08-22.** Two of the three knobs this paragraph cites as precedent are
> right and one never existed. `llm_reasoning_effort` and `embedding_model` are in
> `KNOB_REGISTER` as `Role.comparability`; **`llm_temperature` is not, and there is no
> temperature knob of any name.** The decision itself shipped, under a different name: the chat
> model is `chat_model` (`register/knobs.py`), joined since by `facet_model`, `rewrite_model`
> and `llm_utility_model`. There is no `llm_model`.

##### 2.8.2.2.1 The original statement of the gap

The declared wiring above says the projection is built beside the index and passed
on `configurable`. **That key has no in-repo writer, because `index` has none
either** — the only asset→`IndexEntry` mapping in the repository is in
`tests/serve/`, which parcel F's contract already records. So `serve/` derives the
projection from the assets on `configurable` when the declared key is absent, and
that path is the one every in-repo caller takes today.

Two costs, both real:

- **The fallback's `problems` have no reader.** An unresolvable join endpoint is
  recorded and then discarded, which contradicts this section's own requirement
  that it surface where the corpus is built.
- **The fallback memoises on the identity of the asset container.** It holds the
  container beside the value so a recycled `id()` cannot return another corpus's
  projection, but a container **mutated in place** between turns is invisible to
  it. Narrow — the eval driver builds fresh per run — and not worth paying a
  content hash for, because the correct fix is upstream.

The fix is a `src/`-side builder that takes an asset set and returns index and
structure together, which is where `problems` would finally have a caller who can
fail loudly. That is a decision about where the corpus enters the process, not a
gap to close inside `retrieve/`, so it is recorded here rather than improvised.

#### 2.9 `connect` — Steiner connectivity

After `resolve`, selected tables may not be mutually reachable, and executable
SQL needs a connected join subgraph.

> Hit `customers` and `order_items`; no direct edge; the path runs through
> `orders`, which no facet hit. `orders` is a Steiner point.

**Restrict to the terminals' connected component before calling
`steiner_tree`** — networkx's mehlhorn default indexes shortest paths for every
node and raises `KeyError` when the graph holds nodes disconnected from the
terminals, which is routine (L§2). That check doubles as the "required tables
must be connected" refusal, which routes to `[decline]`.

**Bounded:** `max_steiner_points = 5`, `max_crossings = 2`. Exceeding either is
a refusal, not a silent expansion. Draft 2 left `connect` unbudgeted,
cross-schema, and steerable by the model through extracted phrases — an
unbounded context and licensing expansion in a pooled lake.

**`expand_hops` defaults to 0.** FK-neighbourhood expansion is speculative and
its value is directly measurable: *of the tables gold SQL uses, how many entered
neither by facet hit nor by Steiner path?* Measure before enabling. (v1's default
was 1, not 0.)

**Cross-schema: allowed, bounded, accounted.** Cross-schema joins are
executable and a hard boundary would make answerable questions unanswerable.
Every crossing emits a governance event, marks the out-of-schema table in the
render, and counts against `max_crossings`. `route` bounds *scoring*, not
containment — and that is now a stated property rather than an omission.

---

### 3. The serve graph

#### 3.1 Diagram

```
START
  │
  ▼
[guard]              deterministic rules (ADR 0006). No model. This turn's input only.
  │  └─ blocked ────────────────────────────────────────────→ [refuse] ──┐
  ▼                                                                      │
[rewrite]            only when the thread has prior turns. Small model.
  │
  ▼
[negative_gate]      semantic vs NegativeExampleAsset, τ. No model. Ships off.
  │  └─ hit ────────────────────────────────────────────────→ [decline] ─┤
  ▼                                                                      │
──── fan-out (one super-step, concurrent) ──────────────┐                │
  ├─ [facet_schema]   raw question    → lex + sem       │                │
  ├─ [facet_term]     LLM extract     → lex + sem       │                │
  ├─ [facet_metric]   LLM extract     → lex + sem       │                │
  ├─ [facet_entity]   LLM extract     → lex + sem       │                │
  └─ [facet_example]  raw question    → sem             │                │
──── fan-in (implicit barrier; per-facet channel) ──────┘                │
  ▼                                                                      │
[route]      aggregate → top-N → pass two → budgets                      │
  │  └─ zero eligible schemas ───────────────────────────────→ [decline] ─┤
  ▼                                                                      │
[resolve]    reference closure                                           │
  ▼                                                                      │
[connect]    Steiner, bounded                                            │
  │  └─ not connected / over caps ───────────────────────────→ [decline] ─┤
  ▼                                                                      │
[assemble]   render → USER message; system stays stable                  │
  ▼                                                                      │
[agent_core] ⇄ [tools]                                                   │
  ▼                                                                      │
[stamp] ◄────────────────────────────────────────────────────────────────┘
  ▼        every terminal path, including node exceptions
 END
```

> **Topology note, re-checked 2026-08-12.** *The diagram is left as decided.
> [`serve/graph.py`](../../src/governed_bi/serve/graph.py) is the authority for what
> runs; `docs/architecture.md` is the living version of this picture.* Six nodes are
> missing from the drawing above, and the first three are the ones that matter:
>
> ```
> [assemble] → [abstain] → [agent_core] ⇄ [tools] → [reflect] → [narrate] → [stamp]
> ```
>
> - **`abstain`** — [ADR 0013](0013-the-declared-abstention-policy.md)'s declared
>   abstention policy, added after this note's first version. It is the fourth node with
>   an edge to `[decline]`, alongside `guard`→`[refuse]`, `negative_gate`, `route` and
>   `connect`. It writes the decline through `path_kind` exactly as `route` and `connect`
>   do, so there is one answer to "did this turn end here" rather than a second channel
>   the edge would have to agree with. Off by default (`abstention_policy_enabled`
>   defaults `False`), in which case it writes a `disabled` verdict, no `path_kind`, and
>   the edge falls through to `agent_core` — which is exactly what the drawn
>   `assemble → agent_core` edge did before it existed. Registered `stream=False` and
>   with no timeout: it is a pure function of state with no model call.
> - **`reflect`** — a post-hoc observer asking whether the SQL this turn produced
>   answered the question. It **changes no control flow at all**: never `path_kind`,
>   never `terminal_reason`, never `answer`. Its edge to `narrate` is plain rather
>   than conditional precisely so that no edge can read its verdict. It ships
>   **disabled** (`reflect_enabled` defaults `False`, and no production path wires a
>   `reflect_model`), returning `{}` before reading anything but the knob; registered
>   with `stream=False` so a disabled observer adds no timeline rows. It calls a model
>   only when enabled.
> - **`narrate`** — writes `answer_text`, which is what the answer card reads.
>   **Usually calls no model:** the normal path adopts the agent's own prose, and the
>   model runs only on the remainder, where the loop ended with no text to adopt. A
>   no-op on `refuse` / `decline` / `crashed`, whose wording is system copy. It
>   swallows its own failures, because a narrator timeout must not mark an
>   already-answered turn `crashed`.
> - **`accept`** (optional, before `guard`) — present only when `build_graph` is
>   passed one, which is the client-facing path. That argument is also the trust
>   boundary: with it the graph is compiled with `ServeInput`/`ServeOutput` schemas.
> - **`fanout`** — the five facets hang off a real passthrough node, not the implicit
>   edge drawn above. It reports stage `facet_schema`, not `fanout`.
> - **`record`** (optional, after `stamp`) — the audit-log append, when `build_graph`
>   is passed one. `stamp → END` otherwise.
>
> Two consequences for §3.3 below, which lists neither new node: its node-contract
> table is missing `reflect` and `narrate` rows, and **its model-entry-point count is
> understated** — `reflect` (when enabled) and `narrate` (on the no-prose remainder)
> both call a model. Separately, §3.3's `guard` row reads "**no** — rules (ADR 0006)",
> but `guard` has a sixth, model-backed gate: the five deterministic rules run first
> and free, and a question that clears them is then asked *is this a BI question at
> all?* on the **utility** model (`serve/nodes/guard.py::_bi_scope`, gated by
> `policy.guard_rule_enabled(BI_SCOPE_RULE_ID)`; enabled-with-no-model records
> `error_failed_open`, never `clear`).

**Every terminal path funnels through `[stamp]`**, the only node that writes
`answer`, because §4.1's contract is one question in, one `Answer` out. A
refusal path that bypasses it hands eval `None` — the same class of defect as
counting a crash as a refusal.

**That alone does not cover crashes**, and draft 2 wrongly claimed it satisfied
the v1 lesson. The v1 defect was an *exception* in `assemble` producing no
answer, no refusal and no log row; LangGraph propagates node exceptions out of
the graph. So: **every node is wrapped, exceptions write
`failure: NodeFailure` and route to `[stamp]`**, which stamps
`Outcome.crashed` with the failing `Stage`. That is a §4.3 enum precondition.

**Why the gates precede fan-out.** They are cheap relative to what they skip:
`negative_gate` is one vector lookup (~10 ms), and `guard`'s five deterministic rules
are free. 10 ms to skip four rewrite calls is net positive on a hit, nearly free on a
miss. **`guard`'s sixth rule, `g_bi_scope`, does call a model** — one word on the
utility model, run only after the free rules clear — so the gate row is not free in
the way this paragraph's arithmetic assumes; it stays in front of fan-out because a
question that is not a BI question at all should not pay for five facets, and because
a refusal the model decides is still one call against four.

**Why `guard` precedes `rewrite`.** This turn's input is the only new input;
history was guarded in its own turn. **Known gap:** history contains the
system's own answers, which contain data read from the database, so indirect
injection bypasses `guard` and can be pulled into the rewritten question. Larger
than this layer; recorded, not solved. ADR 0006 owns the rule set.

**Clarification needs no extra machinery.** `ask_user` → `interrupt` →
`Command(resume=…)`. On resume LangGraph **restarts the interrupted node from the
beginning** (and the outer `agent_core` body re-runs); side effects before
`interrupt()` must be idempotent. Sibling tool `Send`s that already completed do
not re-run (probed on langgraph 1.2.10). The pause itself is never stamped —
`Outcome.clarification` is transport-level (`__interrupt__` / HTTP), not a
register path. **The resume must be bound to `identity`** on the REST path and
reject a mismatch — v1's process-global checkpointer let a guessable
`thread_id` land on another caller's paused clarification, which embeds their
question. The primary UI stream path bypasses that gate by design (ADR 0007 §6);
namespacing is a mitigation, not authentication.

> **The stream path no longer bypasses it, 2026-08-18.** "On the REST path" was the whole problem:
> the REST path is deleted ([ADR 0014](0014-one-conversation-store.md)), so a gate living there
> would have been the only gate and would have gone with it. The check is now
> `serve/resume.py::authorise_resume`, called from `ask_user` on the instruction `interrupt()`
> returns on — the first point in the process holding both the paused turn's checkpointed
> `identity` and the resuming run's caller — and it covers every resume, including
> `Command(resume=…)` applied by the platform. ADR 0006 §10 B9 and ADR 0007 §6 carry the detail.
> What is still true: with one principal it cannot tell two callers apart, and a guessed
> `thread_id` still lets a stranger *destroy* a pending question, because the platform consumes
> the resume before the task re-runs.

**LangGraph mechanics:**

- Fan-out is five static edges; `Send` is unnecessary for a fixed facet set.
- Fan-in is implicit — five nodes pointing at `route`.
- **Facet results need a reducer, and a bare `dict` is not one.** Five nodes write
  the same channel in one super-step, so without a reducer LangGraph raises
  `InvalidUpdateError` ("can receive only one value per step") — a plain
  `dict[str, FacetResult]` does not merge, it collides. But
  `Annotated[list, operator.add]` has the opposite bug: with a checkpointer, turn 2
  starts holding turn 1's five results and `route` aggregates both.

  The shape that has neither: `Annotated[dict[str, FacetResult], merge_facets]`
  where `merge_facets` replaces by key. Concurrent-safe within a super-step
  (five disjoint keys), and overwrite-per-turn across turns (turn 2 writes the same
  five keys).

  > **Read this as "accumulation must be chosen", not "`operator.add` is wrong."** `ServeState.turns`
  > is `Annotated[list[TurnEntry], operator.add]` on purpose
  > ([ADR 0014](0014-one-conversation-store.md) §2): a channel that *is* the conversation's history
  > wants exactly the behaviour this bullet calls a bug. What makes it safe is what `facets` lacked
  > — every row carries its own `turn_id` inside `record`, and `record_node` refuses to emit a row
  > without one, so a flat list is still attributable per turn. The same argument as the
  > `turn_index` on `usage` below. `tests/serve/test_state_channels.py` forces the choice to be
  > declared: `PER_TURN_RESET | ACCUMULATING | TURN_IDENTITY | TEST_HOOKS` must partition every
  > channel.

- **`usage` has the same multi-turn bug and needs the same care.**
  `Annotated[list[UsageRecord], operator.add]` accumulates across turns under a
  checkpointer, so the per-turn `usage` record and every cost number derived from
  it double-count from turn 2 onward. Either stamp each record with the turn index
  and filter at projection, or reset the channel at the head of each turn — but not
  "it is a list, `add` is obviously right", which is how this lands.
- Latency is `max(branches)`, cost is `sum(branches)`. Fan-out buys latency, not
  money. Extraction is classification — small model.
- A module LangGraph loads by file path must **not** use `from __future__ import
  annotations` (it inspects raw parameter annotations) and cannot use relative
  imports. `Path.resolve()`/`Path.cwd()` trip its ASGI blocking-call detector.

#### 3.2 State

**The listing below is a partial one and always was.** `serve/state.py::ServeState` is the
authority: it is declared `total=False` and carries forty-eight keys, six of which hold
decisions this section never mentions — `turns` ([ADR 0014](0014-one-conversation-store.md)),
`abstention` ([ADR 0013](0013-the-declared-abstention-policy.md)), `path_kind` /
`terminal_reason` (how a turn ended), `answer_text` (`narrate`'s prose, distinct from
`answer["text"]`) and `reflect_verdict`. Read the module before believing a field is absent.
*(Partial-listing note and the two type corrections inside the block added 2026-08-22.)*

```python
class ServeState(TypedDict, total=False):   # partial listing — see serve/state.py
    question: str
    thread_id: str
    identity: dict[str, Any]        # provenance, NOT enforcement (ADR 0006 §10)

    guard: GuardVerdict             # total; written on every turn
    rewrite: RewriteResult | None   # None = node did not run (first turn)
    negative: NegativeVerdict       # total; written on every turn

    facets: Annotated[dict[str, FacetResult], merge_facets]   # see below

    schemas: list[str]
    retrieved: RetrievalResult
    crossings: list[SchemaCrossing]
    licensed: list[str]             # table ids the turn may reach — see below

    delivery: Delivery              # what actually reached the model (§3.6)
    messages: Annotated[list, add_messages]
    usage: Annotated[list[UsageRecord], operator.add]   # every model call

    execution: ExecutionRecord      # ADR 0006 §12; total, written every turn
    failure: NodeFailure | None     # set by the node wrapper; routes to stamp
    answer: Answer | None
```

**`licensed` is an explicit output of `connect`**, and it is deliberately **not**
`by_type["table"]`. Budgets shape what is *rendered*; licensing shapes what is
*reachable*. A Steiner point must be licensed or every multi-hop query refuses
at ADR 0006's table layer — which is what `connect` exists to prevent.

> **The code did the opposite until 2026-08-26, and the consequence was a wrong refusal.**
> `serve/nodes/route_retrieve.py::route_node` seeded the set directly from the post-budget
> rendering: `licensed = retrieved["by_type"]["table"]`, and that `by_type` is assembled in
> `serve/nodes/pass_two.py` from the hits `apply_budgets(...)` **kept** — the ones the budget
> dropped never entered it. Only `resolve_node` (reference closure) and `connect_node` (Steiner
> points) widened the set afterwards, and nothing restored a table the budget cut. So budgets
> gated what is reachable: a gold table that lost its slot to the retrieval cap was unlicensed,
> and ADR 0006's table layer refused the statement with `r_table_not_licensed` — a
> *retrieval-budget* outcome wearing a *governance* label.
>
> **Fixed by taking open-work.md §4.2's first option: license the pre-budget table set.**
> `pass_two` records `retrieved["table_candidates"]`, every table it scored before the caps
> ran; `route` seeds from it and `connect` closes the licence over it. `connect`'s Steiner
> *terminals* stay the rendered tables — the licence is now wider than the prompt, and a table
> nobody is shown must not be able to decline the turn on `missing_join_path`. The fuller note,
> including what the widening costs, is at
> [ADR 0006](0006-execution-time-governance.md) §8;
> `tests/serve/test_the_render_budget_does_not_decide_what_a_turn_may_query.py` is the gate.
>
> Note also that `licensed`'s *meaning* was narrowed by
> [ADR 0012](0012-access-seam-principal-and-authorization.md) §3: it is "what retrieval found
> this turn" and nothing more, so a licensed table can still be refused
> `r_table_not_authorized` by the grant (`govern/check.py`, `govern/layers.py`). Widening the
> licence widens what that rule can name, and `_tables` asks the licence *first* precisely so
> the pair does not become an existence oracle.

```
licensed = { table ids from facet hits }
         ∪ { table ids pulled in by resolve }   # join endpoints, few-shot SQL tables
         ∪ { Steiner points added by connect }
```

`resolve` carries the same crossing accounting `connect` has, because few-shot
SQL closure pulls in every table a gold statement touches — unbounded and
unaudited otherwise, which is the shape of v1's self-licensing bypass.

**`execution` is why a broken `check()` cannot pass as a clean arm.** ADR 0006
converts any exception inside `check()` into a block; without a counter, a
`NameError` there turns every turn in an arm into a refusal while `crash_rate`
stays 0 and every register key is present. `guardrail_errors == 0` is a
quotability precondition (§4.1).

```python
# GuardVerdict is defined by ADR 0006 §6 and imported here — one owner per type.
class GuardVerdict(TypedDict):
    outcome: Literal["clear", "blocked", "error_failed_open"]
    rule_id: str | None             # ledger only
    detail: str | None              # ledger only — refusals return a fixed public string

class RewriteResult(TypedDict):
    before: str
    after: str
    outcome: Literal["rewritten", "unchanged", "failed"]

class FacetResult(TypedDict):
    facet: Stage                    # one of FACET_STAGES
    queries: list[str]              # the text actually searched; one element
    hits: list[Hit]                 # deduped within the facet
    channels: dict[str, ChannelState]   # extraction / lexical / semantic
                                        # degradation = differs from
                                        # expected_channel_state(facet, channel)

class SchemaCrossing(TypedDict):
    from_schema: str
    into_schema: str
    table_id: str
    reason: Literal["steiner_point"]

class NodeFailure(TypedDict):
    stage: str                      # a Stage member
    error_type: str                 # exception class name only — no message (L§6)

class RetrievalResult(TypedDict):
    """v2 replacement. v1's (rvgd.py:233) is deleted: it carried note_ids and
    triggered_note_ids (deleted concepts), one flat scores dict, and no
    join_ids."""
    by_type: dict[str, list[str]]           # asset_type → ids, post-budget
    selected: dict[str, Hit]                # asset_id → the highest-scoring hit
    attributions: dict[str, list[Hit]]      # asset_id → every facet's hit for it
    pulled_in: dict[str, Literal["resolve", "connect"]]
    schema_ranking: list[tuple[str, float]] # ALL scored schemas, pre-truncation
    lexical_coverage: float
```

**Two maps, because §2.4 rules that hits are not merged across facets.** A
single `dict[str, Hit]` can hold one, which would silently discard the
multi-facet attribution §4.1 requires the record to carry. `selected` drives
rendering; `attributions` is the audit and feedback surface.

**Budgets apply over distinct `asset_id`s, ranked by max hybrid** — an asset hit
by both `term` and `entity` consumes one table slot, not two. `route` counts
facets; budgets count assets. Those are different questions and the natural
implementation gets the second one wrong.

**`schema_ranking` keeps the full ordering, not just the winners.** Without it,
"the gold schema was not a candidate" and "the gold schema ranked 4th" are the
same observation — v1's `gold_schema_rank=None` collapse published a documented
failure bucket at a perfect score over 2030 rows.

**`lexical_coverage` is carried forward from v1 and must not be dropped.** With
an embedder every asset scores above zero, so an out-of-corpus question still
returns `top_k` tables and a clean run stamps confidence. Coverage is the
fraction of the question's content terms present in the index vocabulary —
deliberately vocabulary-level, **not** a score threshold, because a fused rank
is not comparable across questions. It feeds
`UncertaintySignals.weak_retrieval`.

Note the R1 discipline in the shape itself: `guard` and `negative` are **total
records written on every turn**, not `X | None`. A gate that leaves a trace only
when it fires cannot afterwards be distinguished from a gate that was never
wired up — *"half this repo's defects have that shape."*

#### 3.3 Node contracts

| node | reads | writes | model |
|---|---|---|---|
| `guard` | `question` | `guard` | **no** — rules (ADR 0006) |
| `rewrite` | `question`, `messages` | `rewrite` | yes (small), only with prior turns |
| `negative_gate` | effective question | `negative` | no |
| `facet_*` ×5 | effective question | `facets[name]` | 4 of 5, utility model — `facet_schema` searches the raw question and calls nothing (ADR 0011) |
| `route` | `facets` | `schemas`, `retrieved` | no |
| `resolve` | `retrieved` | `retrieved` | no |
| `connect` | `retrieved` | `retrieved`, `crossings` | no |
| `assemble` | `retrieved` | `delivery`, `messages` | no |
| `agent_core` | all | `messages`, `usage` | **yes**, main model |
| `refuse` / `decline` | `guard` / `negative` / route / connect reason | `messages` | no |
| `stamp` | all | `answer` | no |

> **Status note, re-checked 2026-08-12.** Left as decided; four rows of this
> section are stale, and §3.1's topology note carries the detail. In short: the table
> is missing **`abstain`** (reads `retrieved`, `licensed`, `schemas`, `delivery` and the
> knob; writes `abstention`, plus `path_kind` and `terminal_reason` when it withholds;
> model **no**; ships disabled),
> **`reflect`** (reads the ledger + result, writes `reflect_verdict`, model **yes**
> when enabled — ships disabled, and routes nothing) and **`narrate`** (reads
> `messages` + `result_table`, writes `answer_text`, model **only** when there is no
> agent prose to adopt); and the `guard` row's "**no**" covers the five deterministic
> rules but not the sixth BI-scope gate, which calls the utility model. The
> "everything else is deterministic" claim below is unaffected: `abstain` is a pure
> function of state, and it is the one *new* node that can end a turn.

"Effective question" = `rewrite.after` when rewriting succeeded, else
`question`.

**Six model entry points:** four extracting facets, `rewrite` (conditional),
and the main loop. Everything else is deterministic and testable without a
model — which is what makes steps 6–9 cheap.

**Who appends the human message matters.** The graph entry appends it, so
`rewrite`'s "prior turns exist" condition is *turn index > 1*, not "`messages`
is non-empty" — otherwise every single-turn eval question pays for a rewrite
call the cost model does not include.

#### 3.4 Message placement and caching

**Retrieved context goes into the user message.** The system prompt holds only
what is stable across turns and users. v1's `agent_core` is 1,590 chars (~397
tokens); the context concatenated onto it had a median of 17,782.

**Cache breakpoints are explicit.** Up to four `cache_control` markers per
request; a hit costs 10% of an input token, **a write costs 125%**, minimum
cacheable prefix is 1,024 tokens (**2,048 on Haiku-class models**, which likely
makes facet calls uncacheable), default TTL 5 minutes.
[Anthropic prompt caching docs, retrieved 2026-08-02.]

> **What shipped, recorded 2026-08-22: one breakpoint, and only on Bedrock.**
> `serve/nodes/agent_core.py::_cache_point` duck-types on `model.create_cache_point` — the
> Converse API is the only provider here that takes an explicit marker — and returns `None` for
> everything else, so OpenAI and the OpenAI-compatible proxy get no marker at all and rely on
> whatever automatic prefix caching the gateway does. The string `cache_control` does not appear
> in `src/` outside a historical citation in `register/citations.py`. The pricing figures above
> are the vendor's and are not measured here; the proxy has reported no `cache_read_tokens` in
> any run so far, which is why `usage.model_calls` exists.

**The prefix worth caching is the accumulated tool returns, not the context
block.** On a turn with `n_tool_calls + 1` model calls, everything before the
last call is cacheable prefix, and that grows with each tool return. Draft 2 did
its arithmetic on the context block alone and concluded "modest".

**Both figures earlier drafts quoted here were one arm's, stated as if they were
the system's.** Measured per arm on the `opus48-high-ladder` run, n = 1,351 each:

| arm | median input tokens | median context chars | context ≈ share of input |
|---|---|---|---|
| `baseline` | 17,115 | 2,154 | 3.1% |
| `seeded` | 17,892 | 4,498 | 6.3% |
| `curated` | **30,923** | **17,782** | 14.4% |
| `curated_sme` | 32,572 | 19,936 | 15.3% |

30,923 and 17,782 (≈ 4,450 tokens) are the `curated` row — not medians of
anything. Pooled across all 19,095 turns the median context is 6,007 chars, and
the arms span 8x. Quoting either number unqualified is the R3 violation
(`lessons-from-v1.md`), and it happened in this ADR.

**Two consequences for the criterion below.** First, **context reduction cannot
reach 30%**: at ~14% of input on the richest arm, deleting the context block
entirely would not get there, so the target has to come from caching. Second,
**caching was measurably off in every run the target was set from** —
`cache_read_tokens` is 0 across all three ladders (nonzero only in a later
47-question run, where it reached 43% of `curated` input). The headroom is real
but unexercised, which is what makes the gate falsifiable rather than
self-fulfilling. Share is computed at 4 chars/token and is an estimate; the
gate reads provider-reported `usage`, never an estimate.

**Acceptance criterion — a cost measurement with numbers in it:**

> Total input cost per question falls by **≥ 30%** over **N = 200** questions on
> the `curated` arm, with EX reported alongside and **not** asserted equal.

"At equal EX" was a draft-2 error: equivalence needs more power than difference
detection, and at n=1351 nothing tighter than ~3pp is demonstrable, so the clause
was unsatisfiable. Cost is measured with a **dated price table that returns
`None` for an unknown model, never 0** — v1 shipped a table entry that overstated
a run nine-fold, and two ladders that produced no USD at all.

#### 3.5 Tools

| tool | reaches what context does not |
|---|---|
| `read_body(asset_ids)` | `body` of assets `resolve`/`connect` pulled in |
| `inspect_schema(table_id)` | full column list including `physical_type` |
| `sample_rows(column_id, limit)` | **the only path to real values** |
| `run_query(sql)` | execution (governed by ADR 0006) |
| `ask_user(question)` | HITL interrupt |

**`sample_rows` takes a `ColumnAsset` id, not a table plus a column name.**
Identifiers cannot be bound as query parameters, so a model-supplied column
*string* interpolated into `SELECT {column} FROM {table}` is a direct injection
surface. Taking a corpus-resolved id means no model string reaches SQL. It is
still one of ADR 0006's four enumerated executors and still passes `check()`.

**Every tool is bounded and reads through `AnalystCorpus` as a type**, not as a
documented convention — v1's unenforced caller contract is how excluded PII
column names reached the routing index. `read_body` accepts only ids in
`hits ∪ pulled_in`; `inspect_schema` and `sample_rows` are bounded by
`licensed` (§3.2), **not** by the post-budget `by_type["table"]`. A rejection
emits a governance event and returns an identical message whether the asset is
out of scope or does not exist, so the model cannot probe for existence.

> **Corrected 2026-08-22, in three places.**
>
> **1. The `AnalystCorpus` type is enforced on one tool, not every tool.** Only
> `serve/fetch.py::sample_rows` tests it (`isinstance(corpus, AnalystCorpus)`, raising
> `GovernanceUsageError` on anything else). `read_body` and `inspect_schema` take
> `assets: Mapping[str, Any]`, and exclusion is pre-filtered *upstream* by
> `serve/session.py::_visible` before that mapping is built. That filter is real and its
> reasoning is worth reading — but it is a caller contract, which is to say the documented
> convention this sentence says it is not.
>
> **2. The sentence above became true on 2026-08-26 and was false before it.**
> `serve/nodes/route_retrieve.py::route_node` used to set `licensed` from
> `retrieved["by_type"]["table"]`, built in `serve/nodes/pass_two.py` from what
> `apply_budgets(...)` kept, so a budget-cut gold table refused as `r_table_not_licensed`. It
> now seeds from `retrieved["table_candidates"]`, recorded before the caps run, and `connect`
> closes the licence over that. See §3.2 — and note the consequence for *these two tools*
> specifically: `inspect_schema` and `sample_rows` are bounded by a set that is now wider than
> the rendered context, so both can be asked about a table the prompt did not show. That is the
> intent (the bound is "what this turn reached"), and it is why both also ask this principal's
> grant — correction 3 below.
>
> **3. The bounds carry more than these two fields now**
> ([ADR 0012](0012-access-seam-principal-and-authorization.md) §6, mirroring the note already
> in ADR 0006 §8). `govern/bounds.py::ToolBounds` declares `licensed`, `readable_assets`,
> `grant: ResolvedGrant` and `withheld`; `__post_init__` **raises** when a restrictive grant
> arrives without a `withheld` set, because an optional control argument is a control that will
> go un-wired. `inspect_schema` and `sample_rows` therefore ask the grant as well as the
> licence; `read_body` deliberately does not.

This is not optional hardening. Column ids are **derived and therefore
guessable** (`derive_column_id(table_id, physical_name)`), and D6 exclusion
happens at corpus-filter time — so an unbounded `read_body` in a pooled
57-schema lake would be a fourth door around D6, into the field that holds the
richest prose. v1's lesson, stated three lines from this table and not applied
to its own new tool: **"a tool that grants privilege must have a bound the model
cannot widen."**

> **"Guessable" is weaker than stated, 2026-08-22 ([ADR 0008](0008-identifiers-end-to-end.md)
> D1).** `derive_column_id` returns `f"{table_id}.{slug(physical_name)}"`, and
> `corpus/identity.py::slug` returns the name unchanged **only** when it is already a bare
> identifier — otherwise it sanitises unsafe characters to `_` and appends a six-hex SHA-256
> digest of the exact name (`Air Carriers` → `Air_Carriers_66c534`). A digested id is not
> guessable from the physical name. On the shipped BIRD corpus that is 1 table of 656 and 1
> column of 5,947, so the argument for bounding `read_body` survives essentially intact — but
> the bound is the reason, not the id's shape.

`read_body` takes a list to avoid round-trips; **total return capped at ~20k
tokens** — the threshold was chosen against Deep Agents' filesystem middleware, which
evicts a tool result past ~80 KB to a file and returns a preview, so one read silently
becomes several turns out of the step budget. **That middleware is not in this tree** —
`deepagents` is retired, not deferred, and `pyproject.toml` carries the reasoning — so the
cap is now a bound on prompt cost and nothing else. It is enforced in characters:
`serve/fetch.py::read_body_cap` reads `read_body_max_tokens` and multiplies by four,
defaulting to 80,000 characters when the knob is absent. `inspect_schema` and `sample_rows`
carry their own caps (`serve/fetch.py::SAMPLE_ROWS_MAX_VALUES` is 20).

`search_corpus` is deleted. The bet is that five facets plus two-pass retrieval
recall better than v1's single pass plus agent self-rescue. **Measure it:** in
v1, how often did `search_corpus` recover a table the front half missed *on a
turn that then answered correctly*? A non-trivial number means restore it.

Two more rules from v1: **batch tools return partial success, never raise** (one
bad spec discarding a whole batch is pure token churn); **tool exceptions must
not be laundered into refusals** (a `NameError` in a helper spent a long time
looking like an intermittent model hiccup).

**Every tool call that has no other trace emits a stage record.** `run_query` is
the exception and has no `Stage` member of its own: it already emits the
`check` + `execute` pair, and a third record would double-count an action the
ledger and every rate already agree on. v1 computed the whole
search/inspect/sample detail and dropped it, because the sink was optional and
no eval arm passed a callback — **zero such rows exist on disk**. The successor
question ("was `read_body` worth it?") is unanswerable by the same mechanism
unless the sink is mandatory.

#### 3.6 What is delivered, and hashing it

`assemble` renders, in this order:

| block | source | rendered |
|---|---|---|
| `## Must honour` | `rules` of selected schemas + tables in context | verbatim (corpus is trusted — §1.6) |
| Schema context | every asset in `RetrievalResult` | **structural line always** (I3); **`body` if the asset was hit; nothing further if it was `pulled_in`** |
| Reliability caveats | `reliability.suspect` | always, never budget-evicted |
| Few-shots | `by_type["few_shot"]` | `body` (question + SQL) |
| Conversation history | prior turns | verbatim |

Draft 2 never stated this, which left `read_body`'s purpose dangling and I2
ambiguous. The rule is the one decided for §2: **hit ⇒ full; pulled in ⇒
structure only.** A column another block references (a join predicate, a metric
expression, few-shot gold SQL, a term binding) is **always** rendered
structurally, or the prompt shows a join the model cannot spell.

```python
class Delivery(TypedDict):
    context_block: str | None
    context_hash: str | None       # None on paths that skip assemble
    tool_delivered: dict[str, str] # call_id → sha256[:16] of the return, for EVERY
                                   # corpus- or database-derived tool return:
                                   # read_body, inspect_schema, sample_rows
    delivery_hash: str | None      # sha256 over context_hash + sorted tool_delivered
```

**`tool_delivered` covers every tool that hands the model corpus or database
content, not just `read_body`.** An earlier draft hashed only `read_body`, which
left `sample_rows` out — and real database values are the single largest source
of arm-to-arm variation in what the model actually sees. I4 says *everything*
delivered, and the type has to mean it.

**A total context budget is a knob, it is counted in characters, and it is a
backstop rather than a cost lever.** The per-type budgets bound what is *ranked*,
not what is *rendered*: `resolve` pulls in every column of every table in the set
and pulled-in assets do not consume budget, so with 8 tables plus join endpoints
plus up to 5 Steiner points, and v1's measured 42–275 columns per schema,
structural column volume is an order of magnitude above the column budget of 30.
A ceiling has to exist.

`context_budget_chars = 80_000`. **Characters, not tokens**, because a token
count needs a tokeniser per provider and has to be correct at delivery time in
production, where characters are free and exact.

**The value sits above the largest context v1 ever delivered** — 76,354 chars,
max over 19,095 turns — so it provably never fires on observed traffic. That is
the point, and the reason is a measurement, not caution. Fire rate by arm:

| arm | n | >24k | >40k | >60k | >80k |
|---|---|---|---|---|---|
| `baseline` | 5,481 | 0.0% | 0.0% | 0.0% | 0.0% |
| `seeded` | 5,461 | 0.0% | 0.0% | 0.0% | 0.0% |
| `curated` | 5,451 | **23.5%** | 5.3% | 1.9% | 0.0% |
| `curated_sme` | 2,702 | **27.4%** | 5.5% | 1.6% | 0.0% |

Every binding threshold truncates **only the treated arms**. A 24,000 cap would
cut the treatment on a quarter of `curated` turns and on none of `baseline`'s —
weakening the treatment in exactly the arms whose treatment the ladder exists to
measure, and reporting it as delivered. So: the cap is a bound against a
pathological corpus or query, **not** the instrument for §3.4, and when it fires
that must be recorded (I4, and the R2 rule in `lessons-from-v1.md`).

*An earlier draft of this section said the cost gate "has nothing to be 30% of"
without a ceiling. That was wrong twice over: §3.4 is denominated in dollars via
a dated price table, not in this budget — and the context block is only ~14% of
input tokens on the `curated` arm, so deleting it entirely would not reach 30%.
See §3.4.*

Eviction order, most-evictable first:

```
1. body of pulled-in assets        (already excluded by the hit/pulled-in rule)
2. body of hit assets, lowest hybrid score first
3. structural lines of pulled-in tables, lowest score first
4. NEVER: structural lines of hit assets, suspect caveats, rules, few-shot bodies
```

**Two hashes, two jobs.** `context_hash` is deterministic — a pure function of
the corpus and the pipeline — and is what the comparability gate reads.
`delivery_hash` depends on which `read_body`/`inspect_schema` calls the model
chose to make, so it is **not** deterministic; it is reported as a diagnostic
and is what answers "did the curated bodies actually reach the model", which
`context_hash` alone cannot. §4.1 states which gates which.

On paths that skip `assemble` (refuse, decline, crash), both hashes are `None`
**as a distinct value, never the string `"unknown"`** — v1's `"unknown"`
compared equal to itself and let two runs with no recorded treatment pass the
comparability gate.

---

### 4. Boundary contracts

Four interfaces, written before deletion begins.

#### 4.1 `eval ↔ serve`

`eval/` is rewritten too, so this is a contract between two things that do not
yet exist. It is still written first, because it is what keeps the rewrite from
becoming two coupled rewrites.

**One question in, one `Answer` out**, on every path including refusals and
crashes.

**The recorded projection is derived, not hand-listed.** §6 forbids
hand-maintained field lists, and draft 2's version of this section was one —
already incomplete on arrival. Instead: one declared **record register** from
which the state schema, the recorded projection, the comparability keys and the
gate list are all generated, plus v1's test that every gate key is non-absent in
a real record. v1's allow-list relay swallowed two fields **for a year**.

Fields the register must include, because they cannot be reconstructed later:

`delivery_hash`, `context_hash`, `tool_delivered` · per-facet `hits` with
`facet`, `asset_type`, `queries`, `lexical`, `semantic` · `schema_ranking`
(full, pre-truncation) — the gold schema's *rank* is derived from it by eval,
which is the only side that holds gold, so it is not a serve-recorded field · `pulled_in` · `crossings` ·
`lexical_coverage` · `guard`, `negative`, `rewrite` (all total records) ·
per-facet `ChannelState` for extraction / lexical / semantic (§2.3) · `usage` including
cache read and write tokens · `failure` · the resolved knob set (§5).

**Required is not the same as always-written, and conflating them breaks a gate.**
Eight of those fields — `facet_hits`, `facet_channels`, `pulled_in`, `crossings`,
`tool_delivered`, `negative`, `licensed`, `schemas` — are owned by stages a
**refusal path never reaches**. A guard-blocked turn arrives at `stamp` without
having run the fan-out. Declaring them unconditionally required leaves two
choices, and both are defects:

1. The presence check fails on **every** guard-blocked turn, so the check gets
   disabled or its failures get ignored.
2. The producer writes an empty collection to satisfy it — **and then the
   degradation gate reads an empty `facet_channels` as "no channel differed from
   its expectation", i.e. as clean, on a turn where no channel ran at all.**

Absence reading as agreement, in the field added to stop absence reading as
agreement. So they are declared **stage-conditional**: `Absence.not_applicable`,
absent only on paths whose owning stage did not run
(`register/record.py`), and the gate is worded to distinguish the two cases:

> on turns where the fan-out ran, no channel state differs from its declared
> expectation; **the observed count is published beside the rate**, so "five
> facets ran and none degraded" cannot be confused with "no facet ran".

**Quotability preconditions** (refuse the comparison, do not warn):

```
facet_degradation_rate      == 0      over the arm, AND the count of turns
                                      where the fan-out ran is published with it
                                      (a rate of 0 over 0 turns is not a pass)
negative error_failed_open  == 0
guardrail_errors            == 0      ADR 0006 §12
crash_rate                  == 0
context_hash recorded by both arms on every shared question
knobs_resolved  declared treatment moved, no other comparability knob did
every register key non-absent
```

**Amended 2026-08-11 (audit D9).** The `context_hash` line read "distinct across arms on ≥ 95%
of shared questions" and was the treatment test. It was not one: retrieval is nondeterministic,
so the hashes differ whether or not the treatment did, and the gate passed at 0.9993 on a pair
differing only by a random seed. It is now an existence check, and the treatment judgement is
`eval/report.py::knobs_comparable`, which reads the treatment the caller declares in
`arms.toml`.

**The delivery gate is on `context_hash`, not `delivery_hash`.**
`delivery_hash` includes tool-fetched bodies, so it depends on which
`read_body` calls the model chose to make — it is not deterministic, and a gate
on it would conflate "the treatment differs" with "the model behaved
differently". `context_hash` is a pure function of the corpus and the retrieval
pipeline. **`delivery_hash` divergence is reported as a diagnostic**, and it is
the field that answers "did the curated bodies actually reach the model", which
`context_hash` alone cannot.

**Build a scoring byte-golden before deleting — scoped to per-row grading
only** (execute, normalise, compare).

**Generate the reference with the current scorer; do not take it from the run
artifact.** The obvious approach — replay `generations.jsonl` and require it to
match what that run recorded — freezes a bug: `b6b7ee5` (2026-08-01 21:06,
*"SELECT \* gold was graded against a hash of a different query"*) landed **the
day after** the 20260731 ladder was produced. Matching the artifact would
require v2 to reproduce that grading defect to pass its own gate.

So: run the **current, fixed** scorer over the archived `generations.jsonl`,
commit its output as the golden with the producing commit sha and date, and
require the v2 scorer to match *that*. Two more conditions, because the scorer
executes SQL: results are `ORDER BY`-normalised before hashing (row order is
not deterministic — §1.7), and a declared **allowed-diff file** makes a
deliberate row-scoring fix a reviewable diff rather than a blocked gate.

**The golden must NOT cover aggregation or statistics.** Draft 2 said "grading,
statistics and gold handling must not change" — but L-R3 and half of L§1 are
defects *inside* that layer: the two-population headline/test split that gave
one quantity opposite signs, MDE computed at the replicate's n, zero discordance
read as zero noise, six raw pairwise tests, question-level tests over questions
nested in databases, and the dataset's own 25-question exclusion list never
opened. Freezing that layer would require v2 to reproduce those bugs or fail its
own gate. **The aggregation layer is explicitly permitted — required — to
break the old numbers**, and L-R3's fix is part of this contract: one filtered
population object shared by headline and significance test, with a test
asserting the stratum's net reconstructs the headline delta to floating-point
equality.

#### 4.2 `api ↔ frontend`

The frontend was not rewritten, but four payload changes reached it and **three were
breaking**:

1. **`Answer.provenance` carried `normative_force`** and the v1 UI read it. Deleting
   `NoteAsset` empties a rendered chat section.
2. **`/columns/{id}/related`'s `rules` array was sourced only from notes** and becomes
   permanently empty.
3. **`/knowledge-graph`'s `kind` was a strict client-side `z.enum`** and `parse()` throws —
   a new `schema` kind **hard-fails the whole graph page** rather than degrading.
4. **`/corpus/assets` already shipped a field named `summary`** — a synthesized display
   one-liner. After v2 the name means the ≤250-char indexed field.

Also: only 2 of the 9 read routes carried `description` at all, and `body` was exposed by
none — a schema browser that showed documentation would start showing a retrieval artifact.

**These were deliverables. Three of the four were delivered.** `normative_force`
appears nowhere in `ui/`; `browse_routes.py` sources `rules` from `table.rules` and serves
`description` as `body` falling back to `summary`. The record stays because
item 4 is the reason `summary` means two things in git history.

**Item 3 was closed from the producer's end, not the consumer's, and the enum is still
frozen.** `ui/lib/schemas.ts::graphNodeKindSchema` is a `z.enum` of seven literals, exactly
as it was. What changed is that `api/routes.py::_SEMANTIC_NODE_KINDS` is the same seven
literals on the server, and `_knowledge_payload` filters every node through it — so
`/knowledge-graph` cannot emit a `schema` node and `parse()` cannot throw. Two consequences
a reader should not have to rediscover. `SchemaAsset` is invisible on that page, which is a
product decision nobody wrote down. And **both sets still carry `note`**, an asset type
§1.4 deleted: it is unreachable from either side and is the kind of leftover that reads as
a supported case. The fix item 3 actually asked for — one vocabulary, derived from
`register.assets`, on both sides of the wire — is still owed.

#### 4.3 `Stage` taxonomy

`stages.py` exists because nine competing failure vocabularies made "which part
is breaking?" uncomputable, and **stage names are how `classify_outcome`
separates a crash from a refusal**. Changing them silently is how a run becomes
unquotable for the reason the pre-2026-07-25 numbers were retired.

Renamed/removed: `route` (v1 = ingest rail; v2 = schema selection, v1's
`schema_pick`), `assemble` (v1 spans retrieval + build; v2 = rendering only),
`refuse_gate` → `negative_gate`, `search_corpus`/`read_notes`/`grep_notes`
(deleted), `shortlist`/`schema_pick`/`retrieve`/`license` (subsumed). New:
`guard`, `rewrite`, `facet_*`, `resolve`, `connect`, `read_body`, `stamp`.

**The enum diff is a precondition of step 9.** Port the design rules verbatim:
two orthogonal axes, gradeability excluded as a third thing,
declared-but-unemitted stages kept on purpose, text and pure functions only.

#### 4.4 ADR 0006

Execution-time governance is a hard dependency. This ADR assumes but does not
specify: the seven-member `Layer` enum and `check()`; the **positive function
allowlist** v1 deferred, including the whole-row-aggregate rule; the positive
binding rule that replaces per-shape denylisting; "absence must refuse" for
every optional security parameter; the canonicalise → check → limit → execute
pipeline order; `guard`'s rule set and its red-team corpus; graded delivery
narrowed to the cost layer only; path validation for `SchemaAsset.name`; the
four enumerated executors; `ExecutionRecord`; and the amendment retiring 0002's
refuse-gate invariant.

**ADR 0006's Context section holds the canonical bypass list (B1–B10).** Both
acceptance suites refer to it — an earlier pair of drafts cited two different
sets, so a suite built to one did not satisfy the other's gate.

Two types cross the boundary and 0006 owns both: `GuardVerdict` (§3.2) and
`ExecutionRecord` (§3.2). 0006's knob section (§13) is a declared part of §5's
knob register, so a run with graded delivery on and one with it off do not hash
identically.

**0006 must be written before deletion**, alongside the other three contracts,
and **its §§1–5 land before the serve graph**, not after.

---

### 5. Defaults

Starting values chosen to be measurable, not results. **This table plus ADR
0006 §13 are the knob register as designed** — the manifest, the comparability keys
and the serve config hash are derived from it, so a new knob joins the
gate by default and two runs with different *security* configuration cannot hash
identically. No knob is settable only from an eval CLI (v1 benchmarked a routing
configuration no deployment could run).

> **The register in force is `register/knobs.py::KNOB_REGISTER`, and it is larger than
> these two tables.** 60 knobs (recounted 2026-08-22; it said 57), of which 50 carry
> `Role.comparability` (it said 47) and therefore enter
> `comparability_keys()` and the config hash; the rest are `Role.operational` (git sha,
> worker counts) and `Role.scope` (arms, split, question subset). The two ADR tables name
> the retrieval and security defaults *decided here*; everything the code has grown since —
> the model, provider, timeout and retry knobs, `prompt_set`, `agent_recursion_limit`,
> `semantic_scale_ceiling`, `reflect_enabled`, `abstention_policy_enabled`, `access_grant` —
> is declared there and nowhere else. Read the module, not the tables, when the question is
> "what is in the hash".

> §4.1's *record* register and this *knob* register are different things: one
> declares what every turn records, the other what the run was configured with.
> The comparability keys derive from the knob register; the presence test
> derives from the record register.

| knob | default | note |
|---|---|---|
| `summary_max_chars` | 250 | Pydantic; over-length is an error, never truncation |
| `summary_min_chars` | 1 | blank documents are a live provider hazard |
| `candidate_depth` | 50 per query, within target types | calibration: route recall@3 vs depth |
| `route_top_n` | 3 | |
| facet weights | all 1.0 | `schema` arguably deserves more; no data |
| `w_lexical` / `w_semantic` | 0.5 / 0.5 | renormalised by active channels |
| `lexical_saturation_k` | 1.2 | declared at the value every run has used, and **frozen across arms** — a per-arm fit would make `lexical` incomparable. Still unfitted against the corpus BM25 distribution; `nodes/facets.py` scales each channel within its own facet, so `k` no longer decides which channel wins |
| per-type budgets | declared in `register.assets` beside the types they belong to (schema all · table 8 · column 30 · join 5 · metric 5 · term 5 · few_shot 3 · negative n/a); referenced here as one content-hashed knob so a budget change moves the config hash | after pass two |
| `max_steiner_points` | 5 | exceed ⇒ decline |
| `max_crossings` | 2 | exceed ⇒ decline |
| `expand_hops` | 0 | off until measured |
| `negative_tau` | **unset** | **gate ships disabled** |
| `cache_cost_reduction_target` | 30% over N=200 | §3.4 acceptance; comes from **caching**, not from context reduction — context is only ~14% of input |
| `context_budget_chars` | 80,000 | **chars, not tokens**; a backstop above v1's observed max of 76,354. Any binding value truncates only the treated arms (§3.6) |
| facet / rewrite model | small (Haiku-class) | concurrent; latency counts once |
| `read_body_max_tokens` | 20,000 | chosen below Deep Agents' eviction threshold; that middleware is retired here, so the cap is now a prompt-cost bound (§3.5) |

### 6. Code organisation constraints

v1: **17 files over 1,000 lines**, one at **5,085**, and **30% of all code in
files over 1,000 lines**.

| constraint | value |
|---|---|
| file length | soft **400**, hard **1000**, CI-enforced. Hard tier raised from 800 on 2026-08-03: Python at this repository's prose density does not fit a coherent unit of work into 800 lines, and the file that forced the question was over by 55 lines of failure messages and preconditions. The cost is stated where the enforcement lives (`tools/check_file_length.py`): 800 caught a file *before* v1's worst shape, 1000 catches it *at* that shape, so the soft tier's printed overrun count is now the early warning rather than a courtesy. `tests/conformance/test_register_closure.py` asserts this row and the constant agree — a limit in a table no process reads is a preference. |
| one implementation per concept | one import name. v1 had two McNemars (`eval/analysis.py:572` and `eval/power.py:338`, both present at deletion), **three** temp-then-replace helpers (`metrics.write_manifest`, `harness._write_jsonl` and `index.append_run` — commit `0eb23ae`, "three copies of the temp-then-replace dance had grown, and no copy had both halves"), and two `LOW_CONFIDENCE_JOIN` constants **with different comparison operators** (source: the constant's own comment at `analyst/answer.py:33-48` in `2347ae3^` — `governance.py` used `<`, `viz/presenter.py` declared its own copy and used `<=`). Enforced by `tools/check_one_implementation.py`. *An earlier draft of this row also claimed "two EX definitions"; that could not be sourced — v1 had one `execution_match` in `eval/ex.py`, imported everywhere — and it has been removed.* |
| no hand-maintained field lists | derive from one declared register (§4.1, §5) |
| no knob reachable only from an eval CLI | |
| no world-describing literal without a source | artifact path and date, in code and docs |
| "not measured" is a distinct value | **L-R1** — including in rounding and formatting helpers; no `x or 0` on a measurement field |
| one durable-write primitive | mkdir → temp → flush → fsync → retried replace → unlink in `finally`, plus an inter-process lock on any read-modify-write artifact |

Test-authoring rules, ported verbatim (L§7): strict xfail; paired no-op
controls; scoped wiring assertions; always-written gate state; never assert a
module against its own constant; credentials stripped and process-wide
singletons reset per test; assert instrumentation at the single producer, not a
re-export.

---

## Consequences

### What this buys

One field, one consumer. Comparable index entries — the precondition for fusion.
Columns rankable, which is the missing path on obfuscated schemas. Routing stops
diluting; every summary embedded once. Long knowledge has an unbounded home that
costs prompt tokens, not recall. Attribution built in: `Hit` carries facet, both
channel scores and every query that hit; `RetrievalResult` separates hits from
pulled-in and keeps the full schema ranking. Caching becomes possible.
**Steps 6–9 are measurable with no model at all**, because the seed guarantees a
non-empty summary on every asset.

### What this costs

86,746 lines. All 22 built corpora. Four extra model calls per turn (three
facets + conditional rewrite) — latency absorbs, cost does not. A new failure
mode: facet extraction can be wrong, and narrow recall on obfuscated schemas is
dangerous — mitigated by per-channel degradation tracking that gates
quotability. ~6,000 additional column summaries to embed per build, cached
after.

### What breaks

Every historical corpus and number — acceptable, since pre-2026-07-25 numbers
are already retired and `curated_sme` is under suspicion (OQ1). Three frontend
payloads, one of which hard-fails a page. The v1 aggregation numbers, **by
design** (§4.1).

---

## Open questions — to be measured, not decided

1. **Did `NoteAsset` leak eval answers into `curated_sme`?** 139 notes carry
   question-specific answers with triggers auto-extracted from that question's
   own text (110 of 139 `on_match`). Cross-reference `injected_note_ids` in
   `generations.curated_sme.jsonl` against the notes' `raised_by` and the
   current question id. **If injections concentrate on the originating question,
   every `curated_sme` number is void.** No code change; v2 closes the channel
   structurally but the historical claim needs settling.
2. **What is `search_corpus` worth?** How often did it recover a table the front
   half missed, on a turn that then answered correctly? Decides §3.5.
3. **What is `expand_hops` worth?** Of the tables gold SQL uses, how many
   entered neither by facet hit nor by Steiner path?
4. **Does fusion become non-negative on a uniform index?** Re-measure
   lexical/semantic/hybrid on the v2 index.
5. **How often is a hit lexical-only?** Now answerable, since both channels
   score every candidate. Decides whether weights need tuning or a third
   exact-match channel is warranted.
6. **Is within-schema IDF worth a second statistics table?** Compare
   within-schema table selection accuracy, global vs subset IDF. Withdrawn from
   the design (§2.5); this is the experiment that could bring it back.
7. **Is `route` top-3 without an LLM pick better than v1's top-10 with one?**
   v1 measured shortlist 0.952 / pick 0.873 at top-10 <!-- [retired]: measured on the architecture this ADR replaces; register/citations.py --> — **106 questions had gold
   in the shortlist and the pick chose otherwise, only 3 survived.** The often-
   quoted "~0.85 at top-3" is an **inherited estimate measured on the
   mean-by-concatenation index v2 deletes**, so it is not a v2 property. If
   measured v2 route recall@3 comes in below v1's pick accuracy of 0.873, revisit <!-- [retired]: that threshold is void, so step 7 is the experiment and not the bar -->
   the picker.
8. **What is `_SEMANTIC_BOOST` worth?** v1's BM25F field weight expressed this
   project's central thesis — *curator-authored language is the trusted match
   surface, raw identifiers are weak and possibly adversarial* — and **sat at 1
   (no-op), never calibrated**. With BM25 winning at recall@1, the thesis itself
   is in question. Inherited as an experiment, not a finding.
9. **Where does an answered clarification go?** Deleting `NoteAsset` removes the
   only sink for question-scoped SME knowledge, which is what all 139 notes
   were. `ask_user` still exists. Naming the sink — a curator-queue artifact? a
   governed `body` edit? nowhere, deliberately? — belongs to the curator ADR,
   but v2 must not ship with an interrupt whose answer evaporates.
10. **The curator is not designed here.** This ADR specifies what it must
    produce; *how* — the execution model that makes 0.82 tool calls per training
    pair and writes nothing on 6 of 57 schemas — is a separate ADR.

---

## Implementation order

**Delete first.** An earlier draft put the boundary contracts before the
deletion, reasoning that a contract protects what is *outside* the boundary. That
reasoning died when `eval/` joined the rewrite: with nothing outside left to
protect, a contract written beside 87k lines of the thing it replaces is not a
guardrail — it is new code with the old implementation in its peripheral vision,
and two files of the same name in the same tree. Written on an empty floor it is
what it actually is: the first module of the new system.

1. **Write ADR 0006 and the frontend delta** (§4.2, §4.4) — the two contracts
   that describe things the deletion does *not* touch.
2. `git checkout -b v2`.
3. **Commit 1: delete `src/`, `tests/`, `scripts/` — 87,812 lines.** Keep
   `docs/`, `runs/` (evidence — archived, not loadable under the new schema),
   configuration, git history. Also delete anything that names a deleted module:
   a config pointing at code that no longer exists reads as wired up, which is
   worse than absent.
4. **The boundary contracts, as the first code on the empty floor** (§4.1, §4.3)
   — `Stage`/`Outcome`, the record register, and a package `__init__` that does
   nothing on import (v1's auto-loaded `.env` leaked a real API key into every
   test process). Import-time invariants: a gate may only read a declared field;
   every `health`-tier field is read by a gate; every `refused_by` maps to a real
   `Stage`.
5. **Build the row-scoring byte-golden** (§4.1) against archived run data, using
   the **current** scorer — not the run artifact, which predates `b6b7ee5`.
6. **Asset schema + validation + CI** (§1): per-type `identifier_field`, the join
   ON digest, `governance` on every asset, the phase-boundary provenance guard,
   path-component validation (§1.6 / ADR 0006 §9), the file-length gate.
7. **Seed** (§1.7) — deterministic summaries for every asset including
   `SchemaAsset`, deterministic `sample_values`. This is what makes the next
   three steps model-free.
8. **Unified index + lexical/semantic/hybrid** (§2.2, §2.4). Offline.
9. **Two-pass retrieval, `route`, `resolve`, `connect`** (§2.5–2.9), producing
   `licensed`. Offline. **Gate: route recall@3 by schema-size decile** (§2.6).
10. **ADR 0006 §§1–5** — `Layer`, `check()`, the function allowlist, binding, the
    connection contract, path validation. **Model-free, and a hard prerequisite
    of step 11**: without it there is no `check()` in the repository, so step 11's
    cost gate would run either with `run_query` disabled (not measuring what it
    argues about) or with an unguarded agent against Postgres.
11. **The serve graph** (§3) including message placement, cache breakpoints, the
    node-exception wrapper, and `ExecutionRecord`. **Gate: §3.4's cost criterion.**
12. **ADR 0006 §§6–11** — `guard` and its red-team corpus, tool bounds, graded
    delivery, the ledger. **Gate: one test per bypass B1–B10 in ADR 0006's
    Context section**, which is canonical for both ADRs.
13. **Facets** (§2.3) with per-channel degradation tracking.
14. **Frontend deltas** (§4.2).
15. **Eval rewrite**, against the golden from step 5. **Run the free grader
    ceiling first** (`--oracle-only`, no model, ~4 minutes) — it re-scales every
    downstream conclusion, and v1 spent a long time reading 56.3% against an <!-- [retired]: absolute EX through the pre-2026-08-06 grader; the point is the missing denominator -->
    unknown ceiling that turned out to be 99.70%.

    **Amended 2026-08-06.** The ceiling needs an *independent* gold to compare
    against — `gold_fingerprint`, or `gold_columns` + `gold_rows` on the question.
    Nothing in this repository produces those, so for the whole of v2 the arm took
    a fallback branch that fingerprinted the executed gold **against itself** and
    returned 1.000 for any statement, `SELECT 'garbage'` included. It now reports
    *unmeasured* instead. A ceiling of 1.000 that costs nothing to obtain is not a
    ceiling; this step is not done until the golden from step 5 ships fingerprints,
    and `pred_fingerprint` on the oracle row is the field to harvest into them.
16. Curator redesign — separate ADR.
17. `negative_gate` — blocked on a negative corpus existing.

Steps 4–10 have no model in them.

**Where the order stands (2026-08-12).** Steps 1–4, 8, 9, 10, 13 and 14 have landed as
written (14 with the exception §4.2 now records). Seven have not, or have not fully, and
the reasons differ enough that "in progress" would hide them:

- **Step 5 never happened.** There is no row-scoring byte-golden in this tree — no golden
  artifact, no allowed-diff file, no test that reads one. Step 15's amendment already
  depends on it ("this step is not done until the golden from step 5 ships fingerprints"),
  so the oracle ceiling reports *unmeasured* and will keep doing so until step 5 exists.
- **Step 6 shipped minus one item, deliberately.** The phase-boundary provenance guard was
  built, had zero callers, and was deleted — §1.5's 2026-08-06 amendment is the record, and
  `tools/graft_corpus_fields.py`'s outright refusal of `governance` is the control that
  replaced it. Everything else in the step is in `corpus/schema.py`,
  `corpus/validate.py`, `corpus/identity.py` and `tools/check_file_length.py`.
- **Step 7 shipped its summaries and not its samples.** `corpus/seed.py::seed` writes a
  deterministic `summary` for every asset; it authors no `sample_values`, and there is no
  profiler to author them — `corpus/introspect.py::Introspection` carries names and types
  only. The shipped corpus bears this out: 0 of 5,947 `ColumnAsset`s carry a
  `sample_values` tuple. §1.7's determinism argument is therefore about a producer that
  does not exist yet; the session setting it requires (`synchronize_seqscans = off`) does
  exist, on `PostgresConnector`.
- **Step 11's gate has not been run.** `cache_cost_reduction_target` is declared and has no
  reader; `tools/check_declared_is_consumed.py` waives it in as many words — *"an acceptance
  criterion for a measurement that has not been run"* — and flags that its
  `Role.comparability` is wrong for a knob nothing reads.
- **Step 12 is the same split ADR 0006's own order records:** `guard` and tool bounds
  shipped, the red-team corpus does not exist, graded delivery is declared and unwired, and
  §11's redactor is withdrawn rather than pending. The B1–B10 gate itself is met.
- **Steps 16 and 17 are untouched and blocked on the same kind of thing.** There is no
  curator in `src/`, and no rebuild kit. The served corpus is a sibling repository.
  `negative_gate` still ships disabled with
  `negative_tau` `UNSET`, waiting on a negative corpus.

**A standing rule from L-R5:** the paid ladder is confirmation, never screening.
MDE is 2.64–3.23pp and the interventions move 1–2pp, so every intervention gets
a deterministic proxy (route recall, column recall, delivery rate) with a stated
conversion factor before anything is run at ladder scale.

---

## Appendix: terminology audit

| pre-v2 | v2 |
|---|---|
| `description` | `summary` (short, indexed) + `body` (long, injected) |
| `NoteAsset` | deleted; content belongs on the asset it annotates |
| `NoteKind`, `NoteActivation` | deleted — "the asset was retrieved" is the activation |
| `NormativeForce` | deleted — field position (`rules` vs `body`) is the semantics |
| `Trigger` / PIN | deleted — `rules` for must-appear, `summary` for must-be-findable |
| `MetricRule` | deleted — content goes to `MetricAsset.body` |
| `RVGD` | retired (mixed two dimensions) |
| `R` / `V` | `lexical` / `semantic` channels |
| `G` | split into `resolve` (closure) and `connect` (Steiner) |
| `D` | the `example` facet |
| `ground` | retired — also collides with LLM "grounding" |
| `schema_documents()` | deleted — `route` aggregates the unified index |
| `Column` | `ColumnAsset` |
| the `schema` string field | `SchemaAsset.name` |
| `search_corpus` | `read_body` (different job — §3.5) |
| `Stage.route` | v2 = schema selection (v1's `schema_pick`) |
| `Stage.assemble` | v2 = rendering only |
| `Stage.refuse_gate` | `negative_gate` |
| `context_hash` alone | `delivery_hash` (context + tool-delivered bodies) |
