# AGENTS.md

**NEVER MODIFY THIS FILE WITHOUT EXPLICIT CONSENT BY USER.**

**THIS IS A GREENFIELD PROJECT. THERE IS ZERO USER. BE BOLD AND CHANGE THINGS.**

## Repo Structure

- This is a monorepo with a backend and a frontend.
- Record all the documentation under `docs/` and do NOT put anything in the root of the repo or in the root of `ui/`.
- The backend is in `src/governed_bi/` and the frontend is in `ui/`.

## Code Guidelines

- Use `uv` to run commands in a virtual environment. This ensures that the correct dependencies are used.
- If you are modifying any code relates to LangGraph or LangChain, make sure to load the skill before writing graph code.
- Check if the skills are up-to-date by using `npx skills update`
- Use `context7` to look up syntax. Use `firecrawl` to search for good internet search.

## Documentation Guidelines

- **Docstrings and comments in this repo are the design record. Read them, and keep them.** They
  carry the reasoning, the measurement and the rejected alternative that `docs/` has no room for,
  and several are load-bearing:
  `tests/feedback/test_the_store_keeps_the_promises_in_its_docstrings.py` exists because nine
  deliberate mutations survived the whole suite and *"every one of those is a sentence in a
  docstring"*, and 22 CLIs under `tools/` use `__doc__` as their `--help` text. Do not strip them.
- **Trust them, and verify the ones you rely on.** They are prose, so they can drift. When one is
  load-bearing for what you are about to change, check it against the code and *fix the prose in
  the same commit* — a comment that contradicts the code beside it is worse than no comment.
  Do not silently work around a stale one.
- Place all *documents* in the `docs/` folder — design documents, architecture diagrams, user
  guides. The root holds only `README.md`, `AGENTS.md`, `CLAUDE.md` and `LICENSE`; `ui/` holds only
  `ui/README.md`. Nothing else goes in either root.

## Testing Guidelines

- There is no need to write tests for everything.
- Always include a test for any new feature or bug fix.

## Git Guidelines

- Use a feature branch for any new work. The main branch should always be stable.
- Squash commits before merging to keep the history clean.
