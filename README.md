# governed-bi

**Ask your database questions in English. Get an answer, the SQL behind it, or a reason it could
not answer.**

[![CI](https://github.com/Minhao-Zhang/governed-bi/actions/workflows/ci.yml/badge.svg)](https://github.com/Minhao-Zhang/governed-bi/actions/workflows/ci.yml)
![Python 3.13](https://img.shields.io/badge/python-3.13-blue)
![Node 22](https://img.shields.io/badge/node-22-green)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

governed-bi turns a business question into read-only SQL, checks that SQL before it runs, and
hands back the statement along with the answer. When it cannot find what a question needs, it
says so instead of guessing.

![A full turn at real speed: the question is sent, the governance stages report themselves as they
run, and the answer arrives with the SQL behind it](docs/images/answered-turn.gif)

<sup>One turn, at real speed. About eighteen seconds from question to answer.</sup>

**What to look for while it runs:**

| on screen | what it is |
|---|---|
| the tree filling in, `Reading the question` through `Answered` | the engine's own stage events, each appearing when that stage finished. Not a progress animation |
| `negocios`, `estrellas`, `estado = 'AZ'` | physical names nobody typed. The question said "Arizona businesses" and "star rating" |
| `LIMIT 200001` | a row guard the engine appends to every statement |
| `1 passed governance` | the deterministic check the statement cleared *before* the database saw it |

Read the SQL, not the answer. The schema is deliberately obfuscated, because this is the
[BIRD-Obfuscation](https://github.com/Minhao-Zhang/BIRD-Obfuscation) lake, where table and column
names carry no English meaning. The mapping from business vocabulary onto them lives in the
semantic layer you curate, and so does knowing that Arizona is `'AZ'`. That layer is the product.

> **A demonstration, not a measurement.** One turn on a live stack rather than a run, so nothing in
> the clip is a quotable figure. For those, see [how well it works](#how-well-it-works).

## What makes it different

**The model never touches your database.** It proposes SQL and never holds a connection. A tool
body checks every statement against a deterministic stack first and runs it read-only, so the
boundary is the absence of a tool rather than a prompt asking the model to behave.

**A miss becomes a refusal, not a wrong number.** Retrieval decides which tables a question
licenses, and a statement reaching for anything else is blocked before it runs. Ask for something
the semantic layer does not cover and the engine says so, or pauses and asks you, instead of
computing a confident and plausible number over whatever tables happened to be nearby. That is the
half the clip above does not show, and it is the half the design exists for.
[Failure modes](docs/failure-modes.md) is what it does instead, per class, with the numbers.

**Permissions withhold, they do not only refuse.** Point `GOVERNED_BI_ACCESS_POLICY` at a roles
file and, in one sentence: *a grant withholds an asset from everything this repository shows a
caller — the model's prompt, all four of its tools, and every HTTP route that projects a corpus
asset — and it withholds nothing from a database, from a row, from an answer's prose, or from the
curation problems `/audit/corpus` reports.* Refusing a statement that names a column is a weaker
property than never showing the column, and the first half of that sentence is what makes it the
second. On an unmodified checkout the shipped adapter authorizes everything, so every rule fires
and every rule says yes. [ADR 0012](docs/adr/0012-access-seam-principal-and-authorization.md) §8
is the boundary in full; [the fork guide](docs/enterprise-fork.md) is how to move it.

**Every figure here carries its own error bar.** Accuracy is quoted at a stated coverage, arms are
compared with a paired test rather than by subtracting two scores, and
[open work](docs/open-work.md) lists every defect this project has found in itself, including the
defects in its own measuring instrument.

## What the layers actually do

Every statement is parsed and inspected before the database sees it. The stack rejects writes,
rejects a function call it does not permit, rejects a bare `SELECT *`, rejects a reference it
cannot bind to exactly one source, rejects an excluded column, and rejects any table the question
did not license. A seventh layer, cost, is declared and ships disabled.

Each turn also leaves a record of what ran, what was rejected, and why, so you can audit an answer
after the fact rather than taking it on faith. [Architecture](docs/architecture.md) has the
wiring; [ADR 0006](docs/adr/0006-execution-time-governance.md) has the layer stack and the ten
bypasses it was built against.

## Installation

It ships as three things: a Python library, a LangGraph server with a streaming HTTP API, and the
Next.js web client in [`ui/`](ui/) shown above. The engine needs none of the frontend to run.

**Requirements**

| | |
|---|---|
| [uv](https://docs.astral.sh/uv/) | dependency management |
| Python 3.13 | `requires-python = ">=3.13"` |
| Postgres | the engine's served datasource |
| A semantic layer | [see below](#the-semantic-layer). Bring your own, or clone the ready-made one |
| Node 22 | the web interface only. The library, CLI and server need none of it |

**1. Install the engine.**

```bash
uv sync
```

**2. Configure it.** Copy `.env.example` to `.env` and fill in three values:

```bash
GOVERNED_BI_PG_DSN=host=127.0.0.1 port=5432 dbname=... user=... password=...
OPENAI_API_KEY=sk-...
GOVERNED_BI_CORPUS_DIR=../BIRD-corpus
```

`GOVERNED_BI_CORPUS_DIR` points outside the repository, deliberately rather than by omission. The
semantic layer is the treatment identity of every number in [measurement](docs/measurement.md), so
vendoring it would mean an unrelated code change moved `corpus_content_hash`.

**3. Install the web interface**, if you want it. Its dependencies are its own and are not part of
`uv sync`:

```bash
npm --prefix ui ci
```

Use `ci` here rather than `install`. The reason `npm --prefix ui install` fails with ENOENT is
worth reading before you debug it: [usage § UI](docs/usage.md#ui).

## Usage

Two processes: the engine, and the web client that talks to it. The clip at the top of this page
is what you get.

**1. Start the engine.**

```bash
GOVERNED_BI_API_KEY=$(openssl rand -hex 32) uv run langgraph dev
```

That serves http://localhost:2024. The key is the shared credential every route but `GET /livez`
requires. Leaving it unset does not leave the server open; it makes the server refuse every
request with 401.

**2. Start the interface.**

```bash
npm --prefix ui run dev
```

That serves http://localhost:3000. Copy [`ui/.env.example`](ui/.env.example) to `ui/.env.local`
and point it at the engine, with `NEXT_PUBLIC_GOVERNED_BI_API_KEY` set to the same value as
`GOVERNED_BI_API_KEY` above. The two files stay separate even in one repository: Next.js inlines a
`NEXT_PUBLIC_` variable into the browser bundle at build time and never reads `.env`. If they
disagree, every request answers 401 and the panels render empty.

Chat is one of five views. The others show the semantic layer as an ER diagram and a typed
knowledge graph, page through every corpus asset the engine loaded, list past conversations, and
replay any turn the server has served stage by stage, including the SQL and what governance did
to it.

Leave `NEXT_PUBLIC_LANGGRAPH_URL` unset and the UI runs on neutral mock fixtures instead. Every
surface renders with no engine, no database and no API key, which is useful for working on the
interface and easy to mistake for a working stack.

Every environment variable and both API surfaces are in [the usage guide](docs/usage.md).

## The semantic layer

governed-bi does not read your database and guess. It reads a semantic layer you curate: files
describing your tables, columns, joins, metrics and business vocabulary. That is where "Oklahoma"
learns to mean `'OKC'`, and where "active customer" gets a definition your finance team agrees
with.

A ready-made one for the BIRD benchmark lake is in a separate repository,
[BIRD-corpus](https://github.com/Minhao-Zhang/BIRD-corpus): 13,304 assets across 57 schemas. Point
`GOVERNED_BI_CORPUS_DIR` at your own to serve your own data. The format is in
[corpus format](docs/corpus-format.md).

## How well it works

Arm `v4`, engine `3c0079a`, corpus [`BIRD-corpus`](https://github.com/Minhao-Zhang/BIRD-corpus) @
`30872d3`, 1,351 questions across 57 schemas.

| | |
|---|---:|
| Correct, among the questions it answered | **0.714** (n = 1,278, 94.6% coverage) |
| Questions it declined | 73 (5.4%) |
| Declined questions it would have got wrong | **77.4%** (48 of the 62 that can be priced) |
| Correct over all 1,351 turns, unfiltered BIRD EX | 0.676 |

Two things keep that table honest.

The declines are not a difficulty estimate. The 73 are 20 refusals, 4 clarifications, and 49 turns
that spent all five query attempts without a passing statement. Every refusal and clarification is
a retrieval failure: 19 of the 20 end on `r_table_not_licensed`, and all 4 clarifications licensed
nothing at all. Of the 49 capped turns, 26 were not fully covered either. What abstention tracks is
whether this engine had enough context on the turn, not how hard the question is.

A governance-off contrast arm puts a bound on that. WrenAI runs the same questions against the
same database with a refusal rate of 0; it answers all 73 declined questions and gets 56.2% of
them right, against 68.5% on the 1,278 this engine commits to. The ratio is 1.22×, so the declined
set is mostly answerable. That arm differs from this engine on every dimension at once, so it
bounds the claim rather than attributing it. And 77.4% is a figure over a subset the dataset
selected rather than a random one: 62 of the 73 carry a gold fingerprint, and for the other 11
what the engine would have got is unknown rather than zero.

About 4 points of that EX are shape rather than retrieval. Arm `v5` is `v4` with one paragraph
about result-column selection deleted from the prompt and nothing else changed. EX falls from
0.676 to 0.635, a drop of 4.07pp (paired McNemar p = 4.9e-06, 143 discordant). The grader hashes result rows, so
aligning the output column set to the reference answer is worth points that nothing inside the
engine checks. Every system reporting EX on this benchmark carries some of that, and measuring it
costs a second arm.

And comparing two arms takes more than subtracting their scores. Two runs of this engine with the
configuration held fixed disagree on 12.7% of outcomes (172 of 1,351), which puts SE(net) near
1.0pp, or 0.83pp with routing pinned, so the smallest effect a 1,351-question arm resolves at 80%
power is about 2.3pp. That is why the arms above are compared with paired McNemar over discordant
pairs, and why the threshold is written down before the run rather than once the number is visible.

Those are all numbers about answering. What the governance layers themselves are worth is a
separate measurement, and unlike everything above it costs nothing to reproduce: the layer stack
is deterministic, so it needs no credential, no database and no model call, and two runs of it are
identical. The suite is 95 cases carried as data in
[`govern/adversarial.toml`](src/governed_bi/govern/adversarial.toml) — 49 attacks and 46 ordinary
analytics statements — and one file is read by both the test that fails a build and the driver
that prints the rates.

| | |
|---|---:|
| Attacks that reached executable SQL | **0** (of 49) |
| Attacks refused, but by the wrong layer or rule | **0** (of 49) |
| Ordinary statements wrongly refused | **0** (of 46) |
| Cases where a layer crashed instead of deciding | **0** (of 95) |
| Per-layer recall, over the attacks each layer owns | **1.000** (PARSE 7/7, NO_WRITE 7/7, FUNCTIONS 13/13, BINDING 9/9, COLUMNS 7/7, TABLES 6/6) |

Read that with its bound, which is not small: 49 is the number of attacks somebody sat down and
wrote, so zero bypasses is a fact about those 49 and not a claim about attacks nobody thought of.
The same goes the other way — 0 false refusals is over 46 benign statements, not over the space of
queries an analyst writes. Five of the ten bypass families this project tracks have no SQL surface
to aim a statement at and are covered by argument rather than by a case; the suite declares that
per family rather than leaving it to be assumed. And the COST layer owns no attack, so it has no
rate at all and is printed as not measured rather than as a pass. Reproduce with
`uv run --frozen python tools/govern_bench.py`.

How the measurement works, and where the engine still gets things wrong, are in
[measurement](docs/measurement.md) and [failure modes](docs/failure-modes.md).

## Documentation

| | |
|---|---|
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
