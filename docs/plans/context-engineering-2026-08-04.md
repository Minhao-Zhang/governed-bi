# Context engineering: what the prompt should carry, 2026-08-04

Working notes from the THOUGHTS.md pass. Items 2, 3, 7 and 8 of that list were built and are
recorded in code and in ADR 0010; this file holds the two that are **design decisions not yet
made**, plus the measurements that should decide them. Written down because the measurements were
expensive to get and the decisions are the maintainer's.

## What was measured on the way

Four facts, each from a live streamed turn against the gold semantic layer (57 schemas, 8035
asset files, 13 968 indexed summaries):

**M1 — the semantic channel had never run, and one facet had no channel at all.** Every piece was
built (`Embedder` port, OpenAI adapter, `UnifiedIndex.vectors`, `build_index(embedder=...)`) and
nothing passed an embedder, so `_channels_for` marked `semantic` **failed** on every facet of
every turn. Worse, `facets.py`'s own comment explained the failure as *"there is no `Embedder`
adapter in `src/` to produce one"* — stale, the adapter exists — while the real cause was that
**pass one had no vector-scoring code at all**; only pass two did. And
`FACET_CHANNELS[facet_example]` is `{semantic}` alone, so the past-SQL-example facet retrieved
**nothing, ever**. That is the facet the maintainer singled out: *"providing past SQL example to
answer the more current question is very helpful, and in this case the embedding model would be
able to retrieve those much better than BM25."* It could not retrieve them at all. Fixed.

**M2 — the query vector could not reach the streamed path.** `Session.configurable(question=…)`
adds a `query_vector`, which serves callers who build one config per question (`eval/harness.py`,
`POST /chat`). `graph_app.make_graph` binds the config **once at load time** with no question, so
on the streamed path — now the only real one — the key was never present. Even a fully populated
vector index would have reported `semantic: failed`. `accept` now computes it into state and the
facets read state first, config second. Fixed.

**M3 — the delivered context is 8.5–12.5 KB and it all goes to the SQL agent.** `assemble` emitted
`n_chars: 8588` and `12553` on two ordinary questions. The knob above it,
`context_budget_chars`, defaults to **80 000** — an order of magnitude more headroom than the
turns are using, so the budget is not what is shaping the prompt. Nothing is.

**M4 — the agent already narrates.** The final message of a live turn reads *"The largest
queryable table is `authors.PaperAuthor`, with **2,315,574 rows**."* The maintainer asked for *"the
table alongside an explanation"*; the explanation was the half that already existed, and the
**rows** were reachable only by parsing a `ToolMessage`'s JSON. A second narrator agent would have
duplicated free work. `answer.result_table` was added instead. Fixed.

## Open decision 1 — per-facet query rewriting

**The ask.** Treat each facet as its own agent that rewrites the user's question into a search
term appropriate to that channel: *"for the schema level routing, we could ask what kind of tables
and schema would help us to resolve the question of blah blah blah. And for the metric is that,
what kind of metric is associated with the following sentence?"* Two implementations named:
deterministic string composition, or an LLM call per facet.

**What is already there.** `register/facets.py` declares `FACET_EXTRACTS` — the facets whose
queries are supposed to come from model extraction rather than the raw question — and an
`extraction` channel that reports **failed on every turn** because nothing implements it. So this
is not a new concept in the design; it is a declared channel with no producer, the same shape as
the semantic channel before M1.

**The choice, and what should decide it.** Five extra model calls per turn is the cost, on the
critical path, before any retrieval. The measurement that settles it is cheap and does not exist
yet: hold the corpus fixed, run the BIRD question set through (a) raw question, (b) deterministic
composition, (c) LLM rewrite, and compare **schema-routing recall@k** — not EX. Routing recall is
the metric because that is what a facet query is *for*, and because the retrieval-ceiling work
already established BM25@3 ≈ 0.35 as the bottleneck. If deterministic composition moves recall as
much as an LLM rewrite, it wins on latency and determinism outright.

Do the deterministic arm first regardless: it is a pure function of the question and the facet, it
needs no prompt and no budget, and it establishes whether the *shape* of the query matters before
paying to find out whether the *model* does.

If the LLM arm is built, each rewriter is a registered prompt (`register/prompts.py`) so
`prompt_set_hash` covers it — that registry now exists for exactly this reason — and the
`extraction` channel is marked `ran` only where a rewrite actually happened, never where the raw
question was used as a fallback. `_channels_for`'s history is the argument: a fallback that reports
as a run is how an arm quietly becomes v1's single-pass retrieval.

## Open decision 2 — shrinking the prompt

**The ask.** *"The prompt in the end is a giant blob, which is very bad for the model to process.
We want to shrink it to a place that we only contain relatively good size of necessary information
before we even send it to the final SQL writing agent."* And the framing that matters more than
the ask: *"we are not cleverly determining what context goes into the prompt and what context goes
out of the prompt at this point."*

**Why a smaller budget is the wrong fix.** `context_budget_chars` is 80 000 and turns are using
8.5–12.5 K (M3), so lowering it changes nothing until it starts truncating — at which point it
truncates by *position in a rendered string*, which is the least informed possible ranking. The
budget is a backstop against a pathological corpus, not a curation policy, and using it as one
would make the prompt smaller without making it better.

**What is actually uncurated.** `resolve` pulled in **66–75** assets by reference closure on the
turns measured, and `connect` licensed 5–8 tables. Everything the closure reached is rendered.
Reference closure is the right rule for *correctness* — a column whose table is absent is
unusable — but it is not a relevance rule, and nothing downstream distinguishes "this asset is
here because the question is about it" from "this asset is here because something else needed
it". `retrieved.attributions` and `retrieved.pulled_in` already record which is which. **That
distinction is the curation policy, and it is already computed and thrown away at render time.**

So the first move is not a summariser and not a smaller budget. It is to render hits and pulled-in
assets differently — full body for what the question hit, identifier and type only for what the
closure dragged in — and measure EX. That is a change to `assemble` alone, it costs nothing at
runtime, and it uses a fact the pipeline already has.

Two further moves, in order, each gated on the previous one not being enough:

1. **Drop pulled-in assets no join path touches.** `connect` knows the Steiner points; an asset
   that is neither a hit, nor licensed, nor on a join path is in the prompt for no stated reason.
2. **A per-asset relevance score.** Now that the semantic channel runs (M1), every hit has a
   cosine against the question. Ranking the rendered set by it is a one-line change to
   `assemble`'s ordering and gives a principled place to cut.

Note what none of these is: an LLM summarisation pass over the context. That adds a call, adds a
prompt, and adds a place where the delivered artifact stops being the governed artifact —
`delivery_hash` covers what was rendered, and a model rewriting it in between would make the hash
attest to something the model never saw. If summarisation is wanted, it belongs *before* the
context block is hashed, not after.

## Not done, and deliberately

**LanceDB.** Researched against current docs (`lancedb.connect`, `db.create_table(schema=…)`, the
embedding registry with `openai` / `bedrock-text` providers, `$var:` secret indirection,
`TextEmbeddingFunction` for a custom adapter, and hybrid search over an inverted index). It is the
right destination and the seam is already a `MutableMapping` (`build_index(vector_cache=…)`), so
swapping the store touches one file. But what LanceDB buys over the file-backed cache now in place
is **approximate nearest-neighbour search at scale**, and the semantic channel does not search a
vector store — `semantic_search` scores an already-narrowed candidate set by exact cosine over an
in-memory dict. Introducing a vector database as a key-value cache would be new machinery
answering a question nobody has asked. The question that *was* asked — why is there no embedding
model — is answered by wiring the embedder. Revisit when ANN or hybrid retrieval is the
requirement, or when the corpus outgrows an in-memory vector dict.

**One schema instead of a shortlist.** *"After all these are done, we need to figure out a schema
that we want to run the query upon on."* The engine deliberately does not: `connect` partitions
terminals into components and licenses **every** component that connects, and
`route_retrieve.py:207-214` records the measurement — over 1351 BIRD questions the router
shortlisted the gold schema 823 times (recall@3 0.609) and a single-component pick reached it only
**0.442**, with every one of the 226 losses ranking 2nd or 3rd. *"No pick rule can beat recall@1,
because picking is the thing that throws the other candidates away."* Narrowing to one schema is
therefore a ~17pp reachability cost, knowingly paid, in exchange for a simpler prompt. That is a
real trade and the maintainer's to make — it is not an oversight to be fixed.
