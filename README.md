# governed-bi

_[English](README.md) · [简体中文](README.zh.md)_

An agentic BI system: it answers natural-language questions over a relational
database with **grounded, governed, auditable** SQL. Point it at a live
**Postgres** database, give it a model key, and ask a question. It retrieves the
relevant slice of a curated semantic layer, generates SQL, runs it through five
guardrail layers, executes it read-only, and returns the answer with an audit
trail.

The connector layer is dialect-pluggable: Postgres is the exercised-live path,
Redshift is seamed, and SQLite is kept only as the offline test / CI substrate,
not a runtime we ask anyone to deploy.

## How it works

- **Two harnesses, one substrate.** A `curator` (build) *produces* a semantic
  layer (the corpus) from a seed of known-good `(question, SQL)` pairs; a
  `server` (serve) *consumes* it to answer. Opposite risk profiles, one shared
  corpus.
- **The corpus is the moat.** Git-tracked typed YAML assets + Markdown skills,
  curator-authored and human-audited. Git is the single source of truth; the
  graph / vector / BM25 stores are rebuildable projections.
- **Fail-closed.** Out-of-scope, missing coverage, or a tripped guardrail returns
  a refusal or a clarifying question, never a confident wrong number. Answers
  carry two separate stamps: `safety_clearance` (did it pass the guardrails) and
  `semantic_assurance` (how well-grounded), never collapsed into one trust score.

## Quickstart

Requires [uv](https://docs.astral.sh/uv/) and Python 3.13.

```bash
uv sync                       # create .venv, install the default (OpenAI) stack
uv sync --extra bedrock       # ...or add the AWS Bedrock provider
```

**1. Point at your database.** The committed default is a bundled SQLite fixture
(`beer_factory`) that exists **only for the demo and tests** — don't build real
work on it. For anything beyond the demo, serve your own database by adding a
git-ignored `governed_bi.local.toml` beside [`governed_bi.toml`](governed_bi.toml):

```toml
[datasource]
kind = "postgres"
db = "your_schema"       # corpus subtree / db id
dsn_env = "PG_DSN"       # names the env var that holds the DSN
```

**2. Set your secrets** in a git-ignored `.env` at the repo root (never commit
these):

```bash
OPENAI_API_KEY=sk-...
PG_DSN=host=... port=5432 dbname=... user=... password=...
```

**3. Serve and ask.** Serving is agent-only and requires a live model, so it
**fails closed without a key**:

```bash
uv run langgraph dev          # starts the `serve` graph; POST questions to /chat
```

See [walkthrough](docs/walkthrough.md) for a guided first-question tour and
[usage](docs/usage.md) for the full reference. To drive the agent core directly
from Python, see [`docs/server.md`](docs/server.md).

## Configuration

All non-secret policy lives in one file, [`governed_bi.toml`](governed_bi.toml)
(parsed by `governed_bi.config.load_settings()`): runtime toggles, models,
datasource, corpus path, serve flags. Machine-local overrides go in a git-ignored
`governed_bi.local.toml` (same tables; local wins). Secrets (API keys, DSN
passwords) live only in the environment or a git-ignored `.env`, which is loaded
on import and never overrides an already-exported variable.

Optional tracing (LangSmith or Langfuse) is documented in
[`.env.example`](.env.example).

## Development

Everything except live serving runs offline with no model and no network, over
the vendored SQLite fixture:

```bash
uv run python -m governed_bi.corpus.cli   # validate the corpus (ID + reference integrity)
uv run pytest                             # run the test suite
uv run python scripts/live_smoke.py       # end-to-end over a real model (needs OPENAI_API_KEY)
```

The offline suite exercises the serve core against deterministic model doubles,
so it needs neither a key nor a network. Everything both harnesses need for the
default OpenAI stack (LangGraph, deepagents, LangChain, OpenAI, Langfuse, psycopg)
lives in `[project.dependencies]`, so a plain `uv sync` installs it all. The one
extra is `bedrock` (`uv sync --extra bedrock`): it pulls `langchain-aws` + boto3
for the AWS Bedrock provider — set `provider = "bedrock"` in `[models]` to use it.

## Status

Built and tested: the governed agentic serve core ([ADR
0002](docs/adr/0002-governed-agentic-serve-runtime.md): a deterministic rails
graph wrapping a bounded `create_agent` loop over read-only tools, with guardrail
+ audit middleware), the corpus contract and validator, the five-layer
guardrails, the curator, retrieval, eval harness, semantic SQL cache, and the
read-only audit API.

The corpus-as-moat claim has a first live result on an obfuscated Postgres
database: curator-built assets lift execution accuracy over a no-corpus baseline
and drive decoy-column touches to zero. But it is single-seed and small-N, so
the result is directional, not yet conclusive. The current milestone is the
**scale run** — all 69 BIRD DBs loaded as Postgres schemas (8,134 train /
2,030 test), where the large held-out test set replaces single-seed deltas as the
unit of evidence (see
[audit dispositions](docs/design-decisions.md#audit-dispositions-2026-07-15)).
Full numbers and method:
[three-arm results](docs/plans/three-arm-experiment-results.md).

Designed but not yet built: `CorpusRelease` (immutable, hash-pinned serving
release). Seamed but toggled off (enterprise-fork scope): identity → query scope
(RLS / tenant isolation), the human approval gate, scoped memory/cache. Redshift
has offline connector tests only.

## Web UI

The frontend is a separate repo:
[Minhao-Zhang/governed-bi-ui](https://github.com/Minhao-Zhang/governed-bi-ui)
(Next.js, `useStream`). It targets this backend's streaming chat contract; it is
not yet wired live end-to-end.

## Documentation

Start at [`docs/README.md`](docs/README.md). Key docs:
[architecture](docs/architecture.md) · [design decisions](docs/design-decisions.md) ·
[asset schemas](docs/asset-schemas.md) · [curator](docs/curator.md) ·
[server](docs/server.md) · [viz](docs/viz.md) · [glossary](docs/glossary.md).

## Repo layout

```
docs/               design docs (canonical)
corpus/             the semantic layer (Git = source of truth); worked example under beer_factory/
data/bird/          beer_factory.sqlite: offline test/CI fixture (BIRD, CC BY-SA 4.0; see NOTICE)
src/governed_bi/
  config.py         environment toggles, models, datasource shape (load_settings)
  llm/              ChatClient / Embedder seams (OpenAI + LangChain + offline defaults)
  corpus/           schemas, IDs, CI validator, loader, serializer, CLI
  gateway/          connectors (SQLite / Postgres / Redshift), read-only gateway, five-layer guardrails
  curator/          Facts profiling, proposers, adversary review, curate loop, deepagents build harness
  graph/            FK graph projection + Steiner-tree join planning
  retrieval/        BM25 + grounding + vector channel (RRF fusion)
  memory/           working memory; episodic/correction seams
  server/           the ADR-0002 governed agentic core (sole serve path): agent, tools, middleware, governance, cache, stamp
  eval/             execution accuracy, arm harness, refuse-gate
  viz/              read-only audit surface (UI-agnostic presenter view models)
tests/              unit + end-to-end suites
```

## License

Code is under the MIT License (see [LICENSE](LICENSE)), © 2026 Minhao Zhang.

Bundled data is third-party and separately licensed:
`data/bird/beer_factory.sqlite` is the `beer_factory` database from the
[BIRD benchmark](https://bird-bench.github.io/), included unmodified under
[CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/); see
[`data/bird/NOTICE`](data/bird/NOTICE). The MIT license does not cover the data.
