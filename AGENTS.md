# This is a reference document for the agents in the project

**THIS PROJECT IS A GREENFIELD PROJECT WITH NO USERS. YOU CAN BE BOLD IN MAKING CHANGES.**

## Shape of projects

- Top level should contain only README.md and necessary configuration files.
- All the docs should live in docs/ directory.

## Coding guidelines

- No need to have a test for everything, unless it is a problem we encountered in the past and we want to avoid it in the future.
- Before you write any LangGraph, LangChain, DeepAgents related code, use the relevant skill to
  understand the basic concepts and principles. The names are exact and the Skill tool will not
  guess: `langgraph-fundamentals`, `langgraph-persistence`, `langgraph-human-in-the-loop`,
  `langgraph-cli`, `langchain-fundamentals`, `langchain-middleware`, `langchain-dependencies`,
  and `ecosystem-primer` for framework selection. They are installed under `.claude/skills/`
  and pinned by hash in `skills-lock.json`.
- **The skills describe the framework's defaults; this repo departs from several of them on
  purpose.** Where the two disagree the call-site docstring is the authority — it cites the
  turn that went wrong. The one that will bite hardest: there is no `RetryPolicy` anywhere,
  because re-running a node after it failed resamples a draw after seeing it, and the gate
  written to catch that (`n_re_served == 0`) counts nothing at the node level.
- No need to comment every line of code, but do comment on the overall structure and design decisions.

## External dependencies

Three sibling repositories. None of them is vendored, so a claim about any of them has to be
checked in the checkout rather than inferred from this tree.

- The UI can be found in the [governed-bi-ui](https://github.com/Minhao-Zhang/governed-bi-ui) repository, and is available locally at `../governed-bi-ui`.
- The data can be found in the [BIRD-Obfuscation](https://github.com/Minhao-Zhang/BIRD-Obfuscation) repository, and is available locally at `../BIRD-Data-Obfuscation`.
- **The corpus** — the semantic layer the engine serves — is the [BIRD-corpus](https://github.com/Minhao-Zhang/BIRD-corpus) repository, locally at `../BIRD-corpus`. Moved out of this tree on 2026-08-07 (D13); `corpora/` still exists and is gitignored, but it now holds only local experiment variants.

Notes on the corpus repo, because it is the one an agent is most likely to get wrong:

- **Point at it with `GOVERNED_BI_CORPUS_DIR=../BIRD-corpus`** (already set in `.env`). The value
  resolves against this repo's root, not the process's working directory.
- **It is the treatment identity of every measurement.** `corpus_content_hash` digests the tree,
  so a number is only reproducible if the corpus commit is known. Quote the commit alongside any
  figure, and do not write generated stores or scratch files into that checkout — anything beside
  the assets becomes part of the corpus's identity. Only a VCS's own bookkeeping (`.git/`,
  `__pycache__/`) is excluded.
- **`corpora/` is not a fallback for it.** Serving a variant out of `corpora/` produces a number
  nobody else can reproduce, because those trees are untracked and there is no curator module in
  `src/` to rebuild them. Promote a variant into the corpus repo before quoting anything from it.
- **Versioned is not the same as rebuildable.** The corpus is now in git; it still cannot be
  regenerated from anything committed. Do not describe it as reproducible-from-source.
