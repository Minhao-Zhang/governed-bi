# 0018: Every answer states its assumptions, and the server checks them

- **Status:** Accepted (2026-10-19).
- **Deciders:** project owner.
- **Scope:** what the model must say about the choices behind an answer, how the server checks
  that against the SQL, and when a guess turns into a clarifying question. How the page shows it
  is [0019](0019-one-plain-chat-page.md).
- **Related:** [0013](0013-the-declared-abstention-policy.md) is unchanged (§3 says why);
  [0017](0017-a-served-chat-app-over-a-layer-the-user-brings.md) is the scope.

## Context

A question in plain English leaves gaps: whether closed businesses count, which year "last year"
means, whether a business with several rows counts once, how ties in a top ten are broken. The
engine fills every gap, and today it fills them silently. A number can be right for the question
the engine answered and wrong for the one the person asked, and nothing on the screen tells them
which.

The DetentAI fork (PR #3, 2026-08-18) measured this. It declared an `assumptions` field on the
answer, and nothing ever filled it: on two answered turns it was empty, and one of them averaged
every row of a table, including rows a certified rule says to exclude, with no statement that it
had. A field the model may leave empty is not enough, which is why this ADR makes the list required
and checks it against the SQL.

## Decision

### 1. The model states every assumption

The agent finishes every turn by calling a new `answer` tool, which takes the answer text and a
required `assumptions` list. The prompt asks for every
choice the answer depends on, including defaults that seem too obvious to mention: time ranges,
how missing values are treated, distinct counting, tie-breaking, rounding, and which rows a join
keeps. Each entry has:

- `statement`, one sentence in the person's words ("Closed businesses are included");
- `about`, the phrase of the question it concerns, or none;
- `basis`, one of `layer`, `convention` or `guess`;
- `asset_id`, required when `basis` is `layer`: the definition it rests on.

### 2. The server checks the claims

The `answer` tool runs these checks in its own body, so a claim it rejects goes back to the agent
as a reply it can correct, the way `run_query` already returns a refused statement. Today the
answer is the agent's last prose message, which has nowhere to hold a list; a tool also fits how
the other five tools write into declared state.

- A `layer` assumption whose `asset_id` does not exist, or was not retrieved for this turn, is
  changed to `guess`. The model cannot claim backing it did not have.
- The server parses the executed SQL with sqlglot and looks for choices the list does not mention:
  a filter on a value the question never named, an inner join that drops unmatched rows,
  `DISTINCT`, `LIMIT`, or a column the layer marks as suspect. Each one is added to the list with
  `basis` set to `found_in_query`.

These checks are deterministic and need no model call. They run before the answer is delivered and
are stored on the turn record with the rest of the answer.

### 3. A guess that changes the answer is a reason to ask

When a guess concerns a phrase the layer defines in more than one way, the engine asks a
clarifying question instead of answering. A phrase is defined more than one way when two or more
term assets retrieved for the turn match it, by name or synonym, and bind to different targets;
a single term holds only one binding. The `answer` tool enforces this in code: it refuses an
answer that holds such a guess and tells the agent to call `ask_user`, whose existing
`data_definition` basis covers the case. The abstention policy of 0013 does not change, because it
runs before the agent and cannot see assumptions.

BIRD-corpus has only two such phrases ("predominant" in `mondial_geo`, "name" in `superstore`), so
this rule is tested on a fixture corpus and reported, not measured, on BIRD.

### 4. A guess can be changed

The page offers "Change" on each guess. It sends a follow-up in the same conversation with the
corrected assumption written into the question, so the person fixes the question rather than
retyping it.

### 5. It is measured

On BIRD-Obfuscation the gold SQL shows what each question meant, so every eval run reports:

- how many assumptions per answer, by basis;
- accuracy of answers with at least one guess against answers with none;
- how many `found_in_query` entries the model failed to state.

If answers with guesses are wrong more often, that is a measured case for asking more often.

## Consequences

- The answer tool's schema, the prompt in `register/prompts.py` and the turn record change, so the
  first run after this lands is a new baseline, not a comparison with the old one.
- A longer answer costs output tokens. The list is short for most questions, and the measurement
  will show the cost.
- The SQL parse used here is the same one the page's "where each part came from" uses (0019), so
  it is written once.

## Rejected

- **Structured output on `create_agent` (`response_format`).** Less code, but its retries are
  generic, so the server's checks would run after the agent rather than inside its loop.
- **A second model call that lists the assumptions afterwards.** The assumptions would come from
  a model that did not make the choices.
- **Asking the model for a confidence score.** [0007](0007-http-surface-and-the-ui-contract.md)
  forbids a trust field, and a number invites false precision. Named assumptions can be checked.
- **Trusting the model's list without the SQL check.** The point is to catch the choices it did
  not mention.
- **Showing only guesses.** The backed assumptions are what let a person see the answer read their
  words correctly.
