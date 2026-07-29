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

English is the source of truth. **Only a small core set has a Chinese twin** — the
docs a Chinese reader enters through:

```
README.zh.md                 corpus/README.zh.md
docs/README.zh.md            data/README.zh.md
docs/architecture.zh.md      data/generated/README.zh.md
docs/design-decisions.zh.md
docs/glossary.zh.md
docs/usage.zh.md
```

Everything else — ADRs, the LLM-call traces, `measurement`, `open-work`, the
runbooks under `plans/`, `viz`, `asset-schemas`, `analyst`, `curator`,
`prompt-experiments`, `references`, `corpus-authoring` — is **English only**. Do
not create a `.zh.md` for them; a mirror that has to be re-translated on every
edit costs more than it returns on docs that are read while working in the code.
Those files carry no language-switcher line, so its absence is the signal.

- **While the work is in progress**: edit the **English docs only**. Let the core
  Chinese set drift.
- **When the work is done** (before commit): align the affected docs *in the core
  set* to the finalized English, then refine each with the `qu-ai-wei` skill.
  Match the existing zh house style rather than imposing a new one (this repo
  uses straight quotes next to CJK).
- A link from a Chinese doc to an English-only doc points at the `.md` — that is
  correct, not a gap.

## External dependencies
- The UI is a separate project, you can find it in the [governed-bi-ui](https://github.com/Minhao-Zhang/governed-bi-ui) repository and it is available locally at `../governed-bi-ui`.
- The data is avaialble in `../BIRD-Data-Obfuscation` locally.
