# Usage

How to run the server, the page, one question from the command line, the evaluation, and the Ossie export. How a turn is built is in [architecture](architecture.md). The decision records are in [adr/](adr/).

## Prerequisites

- [uv](https://docs.astral.sh/uv/)
- Python 3.13, pinned in [`.python-version`](../.python-version)
- PostgreSQL
- An OpenAI API key
- Node 22, for the page

```bash
uv sync
```

CI runs `uv sync --frozen`, then `uv run --frozen ruff check .`, `uv run --frozen mypy src/governed_bi`, and `uv run --frozen pytest -q -rs`.

The app and the evaluation use OpenAI.

## Secrets and settings

Copy [`.env.example`](../.env.example) to `.env`. That file may hold only secrets:

| Name | Role |
|---|---|
| `OPENAI_API_KEY` | The OpenAI key |
| `GOVERNED_BI_PG_DSN` | The Postgres connection string. Required for the server, the one-question command, and the data-lake evaluation |
| `LANGSMITH_TRACING`, `LANGSMITH_API_KEY`, `LANGSMITH_PROJECT` | Tracing. Off unless you set them. LangSmith reads these itself |

A `.env` entry that is not one of those, and that has a value, stops startup and names the key. Settings are not read from `.env`. A `GOVERNED_BI_*` override belongs in the process environment. The names and defaults are in [`src/governed_bi/registry/settings.py`](../src/governed_bi/registry/settings.py). An empty value is ignored, and the default stands.

The ones you are likely to set:

| Variable | Default | Role |
|---|---|---|
| `GOVERNED_BI_CORPUS_DIR` | `../BIRD-corpus`, resolved from the repository root | The semantic layer |
| `GOVERNED_BI_SCHEMA` | empty | Limit a curated corpus to one schema |
| `GOVERNED_BI_MODEL` | `gpt-6-luna` | The chat model |
| `GOVERNED_BI_MODEL_EFFORT` | `xhigh` | Reasoning effort for the chat model |
| `GOVERNED_BI_UTILITY_MODEL` | `gpt-6-luna` | The model for retrieval rewriting and the scope check |
| `GOVERNED_BI_UTILITY_MODEL_EFFORT` | `medium` | Reasoning effort for the utility model |
| `GOVERNED_BI_EMBEDDING_MODEL` | `text-embedding-3-large` | Embeddings |
| `GOVERNED_BI_LLM_MAX_RETRIES` | `3` | Retries for the chat model |
| `GOVERNED_BI_LLM_TIMEOUT_S` | `300` | Timeout for the chat model, in seconds |
| `GOVERNED_BI_UTILITY_TIMEOUT_S` | `30` | Timeout for the utility model, in seconds |
| `GOVERNED_BI_CONVERSATION_DB` | `runs/conversations.sqlite` | The server's conversation file |
| `GOVERNED_BI_HARNESS_DB` | `runs/harness-checkpoints.sqlite` | The checkpoint file for the one-question command and the evaluation |
| `GOVERNED_BI_VECTOR_CACHE` | `runs/vectors` | The embedding cache |
| `GOVERNED_BI_CORPUS_RELEASE` | empty | The corpus tag recorded with a measurement |
| `GOVERNED_BI_AGENT_NODE_TIMEOUT_S` | `1200` | Wall clock for one turn. `0` means no limit |
| `GOVERNED_BI_AGENT_RECURSION_LIMIT` | `40` | Step ceiling for the agent loop |
| `GOVERNED_BI_RAIL_NODE_TIMEOUT_S` | `120` | Wall clock for one utility step |
| `GOVERNED_BI_SEED_DIR` | empty, then `runs/seeded-corpus/<schema>` | Where a corpus seeded from a live schema is written, when the server takes that path |

`GOVERNED_BI_CONVERSATION_DB` and `GOVERNED_BI_HARNESS_DB` are file paths. A value that looks like a database connection string is refused, because those files must not be the warehouse the questions run against. The markers are `postgres://`, `postgresql://`, `redshift://`, `host=`, `dbname=`, and `password=`.

`OPENAI_BASE_URL` is read by the OpenAI SDK, so it redirects chat and embeddings together. Leave it unset unless you mean to.

Who may see which rows is a role on the database. This app has no setting for that, and the server does not ask a caller for a credential.

## A database and a corpus

The server needs both halves. `GOVERNED_BI_PG_DSN` names the database. `GOVERNED_BI_CORPUS_DIR` names the semantic layer, and it defaults to `../BIRD-corpus` beside this repository. The two have to describe the same warehouse. Setting the schema variable picks one schema inside that corpus. It does not replace the corpus.

A ready-made layer for the obfuscated BIRD lake is [BIRD-corpus](https://github.com/Minhao-Zhang/BIRD-corpus). The database is [BIRD-Obfuscation](https://github.com/Minhao-Zhang/BIRD-Obfuscation). The format of a layer you write yourself is in [corpus format](corpus-format.md).

To ask one question of a small schema, without a curated corpus, create the schema and then use the one-question command below. The command seeds a corpus from the live schema into a temporary directory.

```bash
uv run --frozen python tools/load_demo_schema.py
uv run --frozen python tools/load_demo_schema.py --schema my_demo
```

`--schema` defaults to `gbi_demo_sales`. The tool drops and recreates that schema, so the name should be one you can lose. `--seed` defaults to `20260803` and fixes the row counts. The connection string comes from `GOVERNED_BI_PG_DSN`.

Leaving `GOVERNED_BI_CORPUS_DIR` unset still selects `../BIRD-corpus`, so the chat server loads that directory. It seeds from `GOVERNED_BI_SCHEMA` only when no corpus directory is configured, and the default means one is.

## Server

```bash
uv run langgraph dev
```

That listens on `127.0.0.1:2024`. `--port` changes the port, and `--host` changes the interface. The default host is `127.0.0.1`. The server will not start without `OPENAI_API_KEY`, because a chat model is configured by default. [`langgraph.json`](../langgraph.json) maps the graph `serve` to `src/governed_bi/api/graph_app.py:make_graph`, and the extra HTTP routes to `src/governed_bi/api/routes.py:app`.

The server admits every caller. It refuses a request that writes conversation state directly. Answering a clarifying question is a resume, and that is allowed. Keep the port on localhost. Anyone who can reach it can ask a question and read past conversations.

Conversations are stored in `runs/conversations.sqlite` and survive a restart of the server.

These routes are the ones this app adds. Their shapes are in [openapi.json](openapi.json).

| Method and path | What it returns |
|---|---|
| `GET /livez` | Liveness. It does not touch the database |
| `GET /readyz` | Ready, or 503 with a reason |
| `GET /capabilities` | The model, the database, and related facts the page reads at startup |
| `GET /clarifications/pending` | Clarifying questions nobody has answered, oldest first |
| `GET /examples` | Example questions from the corpus. `limit` defaults to 3. `near` ranks them against a question. The SQL stays off the response |

`langgraph.json` allows browser calls from `localhost` and `127.0.0.1` on ports 3000 and 3100, and from `https://smith.langchain.com`. Restart the server after changing that list.

## Page

The page is `ui/`. From that directory, the same commands CI runs:

```bash
cd ui
npm ci
npm run dev
```

`npm run dev` serves <http://localhost:3000>. Copy [`ui/.env.example`](../ui/.env.example) to `ui/.env.local`.

| Variable | Role |
|---|---|
| `NEXT_PUBLIC_LANGGRAPH_URL` | The server, `http://localhost:2024`. When this is unset, the page uses the fixtures in `ui/lib/mock/fixtures.ts` and does not call a server. Next.js inlines it at build time |
| `NEXT_PUBLIC_ASSISTANT_ID` | The graph id. Default `serve` |

The page is one chat. "New conversation" starts a thread, and the open thread is `/?thread=<id>`. The sidebar groups older conversations by day. A thread that is waiting on a clarifying question is listed first, under "Waiting for you".

You type a question and send it. Enter sends, and Shift+Enter inserts a line. While a turn runs, Stop replaces Send, and one line names the current step. An answer shows the text, the rows, the assumptions, and Copy and Details. A guess has a Change button, which resends the question with "(Assume instead: …)" appended. A refusal offers nearby example questions, and clicking one fills the composer. A clarifying question offers choices, a typed answer, Defer, and Decline.

The checks the page runs, from `ui/`, are `npm run lint`, `npx tsc --noEmit`, `npm test`, and `npm run build`. `npm run check:stream-messages` stays manual.

## One question from the command line

```bash
uv run --frozen python -m governed_bi.serve --schema gbi_demo_sales -q "how many customers?"
uv run --frozen python -m governed_bi.serve --corpus-dir ../BIRD-corpus -q "how many customers?"
```

One of `--schema` or `--corpus-dir` is required, and so is `-q`. `--schema` alone seeds a corpus from that schema in the live database. `--corpus-dir` reads a corpus already on disk, and `--schema` then limits it to one schema.

| Flag | Role |
|---|---|
| `-q`, `--question` | The question. Required |
| `--schema` | Schema to seed from, or the schema to load from `--corpus-dir` |
| `--corpus-dir` | A corpus already on disk |
| `--thread-id` | Name the checkpoint, so a later command can reach a paused turn. Omit it and the checkpoint is deleted after the turn prints |
| `--model` | Chat model for this run. Omit it and the command uses `GOVERNED_BI_MODEL` from the environment, or the default `gpt-6-luna` |
| `--no-model` | Run the stub path, with no model call |
| `--embed` | Build the embedding index. This spends tokens |
| `--effort` | Reasoning effort: `none`, `low`, `medium`, `high`, or `xhigh` |
| `--json` | Print the turn record as JSON |

A turn that pauses on a clarifying question exits 4. This command cannot answer the question for you. With `--thread-id`, the checkpoint is kept. Resuming it means posting a run with `command.resume` on that thread, which is what the page does.

## Evaluation

The data-lake run, over Postgres and the BIRD questions, is:

```bash
uv run --frozen python tools/run_datalake_eval.py --workers 2 --effort xhigh --resume
```

It needs `OPENAI_API_KEY` and `GOVERNED_BI_PG_DSN`, and it exits 2 when either is missing. `--corpus-dir` defaults to `../BIRD-corpus` beside this repository, from the script's own location. `--dataset` defaults to `../BIRD-Data-Obfuscation/eval_dataset`. Rows are appended as they finish. `--resume` keeps finished rows and retries rows that crashed. `--resume` and `--truncate` cannot be used together.

| Flag | Default | Role |
|---|---|---|
| `--corpus-dir` | `../BIRD-corpus` | The semantic layer |
| `--dataset` | `../BIRD-Data-Obfuscation/eval_dataset` | The question files |
| `--model` | the settings default, `gpt-6-luna` | Chat model |
| `--effort` | the settings default, `xhigh` | Reasoning effort. An empty string sends none |
| `--embedding-model` | the settings default | Embedding model |
| `--utility-model` | the settings default, then `--model` | Utility model |
| `--utility-effort` | the utility effort from settings, when the utility model came from settings | Needs a utility model |
| `--embed` | off | Build the embedding index. This spends embedding tokens |
| `--workers` | `2` | Concurrent questions |
| `--max-retries` | `8` | Provider retries per call |
| `--timeout` | `240` | Per-request timeout, in seconds |
| `--limit` | none | Stop after this many questions |
| `--per-schema` | none | Cap questions per schema |
| `--top-n` | the `route_top_n` setting | How many schemas routing keeps |
| `--abstain` | off | Turn on the abstention policy for this run. Accuracy and coverage both move |
| `--config` | none | A name from `src/governed_bi/registry/configs.toml`. The driver checks it against the corpus before the first paid question |
| `--prompt-variant` | none | `NAME=VARIANT`, repeatable. An unknown name is refused |
| `--out` | derived | Where the JSONL is written |
| `--against` | none | A second artifact. The report then includes how often the two runs disagree |
| `--replay-routing` | none | A prior JSONL whose schema shortlist this run reuses |
| `--resume` | off | Continue an artifact, and retry crashed rows |
| `--force-fresh` | off | Allow a first run when sibling artifacts exist and this output file does not |
| `--truncate` | off | Delete the artifact at `--out` and start over. This is the flag that discards a measured run |

How to read the report, and which numbers may be quoted, is in [measurement](measurement.md). The current baseline figures are there too.

A smaller harness runs gold SQL, a stub, or a scripted config against a SQLite file. It does not call the chat model.

```bash
uv run --frozen python -m governed_bi.eval --db path/to.sqlite --questions path/to.jsonl
```

| Flag | Default | Role |
|---|---|---|
| `--db` | required | The SQLite file |
| `--questions` | required | JSONL with `question_id`, `question`, and `gold_sql`. The oracle config also needs `gold_fingerprint`, or `gold_columns` and `gold_rows` |
| `--configs` | `oracle` | Comma-separated: `oracle`, `stub`, `scripted` |
| `--oracle-only` | off | Run only the oracle config |
| `--limit` | none | Stop after this many questions |
| `--pair` | none | `configA,configB`. The summary then includes the paired test |

## Export

```bash
uv run --frozen python -m governed_bi.corpus.ossie_export --corpus-dir ../BIRD-corpus --out runs/ossie
```

`--corpus-dir` and `--out` are both required. The command writes one Ossie model per database under `--out`, and `export-report.json` beside them. It prints the totals. Load problems exit 1. The mapping is in [corpus format](corpus-format.md).

## Checks

```bash
uv run --frozen pytest -q -rs
```

That is the test command CI runs. `-rs` prints the reason for every skip. Postgres and OpenAI tests skip when the credentials are absent.

Two checks look for benchmark text in the wrong place:

```bash
uv run --frozen python tools/check_no_benchmark_discriminators.py
uv run --frozen python tools/check_train_only.py PATH/TO/CORPUS --control PATH/TO/TRAIN-ONLY-CORPUS
```

The first needs no corpus, and CI runs it. The second needs a corpus, a train-only corpus passed as `--control`, and the question file. `--dataset` defaults to `../BIRD-Data-Obfuscation/eval_dataset/test_final.jsonl`. `--tolerance` defaults to `2`. A control that is the same tree as the corpus is refused. Passing `--control ''` skips that comparison.
