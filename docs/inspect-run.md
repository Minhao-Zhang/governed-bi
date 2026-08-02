# Experiment inspector

An offline, read-only web view for reading what the agent actually did on an eval run —
per question, per arm, per schema. It is a **debugging tool for experiments**, not the
chat frontend (that lives in `../governed-bi-ui`). Use it after a ladder finishes to
open a failing question and read its whole trajectory: the question, the gold SQL, the
generated SQL, the verdict, the ordered tool calls, and the rail stage timeline, with
timing / token / cost fields where the run recorded them.

It reads the `run.sqlite` that [`scripts/load_run_db.py`](../scripts/load_run_db.py)
produces. Nothing new is written to a run directory except that `run.sqlite`.

## Start it

The one-liner — point it at a run directory; it builds `run.sqlite` if it is missing and
opens a browser:

```bash
uv run python scripts/inspect_run.py --run-dir runs/datalake/luna-max/20260801T-ladder
```

Serve a database you already built (including a shared `runs.db` from
`load_run_db --discover`, in which case the run picker lists every run it holds):

```bash
uv run python scripts/inspect_run.py --sqlite runs/datalake/luna-max/20260801T-ladder/run.sqlite
```

Then browse to <http://127.0.0.1:8765/>.

### Options

| flag | meaning |
|---|---|
| `--run-dir DIR` | a run directory; builds `DIR/run.sqlite` via the loader if absent |
| `--sqlite FILE` | serve an existing `run.sqlite` / `runs.db` as-is |
| `--rebuild` | with `--run-dir`, rebuild `run.sqlite` even if it exists |
| `--host` | bind address (default `127.0.0.1`, localhost only) |
| `--port` | port (default `8765`; `0` picks a free one) |
| `--no-browser` | do not open a browser (for a headless box) |

Exactly one of `--run-dir` / `--sqlite` is required. Stop it with `Ctrl-C`.

### Building the database by hand

`--run-dir` calls the loader for you; to do it explicitly (the conventional per-run
export):

```bash
uv run python scripts/load_run_db.py runs/datalake/<...>/<ts> --db runs/datalake/<...>/<ts>/run.sqlite
```

## What you can read

- **Overview** — per-arm EX, crash rate (kept out of the EX denominator on purpose:
  a crash is our bug, a wrong answer is the model's), pick/route hit counts, latency,
  tokens, cost. Plus the loader's degeneracy notes for the run.
- **List** — every turn, filterable by arm / schema / outcome / verdict and searchable
  across question id, question text, generated SQL, gold SQL, and error text. Sort by
  any numeric column. A green/red/grey dot is pass/fail/ungraded at a glance.
- **Detail** — one turn in full: the meta grid, the question and evidence, gold vs
  generated SQL, the **tool trajectory** (the governance ledger — each tool call with
  its verdict, SQL, and row count), the tool-call histogram, the **stage timeline**
  (each rail step with status and timing), and the licensed/used tables. The raw
  generation row is one disclosure away. Sibling chips jump to the same question on
  another arm.

**Deep links.** A turn's URL carries `#<arm>/<question_id>` (e.g.
`http://127.0.0.1:8765/#curated/1016`), so a specific turn is shareable and
bookmarkable.

## Honest degradation

The loader is built to read half-broken runs, and the inspector follows it:

- No `stage_events.jsonl` → the trajectory's stage timeline says so; everything else
  still works. Capability badges in the header (`trajectory ✓` / `no stage events`,
  `gold SQL ✓`) tell you up front what this run can show.
- No `questions.jsonl` → gold SQL, question text, and evidence read "not recorded"
  rather than vanishing.
- A run that never finished, or an arm with no generations, still opens; the overview
  carries the note.

## Notes on safety and setup

- **Read-only.** The database is opened `mode=ro`; the inspector cannot write the run
  it inspects. It binds localhost only by default.
- **No SQL from the browser.** Every query in the server is parameterised and every
  sort/column name is checked against an allow-list, so a crafted query string cannot
  reach the database as code. Result content is rendered with `textContent`, never
  `innerHTML`, so markup inside a BIRD question is shown, not executed.
- **Windows.** Pure `uv run`, no shell tricks, forward-or-back slashes both fine. If a
  `PYTHONPATH` from another tool is set in your shell, clear it before running the test
  suite (`PYTHONPATH= uv run pytest tests/test_inspect_run.py`).

## Tests

```bash
uv run pytest tests/test_inspect_run.py
```

Covers injection-proofing, the sort allow-list, read-only enforcement, static-file
traversal blocking, graceful degradation, and building `run.sqlite` from a run dir.
