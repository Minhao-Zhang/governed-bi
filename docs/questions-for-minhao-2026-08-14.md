# Four questions before 21 Aug — DRAFT, not sent

> **Question 0 was added 2026-08-15 and is the urgent one.** It is the only question here that
> nobody but him can answer, because the evidence lives on his machine and nowhere else. The
> other three are decisions; this one is a fact we cannot recover.

## 0. Which 1 351 questions did `v3_fold`, `v4` and `v5` actually run?

**What we observed.** `BIRD-Obfuscation@upstream/main` is 15 commits ahead of the 2026-07-19
state this fork carries, and among them `efc655e` purges **2 739 questions with bad BIRD gold**,
`1711248` drops 11 databases and rebuilds the 80/20 split, and `684d055`/`22fe2a6` quantify
train/test leakage at 3.6% and cut it to 0.22%. Measured: our fork held 10 164 questions,
upstream now holds 6 743 — **3 421 purged, 0 added.**

The three published arms all report **1 351 questions**. That number is not derivable from either
version we can see:

| set | n |
|---|---:|
| our 7/19 `test_final` | 2 030 |
| ...filtered to the 57 schemas `BIRD-corpus` covers | 1 848 |
| upstream's `test_final` today | **1 351** |
| overlap between the old filtered set and today's | **280** |

So today's `test_final` has exactly the published count and shares 280 questions with the set we
would have guessed the arms used.

**Why we cannot answer it ourselves.** `arms.toml` pins the corpus twice — `corpus = "30872d3"`
and a mandatory `corpus_content_hash` — and records **nothing** identifying the question set. A
repo-wide search for `dataset_sha`, `question_set` or `qid_list_hash` returns nothing: no
measurement row carries a dataset identity. And the artifacts that would settle it
(`runs/eval/proxy_v3_fold_opus_high_corpus30872d3.jsonl` and its siblings, which `arms.toml`
itself cites) are **not on this machine** — `runs/` is gitignored, so they exist only where they
were produced.

**Why it matters.** Every headline figure in `README.md` and `docs/failure-modes.md` — v4's
accuracy, the 3.16× delivered-over-withheld ratio, the WrenAI contrast on "the same 1 351
questions" — is quoted against a question set that is not recorded anywhere committed and that
upstream has since replaced. A rerun today would produce the same `n`, a mostly different
population, and **pass every quotability gate**, because the gates check the corpus digest and
the knobs, both of which would match.

This is the same defect `arms.toml`'s own header exists to prevent, one field over. Its comment
reads: *"an arm that cannot be reconciled must say so, not report agreement."* It enforces that
for the corpus completely and for the question set not at all.

**What we would like:** the `question_id` list, or the dataset commit, that those three arms ran
against. One file or one SHA settles whether the published numbers can be reproduced or whether
they become historical and the baseline needs re-running.

**What we plan to do either way:** add a mandatory dataset identity to `arms.toml` alongside
`corpus_content_hash`, so the next arm cannot have this problem. We would rather he shaped that
field than inherit ours.

---

# Three questions from 2026-08-14

Written 2026-08-14, after merging `upstream/main` (102 commits) into this fork and porting the
frontend into `ui/`. Minhao is on leave 21 Aug – 14 Sep, so these want answering before then;
each one is a decision that costs us something to guess at, and all three are cheap for him.

Branch: `ryan/merge-upstream-0814`. Nothing here needs him to review our code — these are about
where the boundary between the two repos should sit.

---

## 1. Is `governed-bi-ui` retired, and should our UI live in `ui/`?

**What we observed.** `506ad9b` (8/11) copied `governed-bi-ui` into this repo as `ui/`, and the
standalone repo took no commits after that day. We read that as retirement and ported our
frontend into `ui/` accordingly — it is now 8 added files and 14 edits against his tree.

**Why it matters to ask rather than assume.** If he is still developing in the standalone repo,
our port put our work on the branch that will fall behind, and the merge surface doubles instead
of halving.

**What we would like:** confirmation that `ui/` is the only frontend going forward, and that the
standalone repo can be archived.

## 2. Does he accept the `analyst` prompt renumbering?

**What happened.** Both lineages branch from `v2` and share no text after it: his `v3`/`v4` add the
result-shape, DISTINCT and star rules; ours add a ranking clarification, `basis`, and the language
rules. Independently, `v3`, `v4` and `v5` had each come to name **two different prompts** — which
is exactly what hashing a variant's name exists to prevent.

**What we did.** Kept his numbering, because the McNemar figures in `register/prompts.py` are
published against it (over-projection 107 → 18, p = 0.0008; `r_star_projection` 35/29 → 2/2), and
renumbered ours `v3–v6` → `v6–v9`. `v9` is the default and carries our three rules only.

**We also composed them as `v10`** — `v9` plus exactly the suffix `v4` adds to `v2`, with a
conformance test pinning the relationship byte for byte. It is **not** the default: his numbers
were measured against his `v2` base, and `v9` is a different base (one of our rules changes when
the agent stops to ask instead of answering, so the result-shape rule may be reached less often or
interact). Promoting it needs its own arm.

**What we would like:** either "the renumbering is fine", or the numbering he wants — and, if he
has an opinion, whether he thinks `v10` is worth an arm before we spend one.

## 3. Should `curator/` be contributed upstream?

**What it is.** The admin-side semantic-layer curation this fork added: the clarifications ledger,
the Setup Wizard's gap detectors, the Enhancer's dedup/conflict handling, and the four UI tabs
that drive them. It is the largest thing we carry that he does not, and it is what makes every
upstream merge a real merge rather than a fast-forward.

**Why it might belong upstream.** It reads the corpus through his own asset schema and writes
drafts through his `corpus/store.py`; nothing in it is UtkuAI-specific. If it lived upstream we
would stop carrying the merge surface, and his own users would get the admin path.

**Why it might not.** It is opinionated about a workflow he may not want in the core, and it is the
reason `curator/` ↔ `serve/` is currently an exempted import cycle on our side — contributing it
would hand him that too, so the honest version of this offer includes lifting the shared
governed-read helpers below both layers first.

**What we would like:** a yes/no on whether he wants a PR, so we know whether to invest in the
layering fix as a contribution or just as our own cleanup.

---

## One thing to tell him rather than ask

While porting, `npm run check:api` (his checker, against a live engine) reported that his client
discards `can_curate_corpus`, `enable_clarification_to_draft`,
`enable_structured_percentage_check`, `checkpoint_durable` and
`hitl_survives_process_restart` from `/capabilities`, plus `label`/`kind`/`provenance_status` on
`/graph` nodes and `rules` on `/schema/{table}`. The last two groups are his engine offering more
than his client reads, which is benign. The first two of ours are the ones that mattered: without
`can_curate_corpus` declared, every curation tab stays unmounted, which is how a working feature
looks like a missing one.

His checker's own docstring says the gap "has cost this project five tabs". It caught a sixth.
