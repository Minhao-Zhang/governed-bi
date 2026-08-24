# 0016: Gating the corpus repository — "did this commit add a conformance finding?"

- **Status:** Accepted; **built, committed in the corpus repository, and never executed**
  (2026-08-24). Built here on `design/return-path`: `tools/check_corpus_delta.py` (git baseline),
  `tools/check_ratchet.py` (pin-file baseline, manual), `tools/conformance_findings.py` (the shared
  arithmetic), and 23 tests over the two policies
  (`tests/conformance/test_a_commit_does_not_add_a_finding.py`, 13;
  `tests/conformance/test_the_ratchet_only_turns_one_way.py`, 10). Built there:
  `.github/workflows/conformance.yml` in `BIRD-corpus`, one job, running the delta tool on every
  push and pull request. **Not observed:** that workflow has never run. The two commits carrying it
  (`bd61b9fb`, `2000cc3a`) are local — `origin/main` in that repository is at `74ff80c4` — and the
  GitHub Actions run count for `Minhao-Zhang/BIRD-corpus` is **0**. Every claim below about what
  the job does on a runner is read from the workflow and from local runs of the same command, not
  from a build log. **Deliberately not built:** nothing in any CI runs the ratchet (§Consequences 3),
  and the workflow's `ref:` pin is a temporary lie with no owner yet (§Consequences 2).
- **Deciders:** project owner + design session (2026-08-24). The design that was proposed was
  larger: a committed pin file as the baseline, a pinned engine SHA in the corpus workflow, and
  tiered jobs (a cheap check on push, the full rule set elsewhere). The owner's pushback was that
  the corpus CI should be **minimal**, because the structural facts about a corpus are correct by
  construction and re-verified at execution time anyway. That argument is what deleted all three:
  the pin file as a CI baseline, the engine version pin, and the tiering. What survived is one job
  asking one question.
- **Scope:** *what a commit to the corpus repository is gated on*, and where the checker lives.
  **Not** what the rules are (that is ADR 0005's field spec, executed by
  `tools/check_corpus_conformance.py`), not whether the corpus is any good, and not this
  repository's own CI.
- **Related:** [0015](0015-the-return-path.md) — its *What this ADR does not cover* names "who owns
  the corpus repository's CI" as unanswered and says the served corpus "has no CI at all". This
  answers that item; 0015's own text is left as written.
  [0005](0005-v2-memory-layer-and-faceted-retrieval.md) §1.2 is the field spec every rule is a
  predicate over. `corpus/hash.py::corpus_content_hash` and `corpus/identity.py::_is_tooling` own
  the treatment identity this gate had to avoid moving.

---

## Context

### 1. The corpus is a repository the engine does not own, and the checker is in the engine

`../BIRD-corpus` is a separate git repository (public; 7,363 tracked files). It holds the treatment
that every measured number is pinned to. The tool that judges it —
`tools/check_corpus_conformance.py`, 22 rules — is in **this** repository, because the rules are
statements about what the engine will do with an asset: V16 measures a table plus its folded column
roster with `serve/context.py`'s own `_structural_line` and `_roster_entry`, V17a parses a metric
expression at the dialect `govern/` parses generated SQL at, V21 runs `govern/guard.py`'s own
`GUARD_RULES` over model-visible text. A second copy of any of those in the corpus repository would
be a second answer free to disagree with the first.

So the gate and the tree it gates are in different repositories, and one of them has to reach across.

### 2. A zero-findings gate would have been rejected on its first run

Measured on `../BIRD-corpus` at `2000cc3a`, 2026-08-24, with
`uv run --frozen python tools/check_corpus_conformance.py --corpus-dir ../BIRD-corpus --json`:

| | findings | identities |
|---|---|---|
| V17a — a metric expression parses as SQL at the engine's dialect | 107 | 85 |
| V17b — every identifier in the expression resolves on `base_table` or a declared join | 17 | 15 |
| V21 — model-visible text passes `guard`'s `GUARD_RULES` | 1 | 1 |
| **total** | **125** | **101** |

The other 19 rules report zero. V17a's 107 findings fall across **85 of the corpus's 478 metric
assets** — the corpus holds 13,304 assets in seven types, and metrics are the only type a V17a
finding can be about.

A gate demanding zero rejects that corpus on its first run. What happens next is not a fix, it is a
waiver — and a waiver is how a real finding goes green afterwards. The answerable question is
narrower: whatever was already wrong may stay wrong, and nothing new may arrive.

### 3. Findings and identities are different nouns, and the difference is 24

A finding's **identity** is `(rule, where)`, where `where` is the `file:asset` the finding is about.
125 findings live on 101 identities, so 24 findings are invisible to an identity-only comparison: an
asset already carrying a V17a finding could take on any number more without the set growing by one
entry. Every baseline in this design therefore carries a **count** per identity, not just the
identity. Verified as a behaviour rather than argued: with a second `DIVIDE` nested into a metric
that already had one, the delta tool exits 1 reporting `1 -> 2` with 101 identities on both sides
(`test_a_second_finding_on_the_same_identity_fails_with_the_count`).

### 4. Three rules answer "not evaluated", and that reads exactly like "passed"

V11, V12 and V15 need the obfuscation dataset's manifests (`trap_manifest.json`,
`trap_table_manifest.json`, `schema_rename_map.json`, `test_final.jsonl`). Without them
`check_corpus_conformance.py` puts them in a `not_evaluated` map and reports zero findings for
them — which is the same output a clean corpus produces. V12 is the held-out-split leakage rule.
On a laptop a missing manifest is normal. In CI it is the failure the gate exists to prevent.

## Decision

### 1. The gate asks whether *this commit* added a finding, and nothing else

`tools/check_corpus_delta.py` runs conformance twice — once on the working tree, once on a base
revision — and compares the two `(rule, where) -> count` maps. Exit 0 if no finding was added; exit
1 if an identity is present at head and absent at base, or a shared identity's count grew; exit 2 if
it could not run at all.

It is a **delta** gate and its own failure message says so: it does not claim the corpus is clean.

### 2. The baseline is a git revision, not a committed pin file

`--base` takes any ref resolvable in the corpus repository. CI passes
`github.event.pull_request.base.sha` on a pull request and `HEAD^` on a push.

The base tree comes from `git worktree add --detach` into a temp directory, removed in a `finally`
on every exit path including the failing ones. Not a checkout in place, which needs a clean tree and
moves the operator's HEAD; not a directory inside the corpus, which conformance would then walk. A
leaked worktree registers a directory in the corpus repository's `.git/worktrees` on every red
build, and that repository is not this tool's to litter.

`_resolve_ref` uses `rev-parse --verify ^{commit}` rather than a bare `--verify`, so a tag or a tree
fails naming what the operator typed instead of failing two steps later inside `worktree add`.

The pin-file design was built first and rejected; the three measured reasons are in §Rejected
alternatives 1.

### 3. Two tools, one arithmetic, two policies — and the asymmetry is deliberate

`tools/conformance_findings.py` owns how conformance is run, how a finding becomes an identity with
a count, and what `added`/`grew`/`closed`/`shrank` mean. Before it existed each tool carried its own
copy, which is how one comes to call a second finding on an already-listed asset "new" while the
other calls it nothing.

The **policy** is not shared, on purpose:

| | baseline | `added` | `grew` | `closed` | `shrank` |
|---|---|---|---|---|---|
| `check_ratchet.py` | `.conformance/pins.txt` (101 pins) | fail | fail | **fail** | **fail** |
| `check_corpus_delta.py` | a git revision | fail | fail | pass | pass |

A closure fails the ratchet because its baseline is a file somebody has to keep in step: a fix that
does not rewrite the pin file leaves the ratchet loose by exactly that many findings, so the next
commit could reintroduce one for free. Git needs no updating, so under the delta gate the same event
is simply progress and is meant to be cheap. That is a real disagreement between two tools, and it
belongs in each of them rather than in the shared arithmetic. Each tool's source says so at the
call site, and each half is pinned by a test —
`test_closing_a_finding_passes_unlike_the_ratchet` against
`test_closing_a_finding_also_fails_until_the_pins_are_rewritten`.

### 4. `--every-rule-must-run` is fatal in CI, and it exits 2 rather than 1

With the flag, any rule in `not_evaluated` on either side raises "could not run". Exit **2**, not 1:
a rule that did not run is not "you made it worse", it is "nothing was checked". Without the flag —
the laptop case — the tool prints the note and continues. The ratchet only ever prints the note.

### 5. Three sibling checkouts, the whole engine installed, and full git history

The corpus workflow checks out three repositories side by side under the workspace, because the
tools resolve their defaults from `Path(__file__).resolve().parent.parent.parent`: `governed-bi/`
beside `BIRD-corpus/` makes `--corpus-dir ../BIRD-corpus` the shape the tool already expects. All
three are public, so the default token can read them and no secret is configured.

- `fetch-depth: 0` on the corpus checkout is load-bearing. The base revision is checked out into a
  worktree, and a shallow clone does not have it.
- The dataset checkout is `Minhao-Zhang/BIRD-Obfuscation` into a directory named
  **`BIRD-Data-Obfuscation`** — the repository name and the required directory name differ, and that
  is where conformance looks for the manifests behind V11, V12 and V15. Getting it wrong is silent,
  which is what §4's flag is for. 34 GB of databases in that repository are gitignored; the checkout
  is 98 tracked files.
- `uv sync --frozen` installs the **whole** engine rather than a minimal set (§Rejected
  alternatives 5).

### 6. `.github` and `.conformance` leave the corpus digest, so the gate does not move what it gates

`corpus_content_hash` digests every file under the corpus root — that is deliberate, and it is why
a `README.md` at the root counts as content. But a workflow file is not content and neither is a
lint's state. `corpus/identity.py::_is_tooling` now excludes both directories by *path part*,
alongside `.git`/`.hg`/`.svn`/`__pycache__`/`.ipynb_checkpoints`, so the next tool gets a
subdirectory rather than another entry in the set.

Without `.github` excluded a corpus repository could not be given CI at all: adding a workflow would
move the treatment identity, and so would editing it. Verified after the change: with the workflow
and `.conformance/pins.txt` both present in the tree, `corpus_content_hash('../BIRD-corpus')` is
`6e5c7b4be83d5682…`, the value ADR 0015 records.

The pin file lives in `.conformance/` for the same reason, and it is the reason a *directory* was
chosen over a filename.

## Rejected alternatives

**1. A committed pin file as the CI baseline.** Built first, rejected for three reasons, all
measured.

- *A stricter rule reddens a corpus that did not change.* Two rule changes landed here on
  2026-08-23. V21 went from running one of `GUARD_RULES`' members to four (five exist; `g_length` is
  declared skipped, since V13 already caps a body). V23 went from examining no column at all to all
  of them: an inline column carries no `id` in YAML and `check_unique_ids` skipped a falsy id, so the
  rule had been missing 5,947 of 13,304 assets — 45% of the tree, measured in `908857a`. Either
  change would have turned a pin-based corpus build red with **no corpus commit behind it**, and the
  corpus author cannot fix an engine rule from their own repository. Under a git baseline the same
  rule set runs on both sides, so a rule change cancels: it fires at base and at head and the
  difference is empty (`test_a_rule_getting_stricter_produces_no_delta`).
- *The pin file has to sit in the corpus tree, and the digest counts every file there.* This is not
  a hazard, it happened: an untracked `.conformance-pins.txt` at the corpus root hashed the tree to
  `8bb37531cff9155a…` where the same content without it hashed `6e5c7b4be83d5682…`. The gate had
  moved the thing it was gating — the treatment identity that every recorded number is pinned to.
  Both values are pinned by `tests/corpus/test_the_hash_ignores_a_tools_bookkeeping.py` (5 tests,
  passing); the second is re-derivable on the tree today, the first is not, because that root file
  no longer exists.
- *Closing a finding fails a pin-based build* until somebody rewrites the pin file in the same
  commit. That ceremony exists only because there is a file to keep in sync.

The pin file was not deleted — it moved to `.conformance/pins.txt`, is now tracked in the corpus
repository, and carries 101 lines. It is `check_ratchet.py`'s baseline: the right instrument for a
human declaring "this is the debt we accept", and the wrong one for CI.

**2. A pinned engine SHA in the corpus workflow.** Rejected with the pin file, and by the same
property. Under a git baseline a rule change cannot break the corpus build, because it applies to
the base revision too — so pinning a SHA buys nothing and costs a bump ritual on every engine
improvement. `uses: actions/checkout@v4` with a branch, and no version pin.

**3. Tiered jobs — a cheap subset on push, the full rule set on a schedule or a release.** Rejected
on the owner's minimality argument. One job asking one question is the whole design. The reasoning
that would justify a *particular* split (which rules are cheap, what a nightly would catch that a
push would not) was never worked out, so there is nothing further recorded here: the proposal was
withdrawn rather than answered.

**4. A mirror-image step in this repository — "does this engine commit add findings to the
corpus?"** Rejected, and by the property that motivated the design. Under a git baseline a stricter
rule fires at the base revision too, so it produces no delta and cannot redden anything. That is
what the baseline was chosen for, and it is also what makes the report pointless. The reason is
recorded in `check_corpus_delta.py`'s manual-gate declaration in
`tests/conformance/test_the_lint_gates_fire_on_a_synthetic_violation.py`, which is where a reader
looks for why a `tools/check_*.py` has no CI step.

**5. A minimal dependency install in the corpus workflow.** Rejected, measured. V16 imports
`governed_bi.serve.context`, and `serve/__init__.py` re-exports the graph, which pulls `langgraph` —
so a three-package install dies at that import. It is also an environment nobody tests, and the
next rule that reaches one module deeper would break the corpus build for a reason no test here
could see.

**6. Moving the checker into the corpus repository.** Rejected: see §Context 1 and §Consequences 4.

**7. A second notion of a finding's identity — per metric call rather than per asset.** Rejected. A
per-call identity needs a stable index into an expression, and editing an expression renumbers every
call after the edit. `(rule, where)` plus a count is stable under both rewording and renumbering.
The delta gate does not re-derive identity at all; it inherits conformance's, because a second
notion would disagree with the ratchet's.

## Consequences

1. **A commit to the corpus now has a gate, and it is honest about being a delta.** The 125 findings
   stay. Nothing in the design pretends they are gone, and the failure text tells a corpus author
   that a rule which got stricter cannot land there.
2. **The workflow pins `ref: design/return-path`, and that is a known, deliberate, temporary lie
   with no owner.** `tools/check_corpus_delta.py` is not on this repository's `main` —
   `git cat-file -e main:tools/check_corpus_delta.py` fails today — so a job pointed at the default
   branch would fail with "can't open file", reporting a missing tool as a conformance result, which
   teaches a corpus author nothing about their corpus. The line must be deleted when
   `design/return-path` merges. **No gate can see it.** The workflow lives in another repository and
   none of this repository's checks reach across; the only in-tree trace is a prose string in
   `tests/conformance/test_the_lint_gates_fire_on_a_synthetic_violation.py:92`, which is a
   declaration and not an assertion, so nothing fails if the branch merges and the line stays.
   There are exactly two places to edit, both in the corpus repository's workflow (its comment at
   line 58 and the `ref:` at line 65). **Not resolved**; the maintainer is deciding how to hold this
   separately.
3. **`.conformance/pins.txt` is tracked and nothing reads it in any CI.** `check_ratchet.py` is
   declared manual here, and the corpus workflow runs only the delta tool. So a closure in the
   corpus makes the pin file stale with nothing noticing, and the ratchet's "closing must be
   declared" policy is enforced only when a person runs it. The pin file is a human's declaration,
   and it now has no automated reader.
4. **The dependency points from the data at the engine.** The corpus repository's CI checks out
   `governed-bi` to borrow a tool. That costs three things: the corpus build breaks if this
   repository moves the tool or renames its flags; the corpus build's meaning depends on which
   engine revision it happened to fetch; and a corpus author reading a red build is reading a
   message written in another codebase. The alternative — a copy of the rules beside the data — was
   rejected because the rules *are* statements about the engine (§Context 1). Nothing mitigates the
   first cost today; the second is bounded by §Rejected alternatives 2's argument rather than by a
   pin.
5. **`HEAD^` does not always exist, and that is an exit 2 rather than a green build.** When there is
   nothing to compare against, nothing was checked, and the tool says so naming the ref
   (`test_a_bad_base_ref_exits_two_and_says_which`). The workflow's comment attributes this to "the
   first push of a branch"; with `fetch-depth: 0` the parent of any non-root commit is present, so
   the condition that actually fires is a root commit, or a base ref missing from the clone. Not
   verified on a runner — this workflow has never run.
6. **The gate does not prove the corpus is correct, and three of its rules can still go quiet.**
   V11, V12 and V15 report "not evaluated" if the dataset checkout directory is misnamed, and that
   is indistinguishable from passing. `--every-rule-must-run` turns it into exit 2, which means the
   protection is one flag in one YAML line with no test on the runner behind it. Its two directions
   are pinned locally (`test_every_rule_must_run_is_fatal_when_a_manifest_is_absent`,
   `test_without_the_flag_an_unevaluated_rule_is_not_fatal`).
7. **Identities key on a file's basename.** Two assets with the same basename in different
   directories share an identity, and moving a file between directories is invisible to both tools.
   That is a property of conformance's `_where`, inherited on purpose rather than re-derived.
8. **The comparison is duplicated between the two tools rather than imported**, and
   `check_corpus_delta.py` says so where it happens. The arithmetic is shared; the block that prints
   and decides is not. Extracting it is left to a commit that owns both files.
9. **A corpus build costs a full `uv sync --frozen` and two whole-tree conformance runs.** Measured
   locally end to end at 82 s on the real corpus when the tool landed (`3b191d5`); not re-measured
   here, because re-running it would write a worktree into the corpus repository's `.git`.

## Acceptance criteria

1. The workflow is pushed and observed: one green run on a corpus commit that adds nothing, and one
   red run — exit 1, naming the finding — on a commit that does. Neither has happened.
2. `git cat-file -e main:tools/check_corpus_delta.py` succeeds, and the `ref:` line is gone from
   `.github/workflows/conformance.yml`.
3. `corpus_content_hash('../BIRD-corpus')` still starts `6e5c7b4b` after any change to the corpus's
   CI or lint state. Held today.
4. A misnamed dataset directory produces exit 2 on a runner, not a green build. Pinned locally only.

## Open questions

1. **Who deletes the `ref:` line, and what makes them.** §Consequences 2. Nothing in either
   repository fails if it is forgotten.
2. **Whether the ratchet should have an automated reader at all**, now that the delta gate covers
   every push. If not, `.conformance/pins.txt` is documentation of accepted debt and should be
   described as that; if so, the reader has to live in the corpus repository's CI, and it will fail
   on the first genuine fix until the pin file is rewritten in the same commit.
3. **What a corpus author does with a V17a finding they did not cause.** The 107 existing ones are
   grandfathered by the design. A rule that gets stricter cannot redden their build, but it can
   redden the next commit that *touches* an already-failing asset, because the count on that
   identity may rise. Nobody has walked that case.

## What this ADR does not cover

- **What the rules say.** ADR 0005 §1.2 is the field spec; `tools/check_corpus_conformance.py` and
  `tools/conformance_rules_metric_and_content.py` are its executable form, and `WHOLE_TREE_CHECKS`
  is the dispatch that decides which rules `--file` mode cannot answer.
- **Whether the corpus is any good.** Retrieval quality, coverage and the measured arms are
  `docs/measurement.md` and `docs/open-work.md`.
- **This repository's CI.** `.github/workflows/ci.yml` and the five gates ADR 0005 §6 declares are
  unchanged by this decision.
- **How a fix reaches the corpus.** ADR 0015 owns the return path: a bundle, `git apply`, and a
  human's commit.
