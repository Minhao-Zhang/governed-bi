# Walkthrough: from clone to your first governed answer

_[English](walkthrough.md) · [简体中文](walkthrough.zh.md)_

A start-to-finish tour: install the repo, validate the example corpus, and ask
your first question — both over the HTTP API and from Python. Everything here runs
**offline** (no API key, no network) against the committed `beer_factory`
database, using the deterministic template SQL generator. A final optional step
shows how to switch on a live model.

By the end you'll have seen the two things that make this more than a
text-to-SQL demo: a **governed answer** (with its two-axis reliability stamp and
the exact SQL) and a **refusal** (fail-closed when a question is out of scope).

## 0. Prerequisites

- [uv](https://docs.astral.sh/uv/) (the package manager / runner)
- Python 3.13 — uv fetches it automatically if you don't have it
- `git`

Optional, only for the live-model step at the end: an OpenAI API key.

## 1. Clone and install

```bash
git clone https://github.com/Minhao-Zhang/governed-bi.git
cd governed-bi
uv sync
```

`uv sync` creates `.venv`, installs the core dependencies, and installs
`governed_bi` in editable mode. Confirm it worked:

```bash
uv run python -c "import governed_bi; print(governed_bi.__version__)"
```

The committed `data/bird/beer_factory.sqlite` (a real BIRD database, CC BY-SA
4.0) means the full pipeline runs immediately — nothing to download.

## 2. Validate the corpus

The corpus is the governed semantic layer: Git-tracked YAML assets + Markdown
skills. The validator checks ID conventions and reference integrity — a green run
is the "done-enough" signal for a corpus (D9).

```bash
uv run python -m governed_bi.corpus.cli
```

Expected output:

```
CI green: 16 assets, 1 skills, 0 findings.
```

(This same command runs in CI on every push.)

## 3. Run the tests

```bash
uv run pytest -q
```

The suite is green offline. With the harness and API extras installed
(`uv run --extra agents --extra api pytest`) all **321** tests run, including the
LangGraph-equivalence and the HTTP API tests; without them, a handful skip.

## 4. Ask your first question

There are two ways in: the HTTP API, and a few lines of Python. Both drive the
exact same governed server flow.

### 4a. Over the HTTP API (recommended)

```bash
uv run --extra api uvicorn --factory governed_bi.api:create_app
```

This serves the governed API at http://localhost:8000 (interactive docs at
http://localhost:8000/docs). Ask your first question by POSTing to `/chat`:

```bash
curl -s localhost:8000/chat -H 'content-type: application/json' \
  -d '{"question":"What is the total revenue?"}'
```

You'll get a governed answer whose JSON carries:

- **tier: governed**
- **safety_clearance: true**  ·  **semantic_assurance: certified**
- the answer: `total_revenue = 18496.0`
- the SQL it ran: `SELECT SUM(PurchasePrice) AS total_revenue FROM "transaction"`
- a **provenance** trace (route, metric, tables, join confidence)

Now ask something the semantic layer does **not** cover:

```bash
curl -s localhost:8000/chat -H 'content-type: application/json' \
  -d '{"question":"How many employees work at the factory?"}'
```

Instead of guessing, the system **refuses**:

- **tier: refused**
- an escalation message: _"not answerable from this data - contact &lt;owner&gt;"_
- no SQL, no number

That refusal is the point: there is no employee/payroll data in scope, so a
governed system says so rather than inventing a plausible-but-wrong number.

The API is stateless — to keep a conversation going, send prior turns back as
`history` (with a stable `session_id`) on the next `/chat` request.

### 4b. From Python

The same flow as a small API you could embed in your own app:

```python
from governed_bi.config import Settings, Environment
from governed_bi.corpus import load_corpus
from governed_bi.gateway import SqliteConnector, Gateway, Identity
from governed_bi.server import answer_question

corpus = load_corpus("corpus", db="beer_factory").for_server()
conn = SqliteConnector("data/bird/beer_factory.sqlite")

ans = answer_question(
    "What is the total revenue?",
    Identity(user="demo", all_access=True),
    corpus=corpus,
    gateway=Gateway(conn),
    settings=Settings.for_env(Environment.dev),
    session_id="demo",
)
print(ans.tier.value)            # governed
print(ans.safety_clearance)      # True
print(ans.semantic_assurance.value)  # certified
print(ans.sql)                   # SELECT SUM(PurchasePrice) AS total_revenue FROM "transaction"
print(ans.text)                  # total_revenue = 18496.0
conn.close()
```

## 5. What you're looking at

- **The two-axis stamp is the honest part.** `safety_clearance` is a gate — did
  the SQL pass all five guardrail layers and execute as the requesting principal?
  `semantic_assurance` (`certified` / `heuristic` / `unverified`) is *how
  well-grounded* the answer is. They are kept separate on purpose: a query can be
  perfectly safe and still be the wrong computation, so "safe" is never read as
  "correct". (See [server.md](server.md).)
- **You can audit the SQL.** The model's output is treated as untrusted; what
  actually ran is shown, and it only touches columns/tables the corpus licenses.
- **The refusal is a feature.** Missing coverage, a tripped guardrail, or a
  curated out-of-scope pattern all fail closed. The counterweight — not refusing
  answerable questions — is measured by the eval's false-refusal rate.

## 6. (Optional) Go live with a real model

Offline, the deterministic template generator answers metric/KPI questions and
ignores conversation. To use a real model — which enables free-form SQL and
context-aware follow-ups in chat — set a key and install the `agents` extra:

```bash
export OPENAI_API_KEY=sk-...        # read from the env, never stored in the repo
uv run --extra agents --extra api uvicorn --factory governed_bi.api:create_app
```

Prefer a file? Copy `.env.example` to `.env` at the repo root and put the key
there instead of exporting it — it's loaded on import and never overrides a
variable already set in your shell. `.env` is git-ignored.

The model is `gpt-5.5` at low reasoning effort (configured in
[`governed_bi.toml`](../governed_bi.toml)), called through LangChain's
`ChatOpenAI`, which routes reasoning models to the OpenAI **Responses API**. Over
`/chat`, follow-ups now resolve against the conversation (prior turns are fed back
through the engine's working memory), the answer is phrased in **natural
language**, and the executed rows are returned in the response's **result** field.
Offline, the answer text falls back to a compact render, but the `result` rows are
still present — the executed rows are always carried on the answer.

For a scripted live check that prints execution accuracy, refusal, and
decoy-touch over `beer_factory`, run:

```bash
uv run --extra agents python scripts/live_smoke.py
```

## Next steps

- [Usage](usage.md) — the fuller quickstart (validate CLI, corpus API, gateway).
- [Corpus authoring](corpus-authoring.md) — write and validate your own assets.
- [System overview](system-overview.md) → [Architecture](architecture.md) —
  the design behind all of this.
- [Server](server.md) — the serve flow, guardrails, and reliability stamp in depth.
