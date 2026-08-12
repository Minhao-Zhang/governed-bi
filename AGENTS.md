# AGENTS.md

**THIS IS A GREENFIELD PROJECT. THERE IS ZERO USER. BE BOLD AND CHANGE THINGS.**

## Repo Structure

- This is a monorepo with a backend and a frontend.
- Record all the documentation under `docs/` and do NOT put anything in the root of the repo and ui repo.
- The backend is in `src/governed_bi/` and the frontend is in `ui/`.

## Code Guidelines

- Use `uv` to run commands in a virtual environment. This ensures that the correct dependencies are used.
- If you are modifying any code relates to LangGraph or LangChain, make sure to load the skill before writing graph code.
- Check if the skills are up-to-date by using `npx skills update`
- Use `context7` to look up syntax. Use `firecrawl` to search for good internet search.

## Documentation Guidelines

- Do not include nor trust any docstrings in the code. They are often outdated and incorrect.
- Place all documentation in the `docs/` folder. This includes any design documents, architecture diagrams, and user guides.

## Testing Guidelines

- There is no need to write tests for everything.
- Always include a test for any new feature or bug fix.

## Git Guidelines

- Use a feature branch for any new work. The main branch should always be stable.
- Squash commits before merging to keep the history clean.
