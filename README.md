# governed-bi

governed-bi is a chat app that answers questions about a database. You bring the semantic layer, which names the tables, columns, joins, metrics, and business words the questions use. Every answer states the assumptions behind it, and every query is checked before it runs.

Who may see which rows is a role on the database. This app has no setting for that.

## Run it

You need [uv](https://docs.astral.sh/uv/), Python 3.13, Node 22, PostgreSQL, and an OpenAI key. OpenAI bills the model calls.

```bash
uv sync
```

Copy [`.env.example`](.env.example) to `.env` and set the two secrets it names, `OPENAI_API_KEY` and `GOVERNED_BI_PG_DSN`. A `.env` file may also set names that start with `LANGSMITH_`. Any other name with a value stops startup. Other settings are process environment variables. The defaults live in [`src/governed_bi/registry/settings.py`](src/governed_bi/registry/settings.py).

The corpus defaults to `../BIRD-corpus`, resolved from this repository's root. Set `GOVERNED_BI_CORPUS_DIR` when the layer lives somewhere else. The database in `GOVERNED_BI_PG_DSN` has to be the one that corpus describes. The chat model defaults to `gpt-6-luna`, and embeddings default to `text-embedding-3-large`.

You can load a small demo database and ask it one question from the command line, without a curated corpus. The chat server still needs a corpus directory. [Usage](docs/usage.md) has both.

```bash
uv run langgraph dev
```

```bash
cd ui
npm ci
npm run dev
```

The server listens on `127.0.0.1:2024`. The page is at <http://localhost:3000>. Copy [`ui/.env.example`](ui/.env.example) to `ui/.env.local`, and set `NEXT_PUBLIC_LANGGRAPH_URL` to `http://localhost:2024`. `NEXT_PUBLIC_ASSISTANT_ID` defaults to `serve`, the graph name in [`langgraph.json`](langgraph.json). When the URL is unset, the page draws fixture conversations and does not call the server.

The server admits every caller. Anyone who can reach port 2024 can ask a question and read past conversations, so leave it on localhost.

## How an answer reads

The page has a sidebar and one conversation. "New conversation" is at the top of the sidebar. Conversations are grouped by day, and one that is waiting on a clarifying question is listed first, under "Waiting for you". The open conversation is `/?thread=<id>`.

An empty conversation says "What do you want to know?" and lists up to three example questions from the corpus. Clicking one fills the composer. Under the composer, the page says "Read-only. Every query is checked before it runs."

While a turn runs, one grey line names the current step, such as "Finding your definitions…" or "Checking the query…".

An answer shows the text, then the rows. When the page holds fewer rows than the query returned, the caption reads "Showing 20 of 1,204 rows" for those counts. Then "To answer this, I assumed:" and one bullet per assumption. An assumption the semantic layer backs ends with "From your definition:" and the definition's name. A guess ends with "This is a guess." and a Change button. Change sends the same question again with the correction appended, in the form "(Assume instead: …)". Something the server found in the SQL, and the model did not state, ends with "The query does this, though you didn't ask for it." A convention is the statement on its own. Copy copies the text and the table. Details opens the SQL, the checks, the tables the turn used, the model, and the time taken.

A clarifying question is the question, a sentence saying why, outlined choices, and a box if you would rather type. Defer lets the engine choose and carry on. Decline stops the turn.

A refusal says "I can't answer this from your data.", then the reason, then nearby questions from the corpus. Choosing one fills the composer.

## Evaluation

How to run an evaluation, and how to read the result, is in [Measurement](docs/measurement.md). The commands are in [Usage](docs/usage.md).

On BIRD-Obfuscation's 1,351 questions, with the current default model, two runs reached execution accuracy of 0.553 and 0.544, with coverage of 0.843 and 0.851. The two runs disagreed on 11.3% of questions, and a question cost about $0.0028. Answers that included a guess were right 46% to 50% of the time, against 67% to 68% for answers with no guess. The figures by half are in [Measurement](docs/measurement.md).

## The semantic layer

The corpus is one Apache Ossie model per schema, so any Ossie tool can open it. What Ossie has no field for is kept in a `GOVERNED_BI` extension. The layout is in [Corpus format](docs/corpus-format.md). The app shows the layer in [ossie-viewer](https://github.com/Minhao-Zhang/ossie-viewer), a separate project.

## Repository

`src/governed_bi/` is the engine: the corpus, retrieval, the SQL checks, the chat graph, and the evaluation. `ui/` is the chat page. `docs/` is the design. `tools/` holds the data lake evaluation and the checks that are not part of the server.

## Documentation

- [Usage](docs/usage.md): install, configure, serve, evaluate, and export.
- [Corpus format](docs/corpus-format.md): the semantic layer, one Ossie model per schema.
- [Architecture](docs/architecture.md): how a question becomes an answer.
- [Failure modes](docs/failure-modes.md): how answers go wrong.
- [Measurement](docs/measurement.md): running an evaluation and reading the result.
- [Glossary](docs/glossary.md): the words this repository uses.
- [Design decisions](docs/design-decisions.md): where the decisions are written.
- [Open work](docs/open-work.md): what is unfinished.
- [ADRs](docs/adr/): the decision records.
- [AGENTS.md](AGENTS.md): the working rules for this repository.

## License

MIT. See [LICENSE](LICENSE). Copyright 2026 Minhao Zhang. The database and the semantic layer are separate repositories, and they are not included here.
