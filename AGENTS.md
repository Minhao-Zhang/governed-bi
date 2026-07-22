# This is a reference document for the agents in the project.

**THIS PROJECT IS A GREENFILED PROJECT WITH NO USERS. YOU CAN BE BOLD IN MAKING CHANGES.**


## Shape of projects

- Top level should contain only README.md and necessary configuration files.
- All the docs should live in docs/ directory.

## Coding guidelines

- No need to have a test for everthing, unless it is a problem we encountered in the past and we want to avoid it in the future.
- Before you write any LangGraph, LangChain, DeepAgents related code, use the relavent skill like `langgraph-fundementals` to understand the basic concepts and principles.
- When writing docs, always use Sonnet 5 subagent to generate the docs. After you do that, use the `humanizer` skill to refine the docs.

## Documentation language workflow

English is the source of truth. To save time, do not maintain the Chinese docs in lockstep while designing.

- **While the work is in progress** (designing, iterating, reviewing): edit and rely on the **English docs only**. Leave the Chinese versions untouched, even if they temporarily drift.
- **When the work is done** (before commit): align every affected Chinese doc to the finalized English, then refine each with the `qu-ai-wei` skill.
- "Ensure all docs are up to date before commit" therefore means: finalize English first, then bring the Chinese docs into alignment as the closing step, not continuously.

## External dependencies
- The UI is a separate project, you can find it in the [governed-bi-ui](https://github.com/Minhao-Zhang/governed-bi-ui) repository and it is available locally at `../governed-bi-ui`.
- The data is avaialble in `../BIRD-Data-Obfuscation` locally.
