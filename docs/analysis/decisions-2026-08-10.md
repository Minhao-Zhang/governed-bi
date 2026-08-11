# Decisions taken while working the 2026-08-10 audit

Every call made without asking, with the reasoning and what would reverse it. Findings live in
[audit-2026-08-10.md](audit-2026-08-10.md); this page is only the *choices*, so that a reviewer can
disagree with one without re-deriving it.

## D-1 — A dead embedder drops the semantic channel rather than degrading to another text's vector

Audit I7. Two existing tests asserted the opposite, on the reasoning that "a dead embedder must
degrade, not drop the semantic channel".

**Chosen:** return `(None, ChannelState.failed)`.

**Why:** the substituted vector is the *raw question's*, while the lexical channel searched the
facet's rewrite. That is not a weaker measurement of the same query; it is a measurement of a
different one, blended into a single `score` and recorded as `ran`.

**Rejected:** falling back on *both* channels, which would keep a real hybrid of a real query. It
gives one facet two possible search texts decided by a provider error, and `queries` would then have
to carry which — a second hidden path in the scoring loop, to save a channel on the rare turn a
retried embed still fails.

**Reverses if:** the record shows `Anomaly.failed` at a rate high enough that losing the channel
costs measurable recall. That is now visible, which it was not before.

## D-2 — The mixed-vector-space check is a probe, not a knob

Audit I9. `_SAME_SPACE_COSINE = 0.99` is a module constant.

**Why:** a knob is a comparability dial — a value an arm might legitimately want set differently,
recorded so two runs can be told apart. This one only decides whether a corrupt store raises, so an
arm choosing its own value is an arm choosing not to be checked. The margin is wide both ways: one
model embedding one text twice agrees to better than 0.9999, two models occupy unrelated subspaces.

**Superseded by D-8, D-10, D-14 and D-15 for *where* the check runs.** What survives of this decision is only
the constant: three probes on the bootstrap path, which reliably catch a whole-store move and — as
review pointed out — catch a 1% partial re-embed 3% of the time. The canary, not the probes, is the
defence.

## D-3 — `semantic_scale_ceiling` replaces the min-max, and RRF is rejected

Audit I1. The plan said reciprocal-rank fusion.

**Why RRF is rejected:** it is immune to scale but purely ordinal, so the top-ranked document scores
`1/(k+1)` — the maximum a channel can award — however weak the match. That is the I1 defect
expressed in ranks, not a fix for it.

**Why not simply delete the min-max:** measured first. Lexical top hit median 0.838 against semantic
top hit median 0.316, so raw fusion at 0.5/0.5 drops the semantic channel to ~27% effective weight,
which is the `max(lexical, semantic)` defect returning.

**Chosen:** a fixed, query-independent ceiling, and only for cosine — `raw/(raw+k)` is already in
`[0,1)`, the absolute scale the sealed contract prescribes. Fitted at 0.6 for
`text-embedding-3-large`.

**Cost accepted:** at 0.5/0.5 the semantic channel now contributes less than min-max gave it, because
min-max asserted that every query's best cosine was maximal evidence. If that costs recall, the
honest response is to re-fit the declared weights, **not** to reinstate a per-query normaliser.

**Measured after the fact:** it costs nothing. `all_gold_tables_licensed` +0.0008, paired shortlist
hit −0.0007 (p=1.0000, discordant=17/1351, MDE=0.0086). See the Phase 6 table in the audit page. The
re-fit of `w_semantic` is therefore *not* needed and was not done.

## D-4 — `coverage` gets a function-word list, not a document-frequency ceiling

Audit I4.

**Why not a df ceiling** (elegant, needs no English): measured on corpus `86ed1dbf`, the function
words spread from 0.26 (`is`, `on`) to 0.76 (`the`), and a schema corpus's real content words —
`name`, `id`, `count` — sit in the same band. Any ceiling low enough to catch `on` also catches
those.

**Cost accepted:** the list is English-only, and in-corpus coverage falls 3.5pp. That fall is the
number to watch; a larger one would mean the list is eating content words.

## D-5 — The scale moved into `combine_channels` rather than staying at the two call sites

**Why:** a normaliser needing a population depends on *which candidates were scored*, and the two
retrieval passes score different sets by design — pass two is restricted to the selected schemas. So
one asset carried two different scores into `apply_budgets`' single global sort. A fixed ceiling
needs no population, so folding it in makes the two passes unable to disagree.

## D-6 — `test_pass_two_and_context.py` was split rather than condensed

It crossed the 1,000-line hard cap for the second time. The channel-scale tests moved to
`tests/serve/test_channel_scale.py`, which is a topical seam rather than an arbitrary cut: everything
in it turns on one question that has now been answered three different ways.

## D-7 — I10 was recorded, then fixed (see D-22)

`FUSE_WEIGHTS` and `SEMANTIC_CEILING` were read from `knob_default` at *import*, so three declared
comparability knobs could not be set per run and the record reported a value the turn could not have
used. Deferred here because it meant threading state into a function called once per candidate
document; done in D-22, where the cost turned out to be a value object rather than a per-document
lookup.

## D-8 — The vector-space check became a canary row

Review found `_refuse_a_mixed_vector_space` sitting behind `if missing:`, blind to the likeliest form
of the incident: a repoint with an unchanged corpus produces no misses at all.

**Chosen:** a fixed canary text, embedded once per store per process and compared against its stored
row. **Where it runs is D-10, which was written in the same commit as this and contradicted it** —
this decision said "on every build, minting on cold ones too", and a sealed contract forbids that.
Read D-10 for what shipped; the two should have been one entry.

**Cost accepted:** one small embed per store per process, on stores that already held rows.

**Not chosen:** verifying at query time. It would catch a mid-process repoint, which the canary
cannot, but query vectors are never cached so there is nothing to compare against without adding a
probe to every turn. Recorded as a stated limit instead.

## D-9 — The store's verified-space memo lives on the store, not in module state

`MEMORY_URI` gives every ephemeral store the same uri, so a process-wide key would let one in-memory
store answer for another — and answering "already verified" for a store nobody looked at is the exact
failure the check exists to prevent.

## D-10 — The canary is gated on `opened_with`, because a sealed contract said so

`tests/model/test_embedder_contract.py` asserts `large.embedded == [summary]` — a build embeds
exactly the texts it needs — and it is sealed. The first canary minted on cold builds and broke it.

The contract is right and forced the gate onto `opened_with`. **The justification I gave for that
being sufficient was wrong, and is retracted here rather than left standing for D-14 to contradict.**
It read: rows this process wrote came from an embedder it holds, and another embedder's rows are under
another key, so a cold store is safe. Circular — the incident is a repoint behind an unchanged id.

**Reverses if:** a way appears for foreign rows to enter a store this process created. **It did**, in
a five-line script, the same day. See D-14 and D-15.

## D-11 — `tests/` joined the citation gate with two named exemptions, not a tier

The gate never scanned `tests/`, so a retired figure quoted in a test docstring was invisible.
Pointing it there found **eight**. Six were marked `[retired]` or rewritten with measured
replacements. The two in sealed contract files cannot be edited at all.

**Chosen:** add `tests` to `STRICT_ROOTS` and name the two sealed files in `GREP_EXEMPT_PATHS`.

**Rejected:** a non-fatal `ARCHIVE_ROOTS` tier — which I tried first, and
`tests/conformance/test_register_closure.py::test_citation_gate_has_no_archive_tier` forbids one.
That test is right for a reason its own docstring does not give: an exemption has to be justified one
path at a time and a tier does not, and a tier would have swallowed the other six along with them.

## D-12 — The RAM fixes are pinned by mechanism, not by measurement

`keys()` is tested by making the full read *raise*; `_replace` by asserting the connection object
changed. A commit-charge or timing assertion in a test suite is a flake, and in both cases the
mechanism is the fix. The measured numbers live in the docstrings and in the audit page.

## D-13 — P3's fix is kept and its diagnosis is withdrawn

Review established that `build_index` constructs a fresh `VectorStore` before every `load_from`, so
`_replace` runs once per store and the retained-store loop I measured is not a path in this tree. On
the real path the reconnect is worth 0.226 → 0.201 MB per build, inside the noise, against the ~47 GB
over 1,351 questions I published.

**Chosen:** keep the line, withdraw the claim, and record the scope in the P-table as
`[scope corrected]` rather than deleting the row.

**Why keep it:** the mechanism reproduces at 43.9 MB per call on a retained store, and the day
something retains one this is where it bites. **Why not keep the claim:** the reported symptom — RAM
"absurdly high" — is therefore **still unexplained**, and the largest allocation actually on the path
is P2, `load_from`'s 6.2x amplification. Leaving a settled-looking wrong cause in AGENTS.md would have
stopped the next person looking.

## D-14 — A cold store gets one probe, and one case stays open by construction

The `opened_with` gate of D-10 reopened I9: cold store, build with A, repoint the gateway behind the
same id, build again where some rows hit. Review demonstrated two spaces in one index with nothing
raised. My justification — "a row written by another embedder in this process is under a different
key" — was circular, since the incident *is* an unchanged id.

**Chosen:** probe one reused row on the cold-store path too, but only when the build also has misses.

**Open by construction, and asserted as such:** a build that hits every row makes no embedding call,
so there is no new-space vector to compare against. Undetectable at zero cost, information-
theoretically, not for want of trying. Bounded to a repoint inside one process against a store that
process created, and the next process to open the store catches it on the canary.

**No canary is minted on a cold store**, because `tests/model/test_embedder_contract.py` is sealed
and asserts both `embedded == [summary]` and `len(cache) == 2`.

## D-15 — The probes are sampled from the store, and "information-theoretic" was wrong

Review found that minting sampled `reused` — this build's hits — so a build reusing *nothing*, which
is what a corpus rewrite looks like, minted the canary in the new embedder's space having examined
none of the pre-existing rows, and then stamped the store verified forever.

**Chosen:** sample from the store's own keys under this embedder's prefix, at the ends and the middle.
`cache_key` is `model|dimensions|text`, so `keys()` — already read once per build — carries every
row's plaintext, and a probe target therefore always exists.

**Retracted:** D-14's claim that the remaining case is "undetectable at zero cost,
information-theoretically". It is detectable for one embed call. What forbids it is
`tests/model/test_embedder_contract.py`'s assertion that a warm rebuild by the same embedder embeds
**nothing**, and that file is sealed. A contract, not a law — a materially different reason, and the
docstring now gives the right one.

## D-16 — Three tests were rewritten a third time, and the mutation entries are why

`test_keys_does_not_read_the_vector_column` shipped green against the defect twice: v1 patched an
object `keys()` never calls; v2 asserted on a scan it built itself. It now does both — the raising
patch *and* a spy on the projection actually requested — because each half is green against a
different regression. `test_replace_reconnects...` shipped green three times: identity-only, then a
source-text order check that a comment satisfied, and now an assertion on **which connection performs
the overwrite**, repeated, since a reconnect firing only once is invisible to a single call.

The lesson, recorded because it recurred three times in two days: **a test written by the author of
the fix tends to assert the shape of the fix rather than the property.** `tools/mutate.py` caught all
three, which is the argument for declaring the mutation at the same time as the test.

## D-17 — `load_from` streams; the second store stays

P2's amplification had two candidate fixes.

**Chosen:** stream the source in projected batches and write re-keyed batches straight into
`create_table`. 5.3x amplification down to 1.8x, which is the payload plus Lance's write buffer.

**Rejected: no `IN` predicate to fetch only the wanted rows.** It saves 9.7% of the read on this
corpus — which needs 90.3% of the store — while building a 1.53 MB SQL literal out of keys that
contain whole summaries. `missing()` already refuses that trade for the same reason. Streaming makes
the extra rows free anyway.

**Deferred, not rejected: eliminating the second store**, by having the index search the
content-keyed cache and translating `cache_key` to asset id in `semantic_search`. It would remove the
179 MB copy outright. **My reason for dismissing it was priced wrong and review corrected it**: I
wrote that "every facet on every turn would build that 1.6 MB predicate". 1.53 MB is the *whole
build's* key set. Per facet, `FACET_TARGETS` slices it by asset type — facet_entity 7,195 distinct
keys (0.79 MB), facet_example 4,857, facet_term 603, facet_metric 478, and **facet_schema 57, about
7 KB**. So the overstatement is 2x to 200x depending on the facet.

Two escapes I also failed to name:

- `VectorStore.search` already has a branch that builds **no predicate at all** — `limit = self._rows`
  plus a Python filter — and its own docstring measures that as *cheaper* at low selectivity (244 ms
  against 451 ms for the `IN` at k = n). With candidate sets at ~55% of the store, that is live.
- The 1.53 MB exists only because `cache_key` embeds the whole summary. A BTree-indexed digest column
  would cut the predicate to ~34 bytes per candidate. Hashing the key *itself* would not work —
  `index.py`'s I9 bootstrap probe recovers plaintext from the key — but a second column is unaffected.

So this stays open on its merits rather than closed on a bad number. It is not free: it makes the
index's vector view a filtered read of a shared store, which is a real change to `UnifiedIndex`.

**Behaviour change accepted and stated:** rows land in source order rather than `pairs` order. This
is a keyed store and `semantic_search` sorts by `(-score, id)` itself, so nothing depends on it.

## D-18 — `coverage` and the scorer tokenise differently, on purpose

E1. Audit I2's compound split is right for scoring — a user types words, a corpus is full of
identifiers — and wrong for `coverage`, whose declared unit is the share of the question's own
content terms the corpus knows. One query word is one term.

**Chosen:** `coverage` measures `_TOKEN.findall`, the whole tokens; `_tokenize`'s split stays in the
index. That is now the *second* deliberate asymmetry between the two (the stopword list is the
first), so both are asserted rather than left to a comment.

**Cost accepted:** two tokenisations of the query per turn, and a reader has to know which is which.
The alternative — one tokenisation — means either coverage counts subparts or scoring loses the
split, and both are worse.

## D-19 — Five words came back out of the stopword list, and the rejects are kept visible

E2. `may` is a month, `am` a time of day, `no` heads "invoice no", `can` and `will` are ordinary
nouns. Filtering them made a question the corpus cannot answer report full coverage.

**Chosen:** remove them, and keep them in a named `_REJECTED_AS_TOO_CONTENTFUL` frozenset with the
reason, guarded by a test asserting the two sets stay disjoint.

**Why keep a list of rejects at all:** the stopword list will be extended, and the next person needs
to see which candidates were considered and thrown out. A deleted word carries no argument; a
rejected one does. This is the same reasoning as D-1's "rejected alternative" paragraphs, applied to
data instead of prose.

## D-20 — E3 is adopted on its mechanism, and the prediction attached to it is recorded as failed

Document length now counts words rather than index terms. A compound's parts are synonyms of its
whole token, not additional content, and counting them made a summary of four identifiers longer than
one of five plain words — so `_B` penalised exactly the documents audit I2 was meant to make
reachable.

**Measured, against a criterion fixed before the run:** `all_gold_tables_licensed` 0.9306 → 0.9330,
`none_licensed` 0.0556 → 0.0531, paired shortlist hit +0.0015 with **2 discordant pairs of 1,351**
(p=0.5000, MDE=0.0029). Non-inferiority satisfied and the direction is positive, so: **adopt**.

**The prediction failed and that is the point of having written it down.** I predicted recall@5 would
recover part of the −0.0037 that I2/I3 could not explain, on the reasoning that an
identifier-penalty is what a recall@5 dip would look like. recall@5 moved **0.0000**. So E3 is not
that mechanism, the −0.0037 is still unexplained, and the next person should not spend the effort
here. Two discordant pairs is also a useful null: the rule changed and the ranking did not, which
means `avgdl` was not deciding shortlists on this corpus.

**Not claimed:** that E3 improves retrieval. The +0.0025 on the primary is inside the noise of a
2-discordant-pair comparison. What is claimed is that the length definition is now correct, which was
demonstrated in a unit fixture rather than in the aggregate.

## D-21 — Peak and net are two numbers, and the OS already knows both

P2's first write-up quoted one amplification figure, 1.8x, taken from the net. Review measured the
peak with `PeakPagefileUsage`/`PeakWorkingSetSize` — counters Windows maintains exactly — and got
+1,473 → +566 MB, so **peak amplification is 3.1x** while net is 1.8x.

Worse than the arithmetic: my 50 ms sampler undershot the peak by 25% before and 40% after, and when
a colleague reported a larger figure I **explained it away as their sampling artefact**. It was not.
That is the second time in this audit I have resolved a discrepancy into a plausible story instead of
checking it, which is the exact failure the whole exercise is about.

**Rule taken from it, and applied in the docstring:** sample a peak only when nothing will tell you
it. On Windows the OS will. And quote peak and net separately, because a fix can move one and not the
other — which is precisely what the batched read does here.

## D-22 — The fusion knobs travel as one frozen value, not three parameters

I10. `channel_scale(state)` is the single reader and returns a frozen `ChannelScale` carrying
`w_lexical`, `w_semantic` and `semantic_scale_ceiling`, resolved through `float_knob` so the
precedence matches every other knob.

**Why a value object and not three parameters:** the three are read together, and a call site that
resolved two from state and one from a constant would be I10 again in a smaller place.

**`combine_channels` takes `scale` with no default**, deliberately. A default would let a call site
keep reading the register while the turn ran on something else — the defect, preserved as a
convenience. It cost five call sites and about thirty test call sites, and it found a fifth scoring
site nobody had listed: `pass_two._pass_one_payload`'s carry-forward, whose test exists *because*
that same site once omitted `consulted`. The same site, the same omission, one audit later — which is
the argument for the required keyword.

**Not measured, and it does not need an arm:** every knob still ships its previous default, so the
resolved values are identical to the constants and no score moves. What changed is whether a request
can move them, which is asserted through `_pass_one_hits` — two knob settings, two different scores
— rather than on the resolver, because a resolver returning the right numbers says nothing about
whether the scoring path uses them. That is exactly how the import-time constants survived.

## D-23 — The merge's own claims are corrected in the tree, not force-pushed

An independent verification of `774b0d5` found 17 record defects and none in the code. The commit
message is already on `origin/main`.

**Chosen:** correct them in `audit-2026-08-10.md`, in a named section that lists what the message got
wrong, and leave the message alone.

**Rejected:** `git push --force` with an amended message. A published commit is a fact other people
may already hold; rewriting it to look correct is the same move as a page quietly correcting itself,
which is what this audit exists to remove. The tree is where the correction is durable and reviewable.

**The one that stings:** the headline "+0.08pp" was a bare subtraction of two rates — forbidden by
`AGENTS.md`, and flagged by this register's own still-open E2 against the very tool that produced it.
Properly paired it is `discordant=25/1224, p=1.0000, MDE=0.0114`: one question, 14x below the
detection floor. I reached for the number the tool printed instead of the test the repo owns, in a
commit whose subject is that gates must be able to fail.

## D-24 — A4 is a denial on two command keys, not on `command`

`command.update` and `command.goto` write thread state; `command.resume` answers a paused turn.

**Chosen:** refuse the two writers by name on `threads.create_run`, and let `resume` through.

**Rejected: denying `command` outright.** It is one character shorter and it removes the
clarification protocol — `ask_user` interrupts and the client answers with `command.resume`. That is
declared as a mutation (`a4-resume-refused-too`) rather than left as a comment, because a blanket
deny passes every test written about the *forgery* and silently deletes the feature.

**Rejected: a filter on which threads may be written.** Same reasoning as the state hook it sits
beside: the finding is not about ownership. `licensed` is the bound the layer stack enforces against
and `corpus_content_hash` is the treatment identity, so there is no value of "which thread" that makes
writing them acceptable.

**Why it was still open after A2/A3 shipped:** those closed `POST /threads/{id}/state`. Run creation
dispatches a different action, `("threads", "create_run")`, which no handler covered — and LangGraph
applies `update` through `map_command`, which unlike `map_input` writes every key it is handed with no
reference to the graph's input schema. Closing a door is not closing the room.

## D-25 — An auth hook is tested through the dispatch, never by calling it

A4's first fix read `value["command"]`; `langgraph_api` puts it at `value["kwargs"]["command"]`. The
handler returned early and allowed the exact payload the audit row said it refused — verified end to
end by review, with the forged `licensed` in the stored run.

**Three things all agreed it worked, and all three were looking at the same fiction:** a test that
called the handler function directly, two mutations written against that call, and the register row.
Deleting the `@auth.on.threads.create_run` decorator outright also left the test green.

**Chosen:** `tests/api/test_a_run_cannot_write_state.py` goes through `langgraph_api`'s own
`handle_event` with a real `AuthContext` and the value shape the runtime builds. It needs no port, no
model, no corpus and no Postgres, so there is no excuse for the direct call. Registration is asserted
separately, because fail-open-on-no-match is silent.

**Also chosen: fail closed on a shape the hook cannot read.** With request encryption on, `command`
arrives as ciphertext; returning early there would reopen this quietly. A security check that fails
open on an unexpected type is precisely how the first version got shipped.

**Rule, generalised, since this is the sixth time:** when a test *can* reach the real path, reaching
for the function instead is not a shortcut — it is a different test, of the author's model of the
system rather than of the system.

## D-26 — The RAM question is closed with a number, not with a diagnosis

The P-rows were opened by a report that RAM was "absurdly high". They produced two fixes worth having
(P1, P2), one diagnosis that was wrong by ~190x and is withdrawn (P3), and — until now — **no answer to
the question actually asked**: what does this thing cost when you run it?

**Chosen:** measure the served path and record the number. `langgraph dev`, real corpus, real
Postgres, three real turns. 727–785 MB working set, 1,348–1,416 MB private commit, peaks 939 / 1,635.
Serving does not grow it; the peak is the boot-time index build.

**Why this closes it rather than more profiling:** the reporter had already concluded the symptom was
another application, and chasing someone else's process is not this repository's business. What *was*
this repository's business is being able to say what it costs, and that was missing while three P-rows
argued about allocation. A number that anyone can re-measure ends the question; another diagnosis
would only have added a fourth thing to be wrong about.

**Not claimed:** that 0.7 GB is good or bad. It is what a process holding 13,304 curated assets and a
13,304 x 3,072 float32 vector store costs, stated so the next person can decide.
