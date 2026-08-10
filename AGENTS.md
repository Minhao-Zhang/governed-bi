# Working in this repository

**Greenfield, no users. Be bold.** Nothing here is deployed and nothing depends on backward
compatibility; if a design is wrong, change it rather than layering around it.

## What this project is

A governed text-to-SQL engine. The model never holds a database handle — it proposes SQL to a
tool body that checks it against a deterministic layer stack first, and the governance boundary
is the *absence* of a tool, not a policy asking the model to behave. BIRD is the measurement
instrument, not the product: a change that raises the score but would not help a real customer
with a real semantic layer is a defect.

## Shape

- Top level holds `README.md` and configuration. Everything else that is prose goes in `docs/`.
- `src/governed_bi/` is the package. `tools/` is CI checks and the eval driver. `scripts/` is
  one-shot campaign kits that nothing imports.
- `runs/` is gitignored. Measurement artifacts live in `runs/eval/`, which has its own README
  naming each arm.

## Documentation

**Docs state what is true now.** Not "this used to be X, now it is Y" — git carries the history
and a page holding both states is a page nobody trusts. Replace a superseded section; do not
append a note explaining that it is superseded. The one legitimate exception is recording *why
two numbers are not comparable*, which is a current fact about the numbers.

Every quoted figure names the arm and the corpus it was measured on, or it is not quotable.

## Measurement

This is where an agent is most likely to produce confident nonsense. Read `docs/measurement.md`
before touching anything in `eval/` or `tools/run_datalake_eval.py`.

- **Arms are compared with paired McNemar over discordant pairs, never by subtracting two EX
  numbers.** Two identical runs disagree on 12.7% of questions here, which puts SE(net) at about
  1.0pp — so a 2-point "improvement" usually is not one. Pinning routing with `--replay-routing`
  brings it to about 0.83pp.
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
- Comment the structure and the decisions, not the lines. Where a comment explains why something
  is *not* done the obvious way, it should cite the turn that went wrong.
- The layer stack, the register and the ledger each have exactly one implementation. Adding a
  second reader of "which attempts count" or "what the budget is" is the defect this repository
  keeps paying for; `tools/check_one_implementation.py` and `tools/check_imports.py` enforce
  parts of it.

Run before you commit — CI runs all of these:

```bash
uv run --frozen ruff check .
uv run --frozen pytest tests/<subset> -q     # the full suite OOMs on a 16 GB machine
uv run --frozen python tools/check_imports.py
uv run --frozen python tools/check_citations.py
```

## LangGraph and LangChain

Load the skill before writing graph code. Names are exact and the Skill tool will not guess:
`langgraph-fundamentals`, `langgraph-persistence`, `langgraph-human-in-the-loop`,
`langgraph-cli`, `langchain-fundamentals`, `langchain-middleware`, `langchain-dependencies`.
They are under `.claude/skills/`, pinned by hash in `skills-lock.json`.

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

## The three sibling repositories

None is vendored, so a claim about any of them must be checked in the checkout rather than
inferred from this tree.

| | Where | What |
|---|---|---|
| UI | `../governed-bi-ui` | [governed-bi-ui](https://github.com/Minhao-Zhang/governed-bi-ui), Next.js |
| Data | `../BIRD-Data-Obfuscation` | [BIRD-Obfuscation](https://github.com/Minhao-Zhang/BIRD-Obfuscation), the obfuscated lake |
| **Corpus** | `../BIRD-corpus` | [BIRD-corpus](https://github.com/Minhao-Zhang/BIRD-corpus), the semantic layer the engine serves |

The corpus is the one an agent is most likely to get wrong:

- **Point at it with `GOVERNED_BI_CORPUS_DIR=../BIRD-corpus`**, already set in `.env`. The value
  resolves against this repo's root, not the process's working directory.
- **It is the treatment identity of every measurement.** `corpus_content_hash` digests the tree,
  so **do not write generated stores or scratch files into that checkout** — anything beside the
  assets changes the identity of every number measured against it. Only `.git/` and
  `__pycache__/` are excluded.
- **Versioned is not rebuildable.** `scripts/corpus_rebuild/01–03` write the mechanical half —
  structure, joins, few-shots. The prose half has no producer anywhere and those scripts leave it
  as `TODO` markers. Do not describe the corpus as reproducible-from-source.

## Git

- **Commit before mutation-testing.** Restoring with `git checkout -- <path>` restores from HEAD,
  which silently discards uncommitted work in the same file. This has cost work here more than
  once.
- **Do not `git add -A` while a subagent is editing.** Stage your own paths by name.
- Artifacts and corpora do not belong in a commit. `runs/` is ignored; keep it that way.
