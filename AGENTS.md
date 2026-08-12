# Working in this repository

**Greenfield, no users. Be bold.** Nothing here is deployed and nothing depends on backward
compatibility; if a design is wrong, change it rather than layering around it.

> **Restored 2026-08-12.** `506ad9b` ("Merge the frontend into this tree…") replaced this file
> with a 31-line generic template, deleting *Measurement*, *Coding*, *LangGraph and LangChain* and
> *The three sibling repositories* — 110 lines. The commit message does not mention it, so it was
> collateral rather than a decision. Eight sites in `src/`, `tests/` and `tools/` went on citing
> the deleted text. This is the old file with the stale parts corrected, not a straight revert.

## What this project is

A governed text-to-SQL engine. The model never holds a database handle — it proposes SQL to a
tool body that checks it against a deterministic layer stack first, and the governance boundary
is the *absence* of a tool, not a policy asking the model to behave. BIRD is the measurement
instrument, not the product: a change that raises the score but would not help a real customer
with a real semantic layer is a defect.

## Shape

- Top level holds `README.md` and configuration. Everything else that is prose goes in `docs/`.
- `src/governed_bi/` is the package. `ui/` is the Next.js client — same repository since
  `506ad9b`, connected only over HTTP, with no import in either direction. `tools/` is CI checks,
  the eval driver, and `tools/corpus_rebuild/`.
- `runs/` is gitignored. Measurement artifacts live in `runs/eval/`, which has its own README
  naming each arm.

## Documentation

**Docs state what is true now.** Not "this used to be X, now it is Y" — git carries the history
and a page holding both states is a page nobody trusts. Replace a superseded section; do not
append a note explaining that it is superseded. The one legitimate exception is recording *why
two numbers are not comparable*, which is a current fact about the numbers.

Every quoted figure names the arm and the corpus it was measured on, or it is not quotable.

**The numbers in this tree are verified; the cross-file and present-tense claims are the ones
still worth checking.** This file used to say "do not trust any docstrings", which was honest and
too blunt. A four-package sweep on 2026-08-12 checked every docstring and comment in `src/`,
`tools/` and `tests/` against the code, corrected what was false and deleted what could not be
verified — and an independent audit then sampled the result and found eight claims still standing.
So the honest rule is the one above, not "everything here is checked".

What the audit found holds: every numeric claim it could execute reproduced exactly, all 573 ADR
citations resolve, no test reference is dead, and no mutation anchor has rotted. What it found
still broken were **cross-file** claims and **present-tense absence** claims — the two kinds a
file-by-file sweep structurally misses, because verifying one costs a second file. Two of the
eight were contradicted by an assertion in this repository's own test suite, which is the cheapest
check there is.

These are the failure modes, each one found here more than once:

- a docstring **crediting a mechanism that does nothing** (a dead line credited for ending a loop);
- a docstring **naming consumers that do not exist** (an argument that three readers made a
  vocabulary safe; none of the three read it);
- a **fabricated quotation** — a sentence attributed verbatim to another module where the string
  appears nowhere in the tree; five of these were found;
- a **stale count** stated directly above the thing it counts;
- a **present-tense absence claim** — "nothing reads this today", "this has no call site" — written
  when it was true and never revisited when someone added the reader;
- a **false exclusivity claim** — "the only reader of X", "the only thing here that reads
  `os.environ`". Cheap to write, and a grep away from being disproved;
- a **fabricated worked example** — an identifier spelled plausibly and wrongly, with a digest
  computed from the name that does not exist;
- an **identifier shape that omits `slug()`** — this one is not a documentation defect. The same
  wrong sentence caused a security bound to compare a raw key against a folded set and fail open.

Two habits close most of these. **Cite symbols, not line numbers** — every `file.py:NN` coordinate
checked in the sweep had rotted. And before writing "nothing does X" or "only Y does X", **grep,
and check whether a test already asserts the opposite**: two of the eight residual claims were
contradicted by this repository's own suite.

## Measurement

This is where an agent is most likely to produce confident nonsense. Read `docs/measurement.md`
before touching anything in `eval/` or `tools/run_datalake_eval.py`.

- **Arms are compared with paired McNemar over discordant pairs, never by subtracting two EX
  numbers.** Two identical runs disagree on 12.7% of questions here, which puts SE(net) at about
  1.0pp — so a 2-point "improvement" usually is not one. Pinning routing with `--replay-routing`
  brings it to about 0.83pp.
- **A paired test on nested populations is not a test.** If one policy delivers a subset of the
  other's turns, one discordant cell is 0 by construction and the p-value restates the nesting.
  `measure.selective.compare_policies` refuses to dress that as a hypothesis test; do not
  reintroduce it by hand.
- **State the threshold before the run, not after.** A criterion chosen once the number is
  visible is not a criterion.
- **Check the mechanism before the score.** If a change was supposed to stop a rule firing, count
  that rule first. A score that moved for a reason you cannot name has not been explained.
- **Rule out the null before believing a pattern.** `run1` and `run2` differ only by seed and are
  on disk for exactly this. A pattern that also appears there is a property of the question set.
- The corpus is the treatment identity. Quote the commit beside any figure.

## Coding

- Tests are not required for everything. They *are* required where a mistake already happened
  once — and then the test must be **mutation-verified**: break the behaviour, watch the test
  fail, restore. A test asserting that a constant equals itself is worse than no test, because it
  reports coverage it does not have. Eight of those were found here in one sweep.
- **A gate must assert that it scanned something.** A sweep that walks a tree, collects offenders
  and asserts the list is empty passes on zero input. That is audit finding D13, and it recurred
  in new code written after D13 was filed — because "add a positive control" is a per-sweep
  instruction and does not survive the next sweep. The control has to travel with the walk.
- Comment the structure and the decisions, not the lines. Where a comment explains why something
  is *not* done the obvious way, it should cite the turn that went wrong.
- The layer stack, the register and the ledger each have exactly one implementation. Adding a
  second reader of "which attempts count" or "what the budget is" is the defect this repository
  keeps paying for; `tools/check_one_implementation.py` and `tools/check_imports.py` enforce
  parts of it.

Run before you commit. CI runs all of these plus the full suite:

```bash
uv run --frozen ruff check .
uv run --frozen pytest tests/ -q            # ~85 s, peaks at ~1.4 GB working set
uv run --frozen python tools/check_file_length.py
uv run --frozen python tools/check_one_implementation.py
uv run --frozen python tools/check_measurement_locality.py
uv run --frozen python tools/check_imports.py
uv run --frozen python tools/check_citations.py
uv run --frozen python tools/check_no_benchmark_discriminators.py
uv run --frozen python tools/govern_bench.py
```

`tools/mutate.py` runs the declared mutation catalogue. It is **nightly, not per-push**, and it
**rewrites source files in place and restores them** — never run it while anything else is
editing the tree, including another agent. A concurrent write inside its window is silently
clobbered.

The whole suite fits in memory: measured 2026-08-10, peak working set 1.46 GB and peak private
commit 2.29 GB in one process. Almost all of that is **one** `build_index` against the warm vector
store. See the P-rows in `docs/analysis/audit-2026-08-10.md`, and read P3 there before trusting any
diagnosis in them — including that one, whose first version was wrong by ~190x. Two measurement traps
recorded with them: `.venv/Scripts/python.exe` is a uv trampoline, so polling its working set from
outside reads a 4 MB stub; and commit charge can move without working set, so sample
`PagedMemorySize64` too.

`tools/check_declared_is_consumed.py` is deliberately **not** in CI. It is declared in
`tests/conformance/test_register_closure.py`'s manual list, which also carries the condition
for wiring it up, and a ratchet test pins the remaining findings **by name** — so a new one fails
the build and closing one also fails it. Names, not a count: six findings and six *different*
findings are the same integer. Adding any `tools/check_*.py` without declaring it fails that test.

## LangGraph and LangChain

Load the skill before writing graph code. Names are exact and the Skill tool will not guess:
`langgraph-fundamentals`, `langgraph-persistence`, `langgraph-human-in-the-loop`,
`langgraph-cli`, `langchain-fundamentals`, `langchain-middleware`, `langchain-dependencies`.
They are under `.claude/skills/`, pinned by hash in `skills-lock.json`; `npx skills update`
refreshes them.

**The skills describe framework defaults; this repo departs from several on purpose, and the
call-site docstring is the authority.** The two that bite hardest:

- **There is no `RetryPolicy` anywhere**, because re-running a node after it failed resamples a
  draw after seeing it. Provider SDK `max_retries` (`llm_max_retries`, default 3) is a different
  thing and is a comparability knob.
- **Nested `create_agent` recursion is capped by `agent_recursion_limit` (default 40)**, not by
  `create_agent`'s built-in 9999.

Also: `n_re_served` is always 0 and is not a quotability gate.

**Deep Agents is retired here. Do not adopt it, and do not consult framework selection — it is
decided.** `.claude/skills/` still carries `deep-agents-*` and `ecosystem-primer`, whose decision
table routes planning and subagent work there; ignore that route. The reason is specific:
`FilesystemMiddleware` contributes `write_file` and `edit_file`, they are not removable, and a
generic write channel is what let v1 forge `source=human, status=certified` on curated assets.
Use a `StateGraph` with a `create_agent` node, which is how `serve/` is already built.
`pyproject.toml` carries the full reasoning.

Use `context7` to look up library syntax rather than recalling it, and `firecrawl` for web search.

## The two sibling repositories

Neither is vendored, so a claim about either must be checked in the checkout rather than
inferred from this tree. (The UI was a third until `506ad9b` moved it into `ui/`.)

| | Where | What |
|---|---|---|
| Data | `../BIRD-Data-Obfuscation` | [BIRD-Obfuscation](https://github.com/Minhao-Zhang/BIRD-Obfuscation), the obfuscated lake. **The GitHub name is not the local directory name.** |
| **Corpus** | `../BIRD-corpus` | [BIRD-corpus](https://github.com/Minhao-Zhang/BIRD-corpus), the semantic layer the engine serves |

The corpus is the one an agent is most likely to get wrong:

- **Point at it with `GOVERNED_BI_CORPUS_DIR=../BIRD-corpus`**, already set in `.env`. The value
  resolves against this repo's root, not the process's working directory.
- **It is the treatment identity of every measurement.** `corpus_content_hash` digests the tree,
  so **do not write generated stores or scratch files into that checkout** — anything beside the
  assets changes the identity of every number measured against it. Only `.git/` and
  `__pycache__/` are excluded.
- **Versioned is not rebuildable.** `tools/corpus_rebuild/01–03` write the mechanical half —
  structure, joins, few-shots. The prose half has no producer anywhere and those scripts leave it
  as `TODO` markers. Do not describe the corpus as reproducible-from-source.

## Git

- **Commit before mutation-testing.** Restoring with `git checkout -- <path>` restores from HEAD,
  which silently discards uncommitted work in the same file. This has cost work here more than
  once.
- **Do not `git add -A` while a subagent is editing.** Stage your own paths by name.
- Use a feature branch. Artifacts and corpora do not belong in a commit; `runs/` is ignored, and
  it stays that way.
