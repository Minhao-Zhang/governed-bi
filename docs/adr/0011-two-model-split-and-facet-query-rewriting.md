# ADR 0011 — Two models, and a query per facet

Status: Accepted (2026-08-04). Amends ADR 0005 §2.3, whose facet table gives `schema` and
`example` their queries from the raw question and marks them as calling no model. All five
facets now call one.

## Context

Two changes landed in one commit and are recorded together, because the second is what makes
the first necessary. Per-facet query rewriting puts five model calls in front of every turn's
retrieval; the two-model split is what keeps that affordable. Neither is worth reading alone.

**What forced the rewriting.** A user asks *"what is the average star rating for restaurants in
this area"*. A schema summary in the corpus reads *"stores basic information about
restaurants"*. Those two strings are not close, lexically or semantically — BM25 is left scoring
function words, and an embedder is being asked to match a question against a catalogue entry,
which are different genres of text. Until this change **every facet searched with the raw
question**: five facets looking for five different kinds of object, all sending the same string
to the index. `register/facets.py` already says the facets differ in what they retrieve over;
nothing made them differ in what they asked for.

**What was already declared and absent.** ADR 0005 §2.3 sources three facets' queries from "LLM
extraction", and `register/facets.py` has carried a `Channel.extraction` for it since. Nothing
implemented it, so it reported `failed` on every turn of every run — the same shape as the
semantic channel, which was declared, fully built, and unreached until an embedder was wired
into `graph_app` earlier today. With semantic alive, `extraction` was the last entry left in
`failed_channels`.

**What forced the split.** Before this, one model served the whole turn. The guard's scope gate
was the only call standing in front of retrieval, and one short call was cheap enough that
nobody had to think about which model answered it. Six is a different question, and it is a
latency question before it is a cost question: these calls sit in the interval between a user
pressing enter and anything at all appearing.

## The decisions

### 1. `Session.utility_model`, named for the role and not for the tier

A second model is configured by `GOVERNED_BI_UTILITY_MODEL`, with
`GOVERNED_BI_UTILITY_MODEL_EFFORT` beside it, built by `api/graph_app.py::_utility_model` and
carried on `Session` as `utility_model`. Two callers read it: the guard's `g_bi_scope` gate
(`serve/nodes/guard.py`) and the five facet query rewriters
(`serve/nodes/facets.py::_rewritten_query`). Six calls, each producing one word or one line, and
five of them run concurrently in a single fan-out super-step before any retrieval happens.

The name is the decision. `GOVERNED_BI_WEAK_MODEL` was the obvious spelling and it encodes a
*relative capability claim* — this year's weak model is next year's default — so the variable
would start lying the moment the models moved. It also invites the wrong question. "Weak" reads
as a concession, and a reader meeting it asks why we do not just use the good model everywhere;
"utility" states the actual reason, which is that a one-word classification and a one-line
rewrite are utility work whose latency the user experiences directly and whose quality ceiling
is low. A fast model is the argument. A bad one is not.

`GOVERNED_BI_UTILITY_MODEL_EFFORT` is separate from `GOVERNED_BI_MODEL_EFFORT` for the same
reason: a yes/no classification does not need a reasoning budget, and spending one there would
undo the point of the split on the calls that are most exposed to it.

### 2. The fallback resolves in one place

Unset means "share the agent's model", so a one-model deployment is unchanged and nothing
silently skips. That fallback is written **once**, in `Session.configurable`, which puts
`self.utility_model or self.agent_model` on the config under `utility_model`. Six call sites
each writing their own `or` is six places for it to be written differently — and the difference
that matters is not a crash but a divergence, one caller falling back where another did not, on
a field the record then reports as a single value.

One redundancy survives: `guard_node` still reads `cfg.get("utility_model") or
cfg.get("agent_model")`. It cannot currently differ from the resolved value, and it is kept as
belt-and-braces for a caller that hand-builds a config, but it is a second copy of the rule and
this section is what would have to change if it ever disagrees.

### 3. `_utility_model` does not set `use_responses_api`

That is the only structural difference from how the agent model is built, and it is deliberate.
The agent sets it because it **binds tools**, and the provider refuses tools alongside
`reasoning_effort` on chat completions, saying so in its own words. Nothing the utility model
does binds a tool — it answers one word, or writes one line of search text — so requiring the
heavier endpoint here would be carrying a constraint from a caller that does not exist.

### 4. `llm_utility_model` is recorded even when it falls back

This is the part of the change most likely to be undone by someone tidying up, so the reason is
stated at length. `register/knobs.py` declares `llm_utility_model` as `Role.comparability`, and
`session.py` writes it from `utility_model or agent_model` — the **resolved** model, not the
configured one — so a run that shares one model records the shared model's id rather than a
blank.

Recording the absence would be the natural-looking choice and it is the defect. "Shared one
model" and "split them" are two different treatments of the same six calls, and a blank in the
shared case would make the two runs agree on every hashed field, so comparability would clear
exactly the pair an experiment was built to separate. That is not hypothetical: it is the
`llm_reasoning_effort` incident this register already records. Two v1 ladders differed **only**
in reasoning effort, it was recorded nowhere, comparability cleared the pair — and effort moved
the baseline arm **+2.5pp against a 2.3pp detection threshold** <!-- [retired]: sizes void, the comparability argument is not; register/citations.py -->. The utility model has a larger
lever than effort did, because what these six calls produce is *what gets retrieved at all*: a
cheaper rewriter that phrases the schema query worse moves routing recall, and routing recall
moves everything after it.

One boundary, stated because it is easy to read as a bug: the knob is written inside the branch
that requires an `agent_model`, so a deployment with a utility model and no agent model records
neither. That configuration has no answering model at all, which `/capabilities` reports
separately, and a knob describing which model performed work in a run that performed none would
be the same absence-as-a-value defect wearing the opposite sign.

### 5. Five prompts, one per facet — and the LLM implementation chosen over the deterministic one

`register/prompts.py` gains `facet_schema_query`, `facet_term_query`, `facet_metric_query`,
`facet_entity_query` and `facet_example_query`, plus `FACET_QUERY_PROMPTS` mapping a `Stage`
value to a prompt name. A mapping rather than an `f"{stage}_query"` convention: a convention
returns nothing for a stage nobody wrote a prompt for, and the registry's own coherence check
plus `tests/serve/test_facet_query_rewrite.py` catch the mapping instead.

Five prompts rather than one parameterised prompt is the whole reason `register/prompts.py`
exists. Each facet searches a different kind of object and each will be tuned against a
different number; independent versioning is what lets a variant of one be compared without
moving the other four, and a single prompt with the facet interpolated would make that
impossible. Four extra registry entries is the price.

**How this decision was made, recorded as a fact about the decision.**
`docs/plans/context-engineering-2026-08-04.md` states the choice as the maintainer's, between
deterministic string composition and a model call per facet, and argues for the deterministic
arm first on the grounds that it is measurable first — a pure function of question and facet,
no prompt, no budget, and it establishes whether the *shape* of the query matters before paying
to learn whether the *model* does. The maintainer chose the LLM implementation with that
tradeoff stated. Their framing of what a rewrite is for stands either way: *"a deterministic way
of aggregating different strings together to make them more semantically close to the thing we
are trying to search."* No measurement has been taken. The risk that carries is specific and
worth naming: the semantic channel came alive earlier the same day, so the two changes ship in
the same window and **their effects on retrieval cannot be attributed separately** from a
before/after comparison. The plan document has the fuller argument and the measurement that
would settle it.

### 6. A fallback is not a run

`_rewritten_query` adds `Channel.extraction` to the facet's `ran` set **only when a rewrite
actually came back**. A model that raises, a reply that is empty or whitespace, and a
deployment with no utility model configured all fall back to the raw question, and the channel
then reports `failed`.

That is the same rule the rest of `_channels_for` follows and it is the rule this whole field
exists for. ADR 0005 §2.3 puts it plainly: a run where extraction failed on every turn
completes normally, grades normally, and *is* v1's single-pass retrieval wearing v2's name. A
fallback that reported as a run would produce exactly that, silently, with the degradation gate
reading a green field.

**The declaration table has not caught up, and that limits what this reports.**
`_channels_for` judges an observation against `expected_channel_state`, which routes through
`FACET_EXTRACTS` — still `{term, metric, entity}`, ADR 0005 §2.3's original three. So on
`facet_schema` and `facet_example` the extraction channel reports `not_configured` whichever
way the rewrite went: a successful rewrite is discarded, and so is a failed one. Those two
facets do call the model, so the table is now wrong about them, which by the register's own
vocabulary is an `extra_channel` anomaly that nothing can observe. Not fixed here, because
widening `FACET_EXTRACTS` changes what `_run_facet` does on the no-index path as well, and that
is a second change on a path this one does not touch. Recorded so the next reader does not
conclude from a `not_configured` that those facets kept searching with the raw question.

### 7. The rewrite is embedded, and `queries` publishes what was searched

`_query_vector` gained the job of embedding the rewrite. The turn's cached `query_vector` is the
*raw question's*, computed once in `accept`, and it remains the right thing to score with when
nothing rewrote the question. It is the wrong thing the moment something did: a facet that
restates the question in the vocabulary of the thing it is searching for, and then scores with
the original question's vector, has paid for the rewrite and thrown away the half that motivated
it. A rewrite reaching only BM25 would miss the point of rewriting.

The cost is up to five extra embedding calls per turn. They are concurrent with the facets that
issue them, they are small, and each one is cheap against the model call that produced the text
being embedded. An embedder failure falls back to the question's vector rather than dropping the
channel, so the worst case is the behaviour of yesterday.

`facets["<stage>"]["queries"]` now carries the text that actually went to the index rather than
the question the user asked. That is what makes the two cases distinguishable in a trace: a
facet that rewrote and a facet that fell back publish different strings, and a reader can see
which happened without inferring it from the channel map.

## Consequences

- **Latency and token cost per turn both rise**: one gate call, five rewrite calls, and up to
  five extra embeddings. The five rewrites are a single LangGraph fan-out super-step, so their
  wall-clock is roughly one call while their cost is five — which is what makes a fast model the
  right one to spend there, and what makes decision 1's naming argument a practical one rather
  than a stylistic one.
- **`prompt_set_hash` moved**, because the registry grew from two prompts to seven and the hash
  is over the whole set. Every run recorded before this commit is a comparison across a
  different prompt set, and the hash now says so instead of letting the pair clear. That is the
  registry doing the job it was built for, not a problem to work around.
- **The observable that says this is working**: with an embedder and a utility model both
  configured, a healthy turn should report **no failed channels at all**. Before today every
  facet failed `semantic`; after the embedder landed, the three extracting facets still failed
  `extraction`; that entry should now be gone. A `failed` after this change means the utility
  model is unconfigured, erroring, or returning nothing — three causes worth telling apart, and
  all three currently present as the same value.
- **ADR 0005 §2.3's facet table is amended** for `schema` and `example`, which now call a model
  and no longer search with the raw question. The table's other claims stand. What is *not* yet
  true of any facet is §2.3's "each extracted phrase is its own query": a rewriter returns one
  string, so `queries` holds one element and `_MAX_QUERIES` has nothing to bound.
- **The knob register now names one model three times.** `facet_model` and `rewrite_model` were
  declared for the two call sites that are now one, and nothing writes either. `llm_utility_model`
  is the one that is written and the one a gate can read; the other two should be retired or
  given producers, and leaving them declared-but-unwritten is the shape this register exists to
  argue against.
