# 0003: Governed notes (`NoteAsset`) and tri-modal retrieval

_[English](0003-governed-notes-tri-modal-retrieval.md) · [简体中文](0003-governed-notes-tri-modal-retrieval.zh.md)_

- **Status:** Proposed (2026-07-21). Design agreed in a multi-agent design
  review (4 independent proposals, 3 diverse judges, and an adversarial
  red-team; all three judges independently ranked "generalize `RuleAsset`"
  first). No code yet; awaiting the 5 open decisions below and Phase 1.
- **Deciders:** project owner + design session
- **Related:** [0002](0002-governed-agentic-serve-runtime.md);
  [pipeline-design.md](../pipeline-design.md);
  [design-decisions.md](../design-decisions.md) (D6 human gate, D9 corpus
  file-structure, D10 proposer+adversary, D15 multi-schema, D16 agentic core);
  [asset-schemas.md](../asset-schemas.md);
  [plans/datalake-run.md](../plans/datalake-run.md) (the routing numbers)
- **Supersedes:** the `skill` asset concept entirely: `SkillFrontmatter` /
  `SkillKind` (`schemas.py:388-396,130-134`), the `corpus/<schema>/skills/*.md`
  markdown surface, and the (never-true) framing that a `kind=routing` skill
  influences schema routing.

## Context

**The data-lake scenario (D15).** ~69 schemas in one Postgres DB; a router
picks schema(s) per question. Two diagnosed gaps and a governance hole
motivate this ADR.

**Gap A: routing never consults skills.** Schema routing
(`agent.py::assemble`, roughly `~365-402`) ranks over `schema_documents()`
(`schema_router.py:86-112`), which buckets only Table / Metric / FewShot /
Term documents per schema; skills are excluded from that ranking entirely. A
skill *is* filtered by schema, but only inside `filter_corpus_for_retrieval`
(`schema_router.py:355-360`), and that filtered corpus (`retrieval_corpus`)
feeds `retrieve()` (`agent.py:410`), not the prompt. A `kind=routing` skill
therefore cannot influence which schema is chosen; it is filtered for
retrieval scoring but never for prompt content. Worse, `assemble` builds the
prompt by calling `assemble_context(corpus, retrieval, ...)` with the
**original, unfiltered `corpus`** (`agent.py:426`), not `retrieval_corpus`, so
every schema's skills are turned into `SkillView`s and rendered into every
prompt (`context.py:273-276`, `context.py:403-408`), regardless of which
schema was routed. At 69 schemas that is unconditional bloat, not a graceful
degradation.

**Gap B: nothing creates skills.** No curator path constructs a skill.
`Skill`/`SkillFrontmatter` is not in the `Asset` discriminated union
(`schemas.py:403-414`), so it is never indexed by `asset_document`
(`rvgd.py:81-106`, which has no `Skill` branch), never validated by
`validate_corpus`, and never adversaried. `adversary.review()` only checks
`TableAsset` (`adversary.py:73-74`), and `adversary.refute()`, the seam that
would examine a skill's claim, is `raise NotImplementedError(...)`
(`adversary.py:104`). The one skill on disk,
`corpus/beer_factory/skills/routing.md`, is hand-authored and stamped
`status: draft` (line 5) though no adversary ever ran on it. There is no
mechanism that could have run one.

**Governance (P6).** Skills are the only annotation outside the governance
substrate: `SkillFrontmatter` (`schemas.py:388-396`) carries `provenance` but
no `Governance` block, no tiers, and no provenance-aware retrieval. This is
not hypothetical. `corpus/beer_factory/skills/routing.md:30` names
`transaction.CreditCardNumber` in prose ("is PII and is excluded; never select
it"), and that column does carry `governance.excluded: true`
(`corpus/beer_factory/tables/tbl_beer_factory_transaction.yaml:82-84`). The
column is correctly hidden from every governed tool, but the skill's prose
*naming* it is injected verbatim into the SQL prompt (`context.py:403-408`), a
live D6 exclusion breach sitting inside a "helpful gotcha".

**Terminology trap.** "Skill" is overloaded three ways: (1) the corpus
markdown asset described above, (2) a Deep-Agents `SKILL.md` capability, (3) a
generic agent tool. Only (1) ships, and it ships as inert data no component
consults.

**The key realization.** `RuleAsset` (`schemas.py:361-371`) is already ~90%
of "a governed note scoped to any asset." It has `kind` + `scope` (list of
asset ids; empty = global) + `statement` + `confidence` + `audit`; it **is**
in the `Asset` union (`schemas.py:403-414`); it **is** indexed
(`asset_document` returns `.statement` for a `RuleAsset`, `rvgd.py:102-103`);
and it is governance-eligible in the sense that a `Governance` block bolts on
with zero plumbing change. It is a first-class Pydantic model in the same
union as `TableAsset`, not a separate frontmatter-plus-prose parsing path like
`SkillFrontmatter`. `Skill` is the same idea, a governed annotation attached
to something, with every one of those properties stripped off.

**Retrieval today is dual-modal.** BM25 lexical (`rvgd.py`) plus an optional
embedding-cosine channel (`embedding.py`), fused via Reciprocal Rank Fusion
(`embedding.py:53-79`). There is no regex/pattern retrieval mode and no agent
tool to fetch a note's text directly. The routing probe
(`docs/plans/datalake-run.md`) measured, over the 2030-question pool,
embedding-only recall@3 = 0.70, BM25 0.35, and RRF 0.535
(`schema_router.py:143-145`): fusing the weak lexical channel with the strong
embedding channel *drags recall down*. And a single per-schema document built
by concatenating every asset's text (`schema_documents`,
`schema_router.py:86-112`) dilutes the vector each additional asset is folded
into.

**`db` and `schema` are not assets.** `schema` is a `SchemaName` field on
`TableAsset` (`schemas.py:261`), not its own asset type. There is no
`DbAsset`/`SchemaAsset` to attach a note to.

**Direction.** Skills should generalize into notes about any asset
(schema/db/table/column), and retrieval should support three access modes:
semantic similarity, regex/keyword pattern match, and an agent directly
reading a short piece of text.

## Decision

**Delete `skill`. Generalize `RuleAsset` into `NoteAsset`**, one governed
annotation attachable to any asset **or** namespace. A "rule" becomes simply a
note with `enforcement=always`. A parallel, brand-new `NoteAsset` primitive
that leaves `RuleAsset` untouched was considered and rejected: it re-derives
everything `RuleAsset` already is (typed, unioned, indexed, scoped) for no
benefit over generalizing in place.

```python
class NoteKind(str, Enum):
    # from RuleKind: default enforcement = always
    business_rule = "business_rule"
    constraint = "constraint"
    context = "context"
    # from SkillKind: default enforcement = on_match
    routing = "routing"
    gotchas = "gotchas"
    domain_overview = "domain_overview"
    pattern = "pattern"


class Trigger(_Strict):
    kind: Literal["keyword", "regex"]
    value: str


class NoteAsset(_Strict):
    asset_type: Literal["note"] = "note"
    id: str

    # ── Inference (curator writes / gold fills) ──
    kind: NoteKind
    scope: list[str] = Field(default_factory=list)  # asset/namespace ids; empty = global
    title: str | None = None
    statement: str
    triggers: list[Trigger] = Field(default_factory=list)
    enforcement: Literal["always", "on_match"]  # kind sets the default; a validator hard-enforces it
    confidence: Confidence | None = None
    related_notes: list[str] = Field(default_factory=list)
    publication_status: Literal["proposed", "draft", "certified"] = "proposed"
    # serve-visible; Audit.Provenance.status is stripped by for_analyst

    # ── Governance: NEW vs. RuleAsset, closes a latent D6 gap ──
    governance: Governance | None = None

    audit: Audit | None = None
```

### Certification must survive `for_analyst`

`ProvenanceStatus` (`proposed`/`draft`/`certified`) lives in `Provenance`
nested inside `Audit` (`schemas.py:72,152-162,188`). `Corpus.for_analyst()`
nulls `audit` on every asset (`corpus/loader.py:105-107`), and that stripped
view is what retrieval and prompt assembly read (`agent.py:410` for
`retrieve()`, `agent.py:426` for `assemble_context`; the caller passes
`corpus_full.for_analyst()` in, see `api/stack.py:204`). A certification
status kept only in `Audit` is therefore invisible at serve time, and this
ADR's "certified > draft" PIN tiebreak and "uncertified notes get zero
routing-order authority" rule would otherwise have nothing to read.
`NoteAsset.publication_status` above is a separate, serve-visible field in
the Inference tier, so it survives `for_analyst` untouched. The PIN tiebreak
and the zero-authority rule (Honest limit #2) both read `publication_status`,
never `Audit`.

### Scope model: the namespace-vs-asset wrinkle

`db`/`schema` are not assets today, and this ADR does not promote them to
assets; that would ripple through `corpus/schemas.py`, the loader, and the
router for a feature notes do not need. Instead, scope entries are typed by
prefix; asset ids never contain `:`, so the sentinel space is free.

| scope entry | resolves against | meaning |
|---|---|---|
| `tbl_…` / `col_…` / `metric_…` / `join_…` | asset ids (+ derived column ids) | asset reference |
| `schema:beer_factory` | `list_schemas(corpus)` (`schema_router.py:36-38`) | namespace reference |
| `db:main` | the whole (single-DB) lake | all schemas |
| `[]` (empty) | n/a | GLOBAL |

Sentinel prefixes can upgrade to a structured `ScopeTarget` (a discriminated
union of `asset` / `schema` / `db` / `global`) later with no data migration:
the string encoding is a strict subset of what the structured form would
express.

**This sentinel convention is not free as written.** `validate_corpus`
requires every `scope[]` entry to resolve against a real asset id
(`corpus/validate.py:151-153`: `require(scoped, all_ids, a.id,
"rule.scope[]")`, where `all_ids` holds only asset and derived-column ids,
none of them containing `:`). A `schema:beer_factory` or `db:main` scope
entry fires a dangling-ref finding and reddens corpus CI today. `db:main`
also has no backing identity to resolve against: `DataSourceConfig`
(`config.py`) has no `db` field, and its `corpus_pin` defaults to
`beer_factory`, never `main`. Shipping this table requires either teaching
`validate.py`'s `require()` the `schema:`/`db:` prefixes and giving `db:` a
real backing identity in `DataSourceConfig`, or adopting the structured
`ScopeTarget` now instead of sentinel strings (Open Q2).

### Tri-modal retrieval and the pin-vs-blend contract

| Mode | Purpose | Wiring point (file:function) | Fusion rule |
|---|---|---|---|
| **Semantic (own vector)** | recall driver at *retrieval* (post-routing) | `asset_document(NoteAsset)` returns `title + statement`, embedded per-asset in the RVGD retrieval index (`embedding.py:45-50`), kept under a note budget after RRF | **BLEND** into RRF normally. This is the `retrieve()` index built *after* schema routing (`agent.py:410`), so it improves within-scope recall, not schema selection. Each note is its own vector so it does not dilute a table's vector; note bodies stay out of the routing `schema_documents` signal entirely (see Gap-A note below). |
| **Regex/keyword trigger** | deterministic patch for NAMED misses | new `retrieval/triggers.py::fire_triggers(corpus, q)`, unioned into `selected` (`rvgd.py:354-372`) and into `shortlist_schemas` (`schema_router.py:130-180`) | **PIN, never blend.** No lexical trigger score ever enters RRF, respecting the RRF-hurts finding above. Capped (≤3); the tiebreak reads `publication_status` first (certified > draft), then `confidence` (Inference-tier, so it survives `for_analyst` too). |
| **Agent-fetch** | "agent reads a short piece of text" | new read-only, non-licensing tools `read_notes(target)` / `grep_notes(pattern)` added to the list `make_tools` returns (`tools.py:289`) | **Neither.** Not added to `_GOVERNED_TOOLS` (`middleware.py:40`), so `wrap_tool_call`'s dispatch (`middleware.py:219-222`, `if name not in _GOVERNED_TOOLS: return handler(request)`) passes them straight through untouched. Both honor `governance.excluded` via `_is_excluded` (`tools.py:33-35`); reading a note that names table X still requires `inspect_schema(X)` to license X for `run_query`. Safety by topology, not by the tool's own discretion. |

### What this fixes

Gap A (schema-scoped notes become reachable by the routing signal, but **only
via the trigger-PIN mode and the deferred Phase 6 max-pool vector, not the
semantic mode**: routing runs *before* `retrieve()` (`agent.py:402` then `:410`)
and ranks `schema_documents`, which never contains notes, so Phases 1-3 make
notes governed and prompt-visible without yet changing schema selection), Gap B
(notes are a governed asset the curator can produce and the adversary can
eventually vet), P6 (notes inherit the full governance substrate), and the
every-prompt bloat from the unfiltered-corpus bug. Bonus: `RuleAsset` /
`NegativeExampleAsset` carry no `Governance` block today, and because both are
`_Strict` (`extra="forbid"`, `schemas.py:146-149`) a `governance:` key is
*rejected at parse* rather than silently ignored, so D6 exclusion cannot be
authored for a rule at all; adding `governance` to `NoteAsset` makes it work.

**Limit on the PII-leak fix.** A `Governance` block only excludes a note
*wholesale*. `governance.excluded` is asset-level (`_is_excluded`,
`tools.py:33-35`) and nothing scans a note's `statement` text, so a note that
*names* an excluded column in prose (as `routing.md:30` does with
`CreditCardNumber`) is not structurally prevented, and the Mode-C `read_notes` /
`grep_notes` tools add a new surface that returns statement text directly. The
`CreditCardNumber` case is closed only by deleting that line during migration; a
content-scanning validator would be needed for a structural guarantee.

### Honest limits (from the red-team)

1. **Regex-over-the-question triggers are the weakest mode.** They are
   lexical, so they inherit BM25's 0.35-recall vocabulary mismatch; they patch
   *named* misses (an overfitting risk, since every trigger is one more thing
   someone had to have already seen fail) and will not lift the 0.70 recall
   ceiling on unseen questions; and hand-authoring triggers does not scale to
   69 schemas. The real value of regex is `grep_notes` over asset **text**,
   not over the incoming question. Default to keyword-only triggers; defer
   regex-over-question (it needs a `regex`/RE2 dependency plus a per-match
   timeout, because Python's stdlib `re` has no ReDoS timeout).
2. **Uncertified notes (`publication_status` not `certified`) must get zero
   routing-order authority.** A single wrong note bumping a schema's score
   can evict the correct schema from
   `top_k=3` (`DEFAULT_SCHEMA_TOP_K`, `schema_router.py:33`). This must be
   proven, not assumed: run an adversarial-wrong-note test and show recall@3
   does not regress before trusting the PIN in production.
3. **`on_match`/retrieved notes have no injection path today.**
   `assemble_context` never reads a `retrieval.rule_ids`-equivalent list of
   *triggered* (as opposed to *scoped-and-licensed*) notes into the rendered
   prompt (`context.py:290-297` only injects by licensed-scope match). If the
   always-on skill prose is migrated straight to `enforcement=on_match` before
   that path is wired, its content silently stops reaching the model, a
   regression rather than a neutral no-op.
4. **The scope-injection resolver matches `licensed_table_ids` only**
   (`context.py:290-297`). It must extend to `schema:` / `metric_` / `join_` /
   `col_` scopes; as written, a column-scoped note never injects today, and
   neither would a schema-scoped or metric-scoped one.
5. **`adversary.refute()` is `NotImplementedError`** (`adversary.py:104`). A
   certified PIN can only rest on structural checks (`review()`,
   `adversary.py:52-93`) until the LLM refutation seam actually lands. "A
   human signed off" (D6) is the only real gate for now, not "an adversary
   tried and failed to break it."
6. **`grep_notes` needs the same ReDoS bound as limit #1.** An agent-supplied
   pattern reaches `re` the same way a question-side trigger would, so
   `grep_notes` must bound its regex (RE2, or a per-match timeout) and cap
   its output size before it ships in Phase 3; this is the same mitigation
   limit #1 already requires on the question side, not a separate one. The
   structural fix for the PII-prose leak above is a content-scanning
   validator over `NoteAsset.statement` for excluded identifiers, not just
   deleting the one known offending line during migration.

## Consequences

**Positive**
- Closes Gap A and Gap B in one primitive instead of two parallel fixes.
- Governance upgrade: `NoteAsset` (and by extension every rule) gets a real
  `Governance` block, so a whole note can be D6-excluded (impossible today, since
  `extra="forbid"` rejects a `governance` key on `RuleAsset`). This does not by
  itself stop an excluded identifier from being *named* in a note's prose (see
  the Gap-A limit above); that needs a content-scanning validator or manual
  removal.
- Deletes a whole ungoverned surface (`SkillFrontmatter`, the
  `corpus/<schema>/skills/*.md` convention, the loader's separate `skills`
  glob) rather than bolting governance onto it in place.
- Delivers all three requested retrieval modes, plus notes attachable to any
  asset *or* namespace; schema/db-scoped guidance was previously
  unrepresentable.

**Negative / costs**
- Rename churn is real, not cosmetic: this repo's own backend `/skills` route +
  presenter (`api/app.py:296-299`, `viz/presenter.py`) and the sibling
  `governed-bi-ui` repo's `/skills` surface both migrate lockstep, or they drift.
- The injection-resolver extension (Honest limits #4) and the `on_match`
  wiring (#3) are load-bearing, not incidental. Skip either and the migrated
  content goes silently missing from prompts.
- If regex-over-question is ever built (deferred per #1), it opens a ReDoS
  surface that must be budgeted for (RE2 or a timeout) before it ships.
- The adversary seam (#5) stays unbuilt; "certified" for a note still means "a
  human looked at it," not "an independent model tried to break it."

## Alternatives considered

- **New first-class `NoteAsset` alongside untouched `RuleAsset`.** Rejected;
  it re-derives everything `RuleAsset` already is. The one argument for keeping
  them separate, that "one type can't hold both always-on and triggered
  injection semantics," dissolves once `enforcement` is a field rather than a
  type distinction.
- **Retrieval-index-centric design (a dedicated annotation index, separate
  from asset retrieval).** Not adopted wholesale, but its pin-vs-blend rigor
  (never let a lexical trigger score enter RRF) was grafted into the Decision
  above.
- **Agent-tool-centric design only (Mode C tools, no semantic/regex modes).**
  Its tools were grafted in as Mode C, but it was rejected as the *sole*
  design: its only routing lever is at the schema-pick stage and cannot move
  the recall ceiling the datalake probe exposed.
- **Keep `skill` as-is and just wire it into routing.** Rejected; it leaves
  the asset permanently ungoverned and uncreatable by the curator, which is
  Gap B, not just Gap A.
- **"Notes are just more description fields on existing assets."** Rejected; a
  single untyped field can't be independently governed, excluded, triggered,
  or scoped to a namespace the way a first-class asset can.

## Migration (phased; each phase independently shippable)

1. **Rename, no new retrieval behavior.** `RuleAsset` → `NoteAsset`
   (`asset_type: rule → note`, id prefix `rule_` → `note_`, directory
   `rules/` → `notes/`); add the `Governance` block (closes D6 for rules
   standalone) and `enforcement`, with a validator that hard-enforces
   `kind → enforcement`. Delete the whole skill path: `SkillFrontmatter`,
   `SkillKind`, the frontmatter parser, the loader's skills glob and
   `Corpus.skills` (`loader.py:87`), `SkillView` + its render block
   (`context.py:273-276,403-408`), `dump_skill`, the `schema_router` skills
   filter (`schema_router.py:355-360`), the CLI's `n_skills` count, the
   `ids.py` skill id pattern (`ids.py:30,43`), the `serialize` skills-write
   branch (`serialize.py:70,95-99`), the `corpus`/`viz` `__init__` skill
   re-exports, and (this lives in *this* repo, not just the UI) the backend HTTP
   surface: `GET /skills` + `SkillResponse` (`api/app.py:296-299`,
   `api/schemas.py:281`), `HealthResponse.n_skills` (`api/schemas.py:45`),
   `AssetTypeFilter` (`api/schemas.py:270`, `'rule'` → `'note'`), and
   `presenter.SkillView` / `skill_views` (`viz/presenter.py:121-131,378,519-527`).
   Migrate the one real skill, `routing.md`, into granular notes; most of it
   dedups into
   `rule_boolean_flags`, a `Column.reliability.note`, and
   `governance.excluded` (the `CreditCardNumber` line disappears entirely,
   since exclusion already covers it). Also teach `validate_corpus`'s
   `require()` (`corpus/validate.py:151-153`) the `schema:`/`db:` scope
   sentinels so a schema- or db-scoped note does not redden CI as a
   dangling reference, and give `db:` a real backing identity in
   `DataSourceConfig` (`config.py`, which has no `db` field today;
   `corpus_pin` defaults to `beer_factory`, never `main`); absent that,
   adopt the structured `ScopeTarget` (Open Q2) instead of sentinel strings
   before this phase ships.
2. **Wire injection for real.** Extend the scope-injection resolver
   (`context.py:290-297`) to `schema:` / `metric_` / `join_` / `col_`; render
   triggered/`on_match` notes into the prompt. Add a no-EX-regression eval arm
   and a prompt-size CI cap before relying on this for anything migrated in
   Phase 1.
3. **Agent-fetch tools.** Add `read_notes` / `grep_notes` to `make_tools`
   (`tools.py:289`); delivers the "regex + agent-reads-text" half of the
   original ask without touching the scoring path at all.
4. **Trigger PIN.** Add `Trigger` + `retrieval/triggers.py` + shortlist-level
   trigger PIN (keyword-only, capped, outside RRF); measure trigger coverage
   on a held-out split before trusting it on the full 69 schemas. This
   phase must not ship live PIN authority (the certified/draft tiebreak
   actually deciding a schema pick) before Phase 5's `publication_status`
   gate lands; until then, treat every PIN as dev-only and unranked.
5. **Certified-gates-PIN.** Wire dev-graduation (draft usable in dev,
   certified required in prod) as separate, comparable eval arms. This is
   the gate Phase 4's tiebreak depends on (see above).
6. **Second per-schema vector, only if still needed.** If schema-routing
   recall still caps EX after Phases 1-4, add a max-pooled second per-schema
   note vector, with count-bias mitigation; notes stay excluded from
   `schema_documents`' concatenation regardless.
7. **Land `adversary.refute()` for notes.** A note is a claim, the natural
   first client for the still-unbuilt refutation seam (`adversary.py:96-104`),
   before "certified" PIN authority is trusted in production.

Phases 1-3 deliver notes as a governed, creatable asset, the semantic and
agent-fetch retrieval modes, and the prompt-bloat fix; the trigger-PIN mode
and, with it, Gap-A routing-reachability come in Phase 4 (plus the deferred
Phase 6 max-pool vector); Phases 5-7 are further hardening and
scale-proving.

## Open questions (for the maintainer)

1. **Rename churn vs. low-churn.** Full `rule` → `note` rename (breaks the
   UI's `/skills` surface, forces lockstep migration) vs. keeping
   `asset_type`/dir as `rule` and renaming only in vocabulary/docs.
   *Recommendation: bold rename, since this is a greenfield project with no
   users (AGENTS.md).*
2. **Prefix sentinels vs. a structured `ScopeTarget` now.** *Recommendation:
   sentinels now; upgrade later with no migration required.*
3. **Build or defer regex-over-question.** *Recommendation: defer. Keyword
   triggers only; `grep_notes` already covers regex-over-text without taking
   on the ReDoS-dependency question.*
4. **Global always-note budget.** Needs a CI cap (max count + max chars) on
   `scope=[]` notes so the every-prompt bloat this ADR fixes does not return,
   just relabeled as rules instead of skills.
5. **PIN authority gate.** Confirm draft-in-dev / certified-in-prod as the
   default before Phase 5 ships.
6. **[C2] Three fields instead of one derived one?** Should `kind`,
   activation (`always`/`on_match`), and normative force
   (`must_honour`/`advisory`) be three separate fields, rather than
   `enforcement` being derived from `kind` by a validator? As written,
   `enforcement` is effectively `kind` relabeled, and it blocks combinations
   the model should be able to express, for example a keyword-triggered
   `business_rule` (`on_match` plus `must_honour`).
7. **[H1] Conflict/precedence rule for competing `always` notes.** No rule
   exists today for two `always` notes (or an `always` and an `on_match`)
   on the same scope that disagree. Needs a precedence rule before Phase 2
   renders more than one into the same prompt.
