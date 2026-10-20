# 0017: A served chat app over a semantic layer the user brings

- **Status:** Accepted (2026-10-19).
- **Deciders:** project owner.
- **Scope:** what the project is, what it keeps, and what it deletes. The answer's assumptions
  are [0018](0018-every-answer-states-its-assumptions.md), the page is
  [0019](0019-one-plain-chat-page.md), and Ossie is
  [0020](0020-ossie-export-and-a-separate-viewer.md).
- **Replaces:** the [trim plan](../reviews/2026-10-trim-plan.md), its
  [milestones](../reviews/2026-10-trim-milestones.md) and the
  [Ossie plan](../reviews/2026-10-ossie-plan.md). The reasoning is in the
  [pivot memo](../reviews/2026-10-pivot.md).

## Context

The project set out to give "grounded, governed, auditable answers over relational data", and to
refuse or ask rather than return a confident wrong number. After the v1 delete on 2026-08-02 the
work went into the serving side and then into things around it: an access policy that never held
more than one principal, a feedback product whose only reader was the owner, and checks on the
repository itself (63 declared knobs, 22 corpus conformance rules, 84 nightly code mutations, 12
`check_*` gates, a parcel map of the tests, quotability gates on every number). Retrieval is 2,441
of 42,907 lines of Python, and the tests are 52,000 lines.

The trim plan would have removed the two subsystems with no users and kept the rest, including
the machinery that makes each deletion expensive.

## Decision

### 1. What the project is

The engine reads a semantic layer and a read-only Postgres connection. It answers a question in
English with the result and the SQL that produced it, or it refuses, or it asks a clarifying
question. It is served by LangGraph Server to one chat page that people use directly.

The semantic layer is an input, written by whoever owns the data. This project does not write it,
so the curator from the first design does not return. BIRD-corpus is the layer used for
measurement.

### 2. What stays

- Retrieval, schema routing, reference closure and join planning (`retrieve/`).
- Context assembly and the agent loop (`serve/`), less the parts in §3.
- The SQL checks and read-only execution ([0002](0002-governed-agentic-serve-runtime.md),
  [0006](0006-execution-time-governance.md) without the access layer or the cost layer,
  `datasource/`). The model never holds a connection, and that is what makes an answer
  trustworthy.
- Identifiers ([0008](0008-identifiers-end-to-end.md)).
- The utility model and per-facet query rewriting
  ([0011](0011-two-model-split-and-facet-query-rewriting.md)).
- The declared abstention policy ([0013](0013-the-declared-abstention-policy.md)), unchanged.
- Clarification through LangGraph interrupts, resumable after a restart, on the one conversation
  store ([0014](0014-one-conversation-store.md)).
- LangGraph Server and `useStream` ([0001](0001-langgraph-server-chat-runtime.md)), and the
  `threads.update` and `create_run` state-write denials in `api/auth.py`.
- The HTTP routes the page calls ([0007](0007-http-surface-and-the-ui-contract.md)), and the stage
  events ([0010](0010-live-stage-events.md)), which the page shows as one status line.
- The eval harness, the paired test, coverage against risk and run-to-run disagreement in
  `measure/`.
- The train-only and test-question leak checks, because they keep the score honest.

### 3. What goes

| What | Why |
|---|---|
| Access and identity ([0012](0012-access-seam-principal-and-authorization.md)) | The database platform owns who may see what. This engine promises only that the model sees the tables retrieval selected and that every statement passes the checks and runs read-only. `api/auth.py` keeps an authenticate handler that returns a constant identity, because LangGraph Server will not start an `auth` block without one |
| The return path ([0015](0015-the-return-path.md)), its tools, review page and note control | It never had a reader |
| The browse API ([0009](0009-browsing-and-filtering-api.md)) and every page except the chat page | The layer is shown by the separate viewer in 0020 |
| The display levels on the answer card (engineer, analyst) and the settings page | One view for everyone, with details on request (0019) |
| `register/`'s declared-knob closure; the mutation catalogue and its nightly run; `tests/contracts.py`; the gates for file length, one implementation, declared-is-consumed, citations, measurement locality, landed, ratchet, corpus delta and imports | They check the repository, not the answers |
| Corpus conformance (`conform/`) and the nightly corpus gate ([0016](0016-gating-the-corpus-repository.md)) | The layer belongs to the user. `corpus/validate.py` already checks that it loads |
| The reflection and negative-example nodes and the cost layer | Off by default and never measured |
| The proxy and Bedrock gateways, the `bedrock` extra, and every non-OpenAI branch in `model/` | The project supports OpenAI only (§5) |
| `tools/govern_bench.py` | The adversarial suite in the tests covers the checks that stay |
| Provenance and quotability gates beyond a run manifest | A manifest of engine commit, corpus commit, model and split is enough |
| [0005](0005-v2-memory-layer-and-faceted-retrieval.md)'s file-length and one-implementation rules, and its statement that this repository does not produce the corpus | The rules police the repository. The statement is now simply true and needs no rule |

`register/` keeps the arm definitions, the prompts and the stage vocabulary as plain modules.

### 4. How the deletions are shown to be safe

Before deleting, take an offline snapshot: for each of the 1,351 BIRD-Obfuscation questions,
record the routed schemas, the licensed tables, the join plan and a hash of the rendered context,
and record the verdict and rule id of every adversarial and benign statement. Run it with the
deterministic embedder and facet rewriting off, so it needs no model. Each deleting PR reruns it
and must match exactly, apart from differences listed in the PR before the run. The deletions are
done in a few large PRs, not one per file.

### 5. Measurement

The app and the eval use one model setup, on OpenAI only: `gpt-6-luna` as both the agent and the
utility model, the newest luna model the account lists on 2026-10-19, and `text-embedding-3-large`
for embeddings. The headline is execution accuracy at
a stated coverage on BIRD-Obfuscation, measured twice, with the paired test against the previous
baseline. Every result is reported for the `dev` half, the `holdout` half and the total, using the
split in `docs/data/db-split.csv`: anything designed from failures reads `dev` only, and a decision
to keep a change reads `holdout` only (`docs/measurement.md`). No run has used the current default model yet, so the first run after the deletions is
the baseline.

## Consequences

- About a third of the Python source and half of the UI go, and with them most of the cost of
  changing anything.
- A deployment that needs per-user data access has to provide it with database roles. The README
  and usage docs must say so plainly.
- ADRs 0009, 0012, 0015 and 0016 are rewritten as reversal records when their code is deleted, and
  0005, 0007 and 0010 are trimmed to what remains.
- The September milestones end, except for measurement work that feeds the baseline. The M8 pilot
  runs on the page in 0019.
- `AGENTS.md` points at the pivot milestones, changed with the owner's consent on 2026-10-19.

## Rejected

- **The trim plan.** It kept the machinery that made each step cost six PRs and a second
  reviewer.
- **Bringing back the curator.** Building the layer is a different product. The owner chose to
  leave it out.
- **Deleting the served app and keeping only an eval library.** The app is the product.
