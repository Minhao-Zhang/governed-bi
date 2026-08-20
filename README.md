# governed-bi

**Ask your database questions in English. Get an answer, the SQL behind it, or a reason it could
not answer.**

[![CI](https://github.com/Minhao-Zhang/governed-bi/actions/workflows/ci.yml/badge.svg)](https://github.com/Minhao-Zhang/governed-bi/actions/workflows/ci.yml)
![Python 3.13](https://img.shields.io/badge/python-3.13-blue)
![Node 22](https://img.shields.io/badge/node-22-green)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

governed-bi turns a business question into read-only SQL, checks that SQL before it runs, and
hands back the statement along with the answer. When it cannot find what a question needs, it
says so instead of guessing. It is for data teams who need an answer they can audit.

![A full turn at real speed: the question is sent, the governance stages report themselves as they
run, and the answer arrives with the SQL behind it](docs/images/answered-turn.gif)

<sup>One turn, at real speed — about eighteen seconds from question to answer. The stage tree is
the engine reporting itself as each stage finishes, not a progress animation.</sup>

**Read the SQL, not the answer.** `negocios`, `estrellas` and `estado = 'AZ'` are physical names
nobody typed — the question said "Arizona businesses" and "star rating". The schema is
deliberately obfuscated: this is the
[BIRD-Obfuscation](https://github.com/Minhao-Zhang/BIRD-Obfuscation) lake, where names carry no
English meaning. Mapping business vocabulary onto them, and knowing Arizona is `'AZ'`, is the job
of the semantic layer you curate. That layer is the product.

> A demonstration, not a measurement. One turn on a live stack, so nothing in the clip is a
> quotable figure. Those are [below](#how-well-it-works).

## What makes it different

**The model never touches your database.** It proposes SQL and never holds a connection. A tool
body checks every statement first and runs it read-only, so the boundary is the *absence of a
tool* rather than a prompt asking the model to behave.

**A miss becomes a refusal, not a wrong number.** Ask for something the semantic layer does not
cover and the engine says so, or pauses and asks you, instead of computing a plausible number over
whatever tables happened to be nearby. [Failure modes](docs/failure-modes.md) is what it does
instead, per class, with the numbers.

**A paused turn outlives the process.** When the engine needs to ask you something it stops
mid-turn and checkpoints, so killing the server and starting a fresh one leaves the question where
it was — answering it resumes that same turn rather than beginning the work again. Three bounds
travel with that. It rests on one hand-run observation, on 2026-08-19, and **no test**: every
human-in-the-loop test runs on an in-memory saver, so nothing automated drives a real pause and
resume across a process boundary. The resumable state and the browsable history are two different
files, and only the first is the durable one. And the 90-day retention this deployment configures
cannot fire on the runtime it runs, so the store only grows.
[ADR 0014](docs/adr/0014-one-conversation-store.md) is the design;
[open work](docs/open-work.md) keeps the gaps.

**Permissions withhold, they do not only refuse.** A grant hides an asset from everything a caller
sees — the model's prompt, its tools, and every route that serves one. Never showing a column is a
stronger property than refusing a statement that names it. The shipped adapter authorizes
everything; [the fork guide](docs/enterprise-fork.md) is how to change that.

**Every figure says what it cannot support.** Accuracy is quoted at a stated coverage,
configurations are compared with a paired test rather than by subtracting two scores, and
[open work](docs/open-work.md) lists every defect this project has found in itself — including
the defects in its own measuring instrument.

**What "governed" means concretely.** Six checks run over every statement before the database sees
it, and any one of them can refuse. It must parse as a single read; it must contain no write;
every function it calls must be on an allowlist; every reference must bind to exactly one column,
which is what rejects a bare `SELECT *`; no column may be one the corpus excludes; and no table
may be one the question did not license. Every turn then leaves a record of what ran, what was
rejected and why. [Architecture](docs/architecture.md) has the wiring.

## The semantic layer

governed-bi does not read your database and guess. It reads a semantic layer you curate: files
describing your tables, columns, joins, metrics and business vocabulary. That is where "active
customer" gets the definition your finance team agrees with, and where the engine learns that a
question about Arizona means `estado = 'AZ'`.

A ready-made one for the BIRD lake is in a separate repository,
[BIRD-corpus](https://github.com/Minhao-Zhang/BIRD-corpus) — 13,304 assets across 57 schemas.
Point `GOVERNED_BI_CORPUS_DIR` at your own to serve your own data; the format is in
[corpus format](docs/corpus-format.md).

## Quick start

You need PostgreSQL, Python 3.13, Node 22, and a model provider — OpenAI by default, Bedrock
supported. Queries cost whatever that provider charges. To reproduce the demo above you also
need its two inputs, cloned as siblings of this repository: the
[obfuscated lake](https://github.com/Minhao-Zhang/BIRD-Obfuscation) (a Postgres dump, restored with
the `docker compose` in that repo) and the
[semantic layer](https://github.com/Minhao-Zhang/BIRD-corpus).

```bash
uv sync                       # the engine
uv sync --extra bedrock       # instead, if the provider is Bedrock
npm --prefix ui ci            # the web client, optional
```

Copy `.env.example` to `.env` and fill in three values:

```bash
GOVERNED_BI_PG_DSN=host=127.0.0.1 port=5432 dbname=... user=... password=...
OPENAI_API_KEY=sk-...
GOVERNED_BI_CORPUS_DIR=../BIRD-corpus
```

Then start the two processes:

```bash
uv run langgraph dev            # engine  :2024
npm --prefix ui run dev         # client  :3000
```

**The engine authenticates nobody.** No route asks for a credential, so anything that can reach
`:2024` can post a turn and read every past one — including the SQL — out of `GET /audit/turns`.
That is a deliberate choice for a single-operator engine on loopback, taken on 2026-08-13 because
LangGraph Studio's bootstrap calls cannot carry a header; it re-opens audit findings A1 and A7,
and [the usage guide](docs/usage.md#serve-langgraph-server) says so in full. Do not put this port
anywhere but `127.0.0.1`. Copy [`ui/.env.example`](ui/.env.example) to `ui/.env.local` to point
the client at the engine.

Chat is one of seven views. The others show the semantic layer as an ER diagram and a knowledge
graph, page through every corpus asset, list past conversations, replay any served turn stage by
stage, and hold the questions the engine asked that nobody came back to answer — a clarification
whose reader closed the tab leaves no trace in the thread's own channels, so that queue is read
out of the platform's interrupt state and is the only place an abandoned one is visible. Leave `NEXT_PUBLIC_LANGGRAPH_URL` unset and the client runs on mock fixtures with no
engine attached.

Everything else — every environment variable, both API surfaces, and the UI's own quirks — is in
[the usage guide](docs/usage.md).

## How well it works

Measured on [BIRD](https://bird-bench.github.io/), a public text-to-SQL benchmark, in the
obfuscated variant linked above: 1,351 questions across 57 schemas. *EX* is the benchmark's own
score — the query ran and its result matched the reference answer. A configuration is fixed and
named before a run so two of them can be compared; this one is `v4`, engine `3c0079a`, corpus
[`BIRD-corpus`](https://github.com/Minhao-Zhang/BIRD-corpus) @ `30872d3`, questions
[`BIRD-Obfuscation`](https://github.com/Minhao-Zhang/BIRD-Obfuscation) @ `22fe2a6`.

**That last identifier is here because the count is not one.** That test split has had four
versions and only one of them holds 1,351 questions, so a rerun against a replaced dataset would
report the same *n* over a different population — and pass every quotability gate, because the
gates compare the corpus digest and the knobs and both would match. Every arm now declares its
question set in [`arms.toml`](src/governed_bi/register/arms.toml) and the driver refuses a
mismatch before the first paid question.

| | |
| --- | ---: |
| Correct, among the questions it answered | **0.714** (n = 1,278, 94.6% coverage) |
| Questions it declined | 73 (5.4%) |
| Declined questions it would have got wrong | **77.4%** (48 of the 62 where that is knowable) |
| Correct over all 1,351 turns, unfiltered EX | 0.676 |

For scale, [WrenAI](https://github.com/Canner/WrenAI) — an open-source engine with no abstention —
scores 0.678 on the same questions and the same database, graded by its own harness. That is a
sense of the neighbourhood, not a head-to-head: it differs on every dimension at once, so it can
bound a claim but cannot attribute one.

Three caveats travel with the table. The declines are **not** a difficulty estimate — they track
whether this engine had enough context on the turn, and every refusal and clarification among them
is a retrieval failure. Roughly 4 points of any score on this benchmark are output *shape* rather
than reasoning, since the grader compares result rows and column choice moves them; that is a
confounder in the metric every system carries, not a discount on this one. And two runs with the
configuration held fixed disagree on 12.7% of outcomes, so a gap under about 2 percentage points
is not a result. [Measurement](docs/measurement.md) and [failure modes](docs/failure-modes.md)
carry all three in full.

What the governance layers themselves are worth is a separate measurement, and unlike the above it
costs nothing to reproduce: the stack is deterministic, so it needs no credential, no database and
no model call. The suite is 115 cases — 62 attacks and 53 ordinary analytics statements — carried
as data in [`govern/adversarial.toml`](src/governed_bi/govern/adversarial.toml).

| | |
| --- | ---: |
| Attacks that reached executable SQL | **0** (of 62) |
| Attacks refused, but by the wrong layer or rule | **0** (of 62) |
| Ordinary statements wrongly refused | **0** (of 53) |
| Cases where a layer crashed instead of deciding | **0** (of 115) |
| Per-layer recall, over the attacks each layer owns | **1.000** (all six layers) |

Read that with its bound, which is not small: 62 is the number of attacks somebody sat down and
wrote, so zero bypasses is a fact about those 62, not a claim about attacks nobody thought of. The
same holds in reverse for the 53 benign statements. Reproduce with
`uv run --frozen python tools/govern_bench.py`.

## Documentation

| | |
| --- | --- |
| [Usage](docs/usage.md) | install, configure, serve |
| [Architecture](docs/architecture.md) | how a turn is put together |
| [Corpus format](docs/corpus-format.md) | writing a semantic layer |
| [Measurement](docs/measurement.md) | running an evaluation and reading the result |
| [Failure modes](docs/failure-modes.md) | how the engine gets things wrong |
| [ADRs](docs/adr/) | the binding design decisions |
| [Open work](docs/open-work.md) | the defect list, the instrument's own defects included |
| [Frontend](docs/usage.md#ui) | running the Next.js client in `ui/`, and its checks |
| [AGENTS.md](AGENTS.md) | the working rules for this repository, human or agent |

## License

MIT (see [LICENSE](LICENSE)), copyright 2026 Minhao Zhang. No third-party data is bundled: the
BIRD lake and the semantic layer are separate repositories under their own terms.
