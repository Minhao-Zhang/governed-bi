# governed-bi

governed-bi is a chat app that answers questions about a database. You bring the semantic layer, which names the tables, columns, joins, metrics, and business words the questions use. Every answer states the assumptions behind it, and every query is checked before it runs.

Who may see which rows is a role on the database. This app has no setting for that.

## An example

This turn was served by the app over the BIRD databases, whose tables and columns are renamed, often into another language. The basketball database calls coaches `entraineurs` and awards `recompenses_entraineurs`. The semantic layer is what maps "coach" and "Coach of the Year" onto them.

> **How many coaches from CHI won Coach of the Year?**
>
> 4 coaches, counting distinct coaches who won a Coach of the Year award in a season they coached a CHI franchise team.
>
> To answer this, I assumed:
>
> - I included both NBA and ABA Coach of the Year awards because no league was specified. *From your definition: recompense.*
> - I counted only awards matching the coach's CHI team stint in the same season. *From your definition: annee.*
> - I interpreted CHI as a franchise identifier, not as a single-season team identifier.
> - With no season specified, I counted across all available seasons.
> - I counted each coach once across seasons and stints; shared award winners count as separate coaches.
> - The statement compares award.recompense to 'ABA Coach of the Year', which the question does not mention. *The query does this, though you didn't ask for it.*
> - Unmatched rows between professional_basketball.equipes and professional_basketball.recompenses_entraineurs are dropped. *The query does this, though you didn't ask for it.*
>
> Details

The turn listed eleven assumptions; seven are shown here. Two cite the semantic layer. Four are conventions the model chose. The rest the server found by reading the SQL, and the model had not stated them. The one about ABA awards is the choice a reader would want to know about: someone who meant only the NBA award now sees that the count includes the ABA one too.

Details opens the SQL that answered, each failed attempt above it with its SQL and its error, the checks, the tables used, the model, and the time taken. This is the SQL:

```sql
SELECT COUNT(DISTINCT coach."id_entraineur")
FROM "professional_basketball"."entraineurs" AS coach
INNER JOIN "professional_basketball"."equipes" AS team
  ON coach."annee" = team."annee" AND coach."id_equipe" = team."id_equipe"
INNER JOIN "professional_basketball"."recompenses_entraineurs" AS award
  ON coach."annee" = award."annee" AND coach."id_entraineur" = award."id_entraineur"
WHERE team."id_franchise" = 'CHI'
  AND award."recompense" IN ('NBA Coach of the Year', 'ABA Coach of the Year')
LIMIT 200001
```

## Results

The app was measured on BIRD-Obfuscation: 1,351 questions over 57 renamed databases, graded by comparing the result rows with the gold query's rows. Each configuration was run twice on all 1,351 questions. The agent writes the SQL and the answer. The utility calls are the smaller steps, such as rewriting the question for each retrieval channel. Embeddings are `text-embedding-3-large` in both.

- **Luna**, the default: the agent on `gpt-6-luna` at reasoning effort `xhigh`, the utility calls on `gpt-6-luna` at `medium`. Run on 2026-10-23.
- **Sol**: the agent on `gpt-6.1-sol` at `high`, the utility calls on `gpt-6.1-sol` at `low`. Run on 2026-10-26.

| | Luna A | Luna B | Sol A | Sol B |
|---|---|---|---|---|
| Execution accuracy | 0.553 | 0.544 | 0.548 | 0.539 |
| Accuracy on the held-out databases | 0.532 | 0.516 | 0.535 | 0.527 |
| Every gold table retrieved, on turns that did not pause | 0.965 | 0.971 | 0.952 | 0.948 |
| Ended in a clarifying question | 13.3% | 13.0% | 11.3% | 10.8% |
| Cost per question | $0.0027 | $0.0028 | $0.0321 | $0.0324 |
| Cost per run of 1,351 questions | $3.71 | $3.75 | $43.41 | $43.83 |
| Median time per question | 41 s | 43 s | 20 s | 20 s |
| Questions the two runs disagreed on | 152 (11.3%) | | 99 (7.3%) | |

Sol costs about 12 times as much and scores the same: averaged over two runs each, it is 0.4 points lower, with a 95% interval from 2.2 points lower to 1.2 points higher. It is twice as fast and more repeatable. Luna stays the default.

The assumptions carry signal on Luna. Its answers that included a guess were right 46% to 50% of the time, against 67% to 68% with no guess, so "This is a guess" marks the answers worth checking. Sol almost never marks a guess, so on Sol the marker says little. On both, the server added at least one choice the model had not stated to more than 80% of answers.

Most of the loss is not retrieval. On Luna, routing put the right database in the top three for 97% of turns. Of run A's 604 wrong questions, 30% paused on a clarifying question, and most of the rest were answered with SQL the grader rejected. Read by hand, about two thirds of those return the right rows in a different layout, differ on `DISTINCT` or ties, or have a doubtful gold. [Error analysis](docs/error-analysis.md) follows every question through the pipeline.

[Measurement](docs/measurement.md) gives every figure by half, the conditions of the runs, and how to run one.

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

## The pages

The app has three views.

- **Chat**, at `/`. The open conversation is `/?thread=<id>`.
- **Conversations**, at `/conversations`. Every conversation with how its last turn ended: "Answered", "Couldn't answer", "Waiting for you", "Stopped" or "Failed". It can filter by ending.
- **Semantic layer**, at `/layer`. The schemas, and the chosen one's Ossie model in an embedded [ossie-viewer](https://github.com/Minhao-Zhang/ossie-viewer). It shows no rows from the database.

The sidebar has "New conversation", links to the other two views, and the conversations grouped by day, each with how it ended. One that is waiting on a clarifying question is listed first, under "Waiting for you".

## How an answer reads

An empty conversation says "What do you want to know?" and lists up to three example questions from the corpus. Clicking one fills the composer. Under the composer, the page says "Read-only. Every query is checked before it runs."

While a turn runs, one grey line names the current step, such as "Finding your definitions…" or "Checking the query…".

An answer shows the text, then the rows. When the page holds fewer rows than the query returned, the caption reads "Showing 20 of 1,204 rows" for those counts. Then "To answer this, I assumed:" and one bullet per assumption. An assumption the semantic layer backs ends with "From your definition:" and the definition's name. A guess ends with "This is a guess." and a Change button. Change sends the same question again with the correction appended, in the form "(Assume instead: …)". Something the server found in the SQL, and the model did not state, ends with "The query does this, though you didn't ask for it." A convention is the statement on its own. Copy copies the text and the table. Details opens the SQL, each failed attempt, the checks, the tables the turn used, the model, and the time taken.

A clarifying question is the question, a sentence saying why, outlined choices, and a box if you would rather type. Defer lets the engine choose and carry on. Decline stops the turn.

A refusal says "I can't answer this from your data.", then the reason, then nearby questions from the corpus. Choosing one fills the composer.

## The semantic layer

The corpus is one Apache Ossie model per schema, so any Ossie tool can open it. What Ossie has no field for is kept in a `GOVERNED_BI` extension. The layout is in [Corpus format](docs/corpus-format.md).

## Repository

`src/governed_bi/` is the engine: the corpus, retrieval, the SQL checks, the chat graph, and the evaluation. `ui/` is the web app. `docs/` is the design. `tools/` holds the data lake evaluation and the checks that are not part of the server.

## Documentation

- [Usage](docs/usage.md): install, configure, serve, evaluate, and export.
- [Corpus format](docs/corpus-format.md): the semantic layer, one Ossie model per schema.
- [Architecture](docs/architecture.md): how a question becomes an answer.
- [Failure modes](docs/failure-modes.md): how answers go wrong.
- [Measurement](docs/measurement.md): running an evaluation and reading the result.
- [Error analysis](docs/error-analysis.md): where each wrong answer was lost, for Luna and Sol.
- [Glossary](docs/glossary.md): the words this repository uses.
- [Design decisions](docs/design-decisions.md): where the decisions are written.
- [Open work](docs/open-work.md): what is unfinished.
- [ADRs](docs/adr/): the decision records.
- [AGENTS.md](AGENTS.md): the working rules for this repository.

## License

MIT. See [LICENSE](LICENSE). Copyright 2026 Minhao Zhang. The database and the semantic layer are separate repositories, and they are not included here.
