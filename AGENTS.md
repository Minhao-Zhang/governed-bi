# AGENTS.md

**NEVER MODIFY THIS FILE WITHOUT EXPLICIT CONSENT BY USER.**

**THIS IS A GREENFIELD PROJECT WITH NO EXTERNAL USERS. BE BOLD AND CHANGE THINGS, AND PREFER DELETING TO ADDING.**

## Current priorities

Until 2026-11-20 all work follows `docs/reviews/2026-09-improvement-milestones.md`. Read its
"Rules for every milestone" before starting any change, and name the milestone the change serves.

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

- **Comments record decisions; git records history.** A comment or docstring states the *decision*:
  the reason, the constraint or measurement behind it, and the rejected alternative when a reader
  would otherwise re-propose it. The 2026-09-18 outside review, run on a comment-stripped mirror,
  re-filed four settled decisions as defects, so this layer is load-bearing. The story of how the
  code got here (dates, audit IDs, "used to", "until", earlier wrong versions) goes in the commit
  message. Write one to three sentences per decision; when you edit a longer one, trim it to its
  decision in the same commit. `tools/` CLIs use `__doc__` as `--help` text, so keep those
  complete.
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
