# governed-bi

An agentic BI / Generative-BI system: natural-language questions → **grounded,
governed, auditable** answers over relational data.

Near-term target is a **general, DB-agnostic showcase** that cold-starts from
`{a DB connection + a handful of known-good queries}` and grows a semantic layer
over time, evaluated on the self-built [BIRD-Obfuscation](https://github.com/Minhao-Zhang/BIRD-Obfuscation) dataset (execution
accuracy). Enterprise abstractions (identity, human gate, RLS, scoped
memory/cache) are seamed in but toggled off.

> **Design-first.** The design is well ahead of the build: D1-D10 are settled
> (see [`docs/design-decisions.md`](docs/design-decisions.md)). This repo is the
> scaffold. The corpus layer is implemented; the harnesses are documented stubs.

## The idea in three lines

- **Two harnesses over one shared substrate.** A `curator` (build) *produces*
  the corpus; a `server` (serve) *consumes* it to answer. Opposite risk
  profiles, one substrate.
- **The corpus is the moat.** Git-tracked YAML typed assets + Markdown skills,
  curator-authored and human-audited. Git is the single source of truth; graph /
  vector / BM25 stores are rebuildable projections.
- **Fail-closed.** Out-of-scope / missing-coverage / tripped-guardrail returns a
  refusal or a clarifying question, never a confident wrong number.

## Documentation

Start at [`docs/README.md`](docs/README.md). Key docs:
[architecture](docs/architecture.md) ·
[design decisions (D1-D10)](docs/design-decisions.md) ·
[asset schemas](docs/asset-schemas.md) ·
[curator](docs/curator.md) · [server](docs/server.md) · [viz](docs/viz.md) ·
[glossary](docs/glossary.md).

## Repo layout

```
docs/                  design docs (canonical)
data/bird/             beer_factory.sqlite (BIRD, CC BY-SA 4.0; see NOTICE)
corpus/                the semantic layer (Git = source of truth); worked example under beer_factory/
src/governed_bi/
  config.py            environment toggles (dev/BIRD vs prod/enterprise) + reusable numbers
  corpus/              done: schemas, IDs, CI validator, loader, CLI
  gateway/             done (SQLite): connector + read-only gateway; Postgres/Redshift seams; guardrails stub
  curator/             profile.py done (Facts tier); proposer/adversary/loop stubs
  graph/               stub: FK graph projection + Steiner-tree join planning
  retrieval/           stub: RVGD retrieval
  memory/              stub: working / profile / episodic / correction
  server/              stub: LangGraph serve DAG + middleware
  eval/                stub: execution accuracy, 3-arm harness, gold oracle, refuse-gate
  viz/                 stub: audit + edit cockpit
tests/                 corpus + connector/gateway/profiling tests
```

Modules carry docstrings that point back to the design docs and decision IDs.

## Usage & development

Requires [uv](https://docs.astral.sh/uv/) and Python 3.13.

```bash
uv sync                                   # create .venv, install deps + package
uv run python -m governed_bi.corpus.cli   # validate the corpus (ID + reference integrity)
uv run pytest                             # run the test suite
```

The full quickstart (validate CLI, programmatic corpus API) is in
[docs/usage.md](docs/usage.md). To write or edit corpus assets, see
[docs/corpus-authoring.md](docs/corpus-authoring.md).

Runnable today: the corpus layer, the SQLite connector and gateway, and
catalog-driven Facts profiling. The remaining harnesses are documented stubs.
Core dependencies are intentionally minimal (pydantic, pyyaml, networkx,
sqlglot); the Postgres/Redshift connectors are optional extras, and `langgraph`
/ `deepagents` are deferred until those harnesses are built.

## License

Code in this repository is under the MIT License (see [LICENSE](LICENSE)),
© 2026 Minhao Zhang.

Bundled data is third-party and separately licensed. `data/bird/beer_factory.sqlite`
is the `beer_factory` database from the [BIRD benchmark](https://bird-bench.github.io/),
included unmodified under [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/);
see [`data/bird/NOTICE`](data/bird/NOTICE). The MIT license does not cover the data.
