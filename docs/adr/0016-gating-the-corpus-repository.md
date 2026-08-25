# 0016: Gating the served corpus — "did the corpus add a finding since somebody last looked?"

- **Status:** Accepted (2026-08-24). **No gate has ever run, in either shape.** Built here on
  `design/return-path`: `tools/check_corpus_delta.py` (git baseline), `tools/check_ratchet.py`
  (pin-file baseline, manual), `tools/conformance_findings.py` (the shared arithmetic), and 23 tests
  over the two policies (`tests/conformance/test_a_commit_does_not_add_a_finding.py`, 13;
  `tests/conformance/test_the_ratchet_only_turns_one_way.py`, 10 — counted 2026-08-24). Landing with
  this record: a nightly job in this repository's `.github/workflows/ci.yml`,
  `tools/corpus_baseline.py` holding the corpus revision it compares against, and
  `.conformance/bird-corpus-pins.txt` (109 lines, **101 pins**) as the ratchet's baseline on this
  side of the merge. **Not observed:** the nightly cannot fire from a feature branch
  (§Consequences 3), so every claim below about a **runner** is read from YAML. The **tool's** three
  exit codes are not argued: all three were driven locally on 2026-08-24 and are recorded in
  §Consequences 2.
- **Deciders:** project owner + design session (2026-08-24). This record was written once and
  rewritten the same day, and the churn is worth stating rather than smoothing over. The first
  design was larger — a committed pin file as CI's baseline, a pinned engine SHA, and tiered jobs —
  and the owner's minimality argument deleted all three. The second design, recorded and then
  discarded within the hour, put a workflow **in the corpus repository** that checked out this one.
  The owner's challenge was one question: *why does it need a dependency on this repo?* That flipped
  the direction, which is §Decision 1 and the load-bearing reasoning in this ADR. The question the
  gate asks has not changed since the first draft.
- **Scope:** *what the served corpus is gated on, which repository runs the check, and when.*
  **Not** what the rules are (that is ADR 0005's field spec, executed by
  `tools/check_corpus_conformance.py`), and not whether the corpus is any good.
- **Related:** [0015](0015-the-return-path.md) — its *What this ADR does not cover* named "who owns
  the corpus repository's CI" as unanswered and said the served corpus "has no CI at all". Both
  sentences are now stale in a way this ADR causes, so 0015 carries an `Amended 2026-08-24` note
  pointing here. [0005](0005-v2-memory-layer-and-faceted-retrieval.md) §1.2 is the field spec every
  rule is a predicate over. `corpus/hash.py::corpus_content_hash` and
  `corpus/identity.py::_is_tooling` own the treatment identity this gate had to avoid moving.

---

## Context

### 1. The corpus is a repository the engine does not own, and the checker is in the engine

`../BIRD-corpus` is a separate git repository — public, 7,361 files tracked on `main` at `74ff80c4`,
measured 2026-08-24 with `git ls-files | wc -l`. It holds the treatment that every measured number
is pinned to. The tool that judges it — `tools/check_corpus_conformance.py`, 22 rules — is in
**this** repository, because the rules are statements about what the engine will do with an asset:
V16 measures a table plus its folded column roster with `serve/context.py`'s own `_structural_line`
and `_roster_entry`, V17a parses a metric expression at the dialect `govern/` parses generated SQL
at, V21 runs `govern/guard.py`'s own `GUARD_RULES` over model-visible text. A second copy of any of
those beside the data would be a second answer free to disagree with the first.

So the gate and the tree it gates are in different repositories, and one of them has to reach
across. Which one is §Decision 1.

> **Amended 2026-08-25.** The reasoning above holds and got stronger, but two of its coordinates
> moved. The 22 rules are no longer in `tools/`: they are `src/governed_bi/conform/`, a package
> between `serve` and `eval` in `tools/check_imports.py::LAYERS`, behind
> `conform.problems_with_corpus(root)` / `problems_with_asset_file(path)`.
> `tools/check_corpus_conformance.py` survives as a thin adapter and keeps its exact argv contract
> and its three exit codes, which is why the `corpus` job in `.github/workflows/ci.yml` needed no
> edit. The layer had to be at or above `serve`, because this section's own argument names the
> reason: V16 reaches into `serve/context.py` and V21 into `govern/guard.py`, so `corpus/` at layer
> 6 was never legal. That reach is also now a real interface —
> `serve/context.py::rendered_closure_chars` replaced V16's use of the private `_structural_line`
> and `_roster_entry`. What this buys beyond tidiness is the thing this section argues for: "the
> rules are statements about what the engine will do with an asset" is now a claim the engine can
> answer in-process, instead of one only a subprocess could.

### 2. A zero-findings gate would have been rejected on its first run

Measured on `../BIRD-corpus` at `main` = `74ff80c4842410e54fc81964b30bbe6d4a91f872`, 2026-08-24,
with `tools/check_corpus_conformance.py --corpus-dir ../BIRD-corpus --json`:

| | findings | identities |
|---|---|---|
| V17a — a metric expression parses as SQL at the engine's dialect | 107 | 85 |
| V17b — every identifier in the expression resolves on `base_table` or a declared join | 17 | 15 |
| V21 — model-visible text passes `guard`'s `GUARD_RULES` | 1 | 1 |
| **total** | **125** | **101** |

The other 19 of 22 rules report zero. V17a's 107 findings fall across **85 of the corpus's 478
metric assets** — the corpus holds 13,304 assets in seven types (ADR 0005 §2.2), and `metric` is the
only type a V17a finding can be about.

A gate demanding zero rejects that corpus on its first run. What happens next is not a fix, it is a
waiver, and a waiver is how a real finding goes green afterwards. The answerable question is
narrower: whatever was already wrong may stay wrong, and nothing new may arrive.

### 3. Findings and identities are different nouns, and the difference is 24

A finding's **identity** is `(rule, where)`, where `where` is the `file:asset` the finding is about.
125 findings live on 101 identities, so 24 findings are invisible to an identity-only comparison: an
asset already carrying a V17a finding could take on any number more without the set growing by one
entry. "The corpus carries 101 findings" is wrong, and so is "125 identities". Every baseline in
this design therefore carries a **count** per identity. Verified as a behaviour rather than argued:
with a second `DIVIDE` nested into a metric that already had one, the delta tool exits 1 reporting
`1 -> 2` with 101 identities on both sides
(`test_a_second_finding_on_the_same_identity_fails_with_the_count`).

### 4. Three rules answer "not evaluated", and that reads exactly like "passed"

V11, V12 and V15 need the obfuscation dataset's manifests (`trap_manifest.json`,
`trap_table_manifest.json`, `schema_rename_map.json`, `test_final.jsonl`). Without them
`check_corpus_conformance.py` puts them in a `not_evaluated` map and reports zero findings for
them — which is the same output a clean corpus produces. V12 is the held-out-split leakage rule. On
a laptop a missing manifest is normal. In CI it is the failure the gate exists to prevent.

### 5. The corpus moves rarely, and it now references this repository nowhere

`main` carries **9 commits, dated 2026-07-11 to 2026-08-18**, read 2026-08-24 with
`git log --pretty='%h %ad' --date=short main`. One landed in the fifteen days before this was
written. A gate on every push to that repository would sit idle for weeks at a time.

The corpus's `main` tree contains **zero paths** under `.github/` or `.conformance/`, measured with
`git ls-tree -r main --name-only`. The two commits that carried the abandoned corpus-side workflow
(`bd61b9fb`, `2000cc3a`) were never pushed and are no longer reachable from `main`, whose tip is the
same `74ff80c4` that `origin/main` points at. The GitHub Actions run count for
`Minhao-Zhang/BIRD-corpus` is **0**.

## Decision

### 1. "Conformant" is a property of the data *relative to this engine*, so the consumer runs the check

This is the whole argument, and every other clause follows from it.

The rules are not facts about YAML. They encode what **this** engine requires of a corpus. V16
imports `governed_bi.serve.context`. V11, V12 and V15 need the obfuscation dataset's manifests. The
job has to `uv sync` the whole engine, because `serve/__init__.py` re-exports the graph and pulls
`langgraph` — a three-package install dies at V16's import, measured (§Rejected alternatives 5).

Put those rules in the corpus repository and the data would be asserting facts about an engine it
cannot see. So the checker lives with the consumer — **and therefore the consumer runs it.** The
discarded shape had the data repository executing the engine repository's default branch, unpinned
by intent. That is legal on GitHub, and it is the wrong way round.

Nothing here was blocked. Cross-repository checkout with the default `${{ github.token }}` needs no
PAT while all three repositories are public and Actions is enabled with `allowed_actions: "all"`,
which is their state today; a private repository would need one. "Checkout Multiple Repositories Side
by Side" is a documented pattern in `actions/checkout`'s own README. The old design was pointed the
wrong way, not prevented.

### 2. The gate asks whether the corpus added a finding since somebody last looked, and nothing else

`tools/check_corpus_delta.py` runs conformance twice — once on the corpus checkout, once on a base
revision inside it — and compares the two `(rule, where) -> count` maps. Exit 0 if no finding was
added; exit 1 if an identity is present at head and absent at base, or a shared identity's count
grew; exit 2 if it could not run at all.

It is a **delta** gate and its own failure message says so. It does not claim the corpus is clean.

### 3. The baseline is a corpus SHA recorded in *this* repository, and bumping it is the acknowledgement

`tools/corpus_baseline.py::BASELINE_SHA` is `74ff80c4842410e54fc81964b30bbe6d4a91f872`, with the
date it was read and the 125/101 counts read at it beside it. The job passes it to `--base`.

Editing that line is not maintenance. It is a human saying "I looked at the new findings and I
accept them" — the one thing the rejected pin file was good at, kept without the file. Because the
baseline is fixed rather than "last night", findings accumulate: a corpus commit that adds one reds
tonight's run and every run after it until somebody fixes the asset or bumps the SHA. That is the
intended signal. A red build that clears itself overnight is a red build nobody reads.

The base tree comes from `git worktree add --detach` into a temp directory, removed in a `finally`
on every exit path including the failing ones. Not a checkout in place, which needs a clean tree and
moves the operator's HEAD; not a directory inside the corpus, which conformance would then walk. A
leaked worktree registers a directory in the corpus repository's `.git/worktrees` on every red
build, and that repository is not this tool's to litter. `_resolve_ref` uses
`rev-parse --verify ^{commit}` rather than a bare `--verify`, so a tag or a tree fails naming what
the operator typed instead of failing two steps later inside `worktree add`.

### 4. Nightly and `workflow_dispatch`, and deliberately not `push` or `pull_request`

Two reasons, and they point the same way. The corpus moves rarely (§Context 5), so a per-push gate
would almost never have anything to say. And a finding introduced by a *corpus* commit must not
redden an unrelated pull request **here** — a red X on someone's engine PR, caused by a tree they
did not touch and cannot fix in that PR, is how a gate gets ignored or removed. `workflow_dispatch`
is what a person uses after bumping the baseline, instead of waiting for 07:00 UTC.

### 5. Two tools, one arithmetic, two policies — and the asymmetry is deliberate

`tools/conformance_findings.py` owns how conformance is run, how a finding becomes an identity with
a count, and what `added`/`grew`/`closed`/`shrank` mean. Before it existed each tool carried its own
copy, which is how one comes to call a second finding on an already-listed asset "new" while the
other calls it nothing.

The **policy** is not shared, on purpose:

| | baseline | `added` | `grew` | `closed` | `shrank` |
|---|---|---|---|---|---|
| `check_ratchet.py` | `.conformance/bird-corpus-pins.txt` (101 pins) | fail | fail | **fail** | **fail** |
| `check_corpus_delta.py` | a corpus revision | fail | fail | pass | pass |

A closure fails the ratchet because its baseline is a file somebody has to keep in step: a fix that
does not rewrite the pin file leaves the ratchet loose by exactly that many findings, so the next
commit could reintroduce one for free. Git needs no updating, so under the delta gate the same event
is progress and is meant to be cheap. That is a real disagreement between two tools, and it belongs
in each of them rather than in the shared arithmetic. Each half is pinned by a test —
`test_closing_a_finding_passes_unlike_the_ratchet` against
`test_closing_a_finding_also_fails_until_the_pins_are_rewritten`.

### 6. `--every-rule-must-run` is fatal in CI, and it exits 2 rather than 1

With the flag, any rule in `not_evaluated` on either side raises "could not run". Exit **2**, not 1:
a rule that did not run is not "you made it worse", it is "nothing was checked". Without the flag —
the laptop case — the tool prints the note and continues. The ratchet only ever prints the note.

### 7. Three checkouts, nested rather than sibling, with every path spelled out

`actions/checkout`'s `path:` must stay inside `$GITHUB_WORKSPACE`, and this repository is checked
out at the workspace root, so the two data repositories land **inside** it rather than beside it.
The local layout is siblings; the runner's is not, and the difference is deliberate. Both tools
resolve their default corpus and dataset from `Path(__file__).resolve().parent.parent.parent`, which
on the runner is a directory containing neither — so a step that forgot `--corpus-dir` exits 2
saying so. Real siblings would let the defaults resolve, which means a wrong or missing flag would
read as a working gate. Both paths in the job are therefore explicit.

- The engine checkout comes **first**, because `actions/checkout` cleans its destination with
  `git clean -ffdx` and at the workspace root that would delete the two untracked data directories
  if it ran after them.
- `fetch-depth: 0` on the corpus checkout is load-bearing, and only there. The baseline revision is
  checked out into a worktree, and a shallow clone does not have that commit.
- The dataset checkout is `Minhao-Zhang/BIRD-Obfuscation` into a directory named
  **`BIRD-Data-Obfuscation`** — repository name and required directory name differ, and the directory
  name is what the tools' defaults, the docs and every local checkout use. The local sibling has the
  same mismatch (remote `BIRD-Obfuscation`, directory `BIRD-Data-Obfuscation`). Getting it wrong is
  silent, which is what §6's flag is for. Its 34 GB of databases are gitignored; the checkout is 98
  tracked files, counted 2026-08-24.
- `uv sync --frozen --extra bedrock` — the same line as the `test` job, deliberately, though no
  conformance rule needs the extra. It installs the **whole** engine rather than a minimal set
  (§Rejected alternatives 5), and matching the other job avoids a second environment nobody tests.

### 8. `.github` and `.conformance` stay out of the corpus digest as defence, not as machinery

`corpus_content_hash` digests every file under the corpus root — deliberately, which is why a
`README.md` at the root counts as content. `corpus/identity.py::_NON_CORPUS_DIRS` excludes
`.github` and `.conformance` by path part, alongside `.git`/`.hg`/`.svn`/`__pycache__`/
`.ipynb_checkpoints`.

Under this decision neither exclusion is load-bearing, because neither directory exists in the
corpus any more (§Context 5). They stay as defence against the next tool that wants a corner of that
tree. Measured 2026-08-24 with both directories absent: `corpus_content_hash('../BIRD-corpus')` is
`6e5c7b4be83d56828bab66183eec03bbdcf486d7454d34acd066530010ebed85`, the same value it returned when
both were present, and the value ADR 0015 records. So the exclusion is now confirmed by measurement
rather than argued.

## The three reasons the pin file was rejected, re-scored

The direction flip is not neutral with respect to why a committed pin file lost. Each reason has
moved:

1. *A stricter rule reddens a corpus that did not change.* **Still dissolved, and now doubly.** The
   same rule set runs on both sides of the delta, so a rule change fires at the baseline too and
   cancels (`test_a_rule_getting_stricter_produces_no_delta`). And because the gate and the rules
   are now in one repository, a rule change plus its baseline bump can land in a single commit here.
   Under the old shape those two edits were in two repositories and could not be made atomic at all.
2. *The pin file entered `corpus_content_hash`, so the gate changed the thing it was gating.*
   **Gone by construction rather than by an exclusion list.** The measurement that killed the pin
   file — an untracked pin file at the corpus root moving the digest from
   `6e5c7b4be83d5682…` to the now-superseded `8bb37531cff9155a…` — no longer has anything to bite:
   the gate's state lives in this repository, not that tree. `_NON_CORPUS_DIRS` is defence
   (§Decision 8).
3. *Closing a finding fails the build until the pin file is rewritten.* **Survives, smaller.** There
   is still one file a human keeps in step, but it is one SHA in `tools/corpus_baseline.py` rather
   than 109 lines of findings, and the bump is the acknowledgement rather than a bookkeeping chore.
   The 109-line pin file still exists — for `check_ratchet.py`, which nothing runs automatically
   (§Consequences 5).

## Rejected alternatives

**1. A workflow in the corpus repository that checks out this one.** Built, committed in both
repositories, recorded as this ADR's Decision, and discarded the same day. It ran `check_corpus_delta.py`
on every `push` and `pull_request` to the corpus, with three sibling checkouts, and pointed
`actions/checkout` at `Minhao-Zhang/governed-bi` with `ref: design/return-path` because the tool is
not on `main` here (`git cat-file -e main:tools/check_corpus_delta.py` fails today). Rejected on
§Decision 1: the data repository would be asserting a fact about an engine it cannot see, and
executing that engine's default branch unpinned to do it. Two smaller costs went with it. The
`ref:` line was a known temporary lie that nothing in either repository could fail on, and the
consequence table had to record that no gate could see it. And the corpus build's meaning depended
on which engine revision it happened to fetch. Both are now moot rather than mitigated. The workflow
text — 101 lines, its reasoning in the header — exists in no repository; what it argued is preserved
in §Context 2-4 and in this list, which is the only record of it.

**2. A committed pin file as CI's baseline.** Built first, rejected for three measured reasons, all
of which survive the flip in the form above.

- *A stricter rule reddens a corpus that did not change.* Two rule changes landed here on
  2026-08-23. V21 went from running one of `GUARD_RULES`' members to four (five exist; `g_length`
  is declared skipped, since V13 already caps a body). V23 went from examining no column at all to
  all of them: an inline column carries no `id` in YAML and `check_unique_ids` skipped a falsy id,
  so the rule had been missing 5,947 of 13,304 assets — 45% of the tree, measured in `908857a`.
- *The pin file has to sit in the corpus tree, and the digest counts every file there.* Not a
  hazard, it happened: the two hashes in §Re-scored 2. Both values are pinned by
  `tests/corpus/test_the_hash_ignores_a_tools_bookkeeping.py`; the first is re-derivable on the tree
  today, the second is superseded and is not, because that root file no longer exists.
- *Closing a finding fails a pin-based build* until somebody rewrites the pin file in the same
  commit.

The pin file was not deleted. It moved to `.conformance/bird-corpus-pins.txt` **in this
repository** — 109 lines, 101 pins — as `check_ratchet.py`'s baseline: the right instrument for a
human declaring "this is the debt we accept", and the wrong one for CI.

**3. A pinned engine SHA.** Rejected with the pin file and by the same property. Under a git
baseline a rule change applies to the base revision too, so pinning buys nothing and costs a bump
ritual on every engine improvement. Now partly moot: the engine revision is whatever the job's own
checkout is.

**4. Tiered jobs — a cheap subset on one trigger, the full rule set on another.** Rejected on the
owner's minimality argument. One job asking one question is the whole design. The reasoning that
would justify a *particular* split (which rules are cheap, what one trigger catches that another
does not) was never worked out, so nothing further is recorded: the proposal was withdrawn rather
than answered.

**5. A minimal dependency install.** Rejected, measured. V16 imports `governed_bi.serve.context`,
and `serve/__init__.py` re-exports the graph, which pulls `langgraph` — so a three-package install
dies at that import. It is also an environment nobody tests, and the next rule that reaches one
module deeper would break the job for a reason no test here could see.

**6. Running the gate on `push` and `pull_request` here.** Rejected: §Decision 4. A corpus finding
must not redden an unrelated engine PR.

**7. A mirror-image question — "does this engine commit add findings to the corpus?"** Rejected by
the property that motivated the git baseline. A stricter rule fires at the base revision too, so it
produces no delta and cannot redden anything. That is what the baseline was chosen for, and it is
also what makes the report pointless. The consequence is that this gate cannot see "the engine got
stricter and the corpus is now worse"; it sees corpus movement, and that is all it claims.

**8. A second notion of a finding's identity — per metric call rather than per asset.** Rejected. A
per-call identity needs a stable index into an expression, and editing an expression renumbers every
call after the edit. `(rule, where)` plus a count is stable under both rewording and renumbering.
The delta gate does not re-derive identity at all; it inherits conformance's, because a second
notion would disagree with the ratchet's.

## Consequences

1. **It is no longer a push-time gate, and that is the price of the direction flip.** A corpus
   commit that adds a finding **lands**. Nothing stops it at the corpus's merge, and nothing there
   tells the author. It is caught on the next nightly, up to a day later, or sooner if somebody
   dispatches the job. The design accepts this because the corpus is human-owned and merged by the
   same person who would read the red build (§Context 5: 9 commits in the corpus's whole history),
   and because the alternative was the dependency direction §Decision 1 rejects.
2. **No runner has ever run this gate, in either shape — but the tool has, and all three exit
   codes are observed.** 0 Actions runs on the corpus repository; the two commits carrying the old
   workflow were unpushed and are abandoned, and the new job cannot fire yet (§3). Driven by hand
   on 2026-08-24 against `../BIRD-corpus` at `74ff80c4` with `--base 30872d3b`:

   | exit | how it was produced | what it reported |
   |---|---|---|
   | 0 | the tree as committed | base 125 findings on 101 identities, head 125 on 101, in 78 s |
   | 1 | one table asset copied to a second filename, keeping its `id` | head 183 on 159; the 58
     added V23 findings printed one per line, each naming the asset that already declares that id |
   | 2 | `--dataset-dir` at a path that does not exist, with `--every-rule-must-run` | V11, V12 and
     V15 each named as `not evaluated` |

   The corpus was clean after each, with no worktree left registered — the leak the tool's
   docstring says it removes on every exit path, including the failing ones. One thing this
   surfaced: with `--every-rule-must-run` and **no** `--dataset-dir`, the run exits 0, because the
   default resolves to a local sibling directory. On a runner that resolution does not hold, which
   is why §Decision 7 passes both paths explicitly rather than trusting a default it cannot see.
   What is still unobserved is everything above the tool: the checkout layout, the token, and the
   `clean: true` ordering.
3. **The nightly cannot fire from `design/return-path`.** GitHub runs a `schedule` only from the
   default branch, and `workflow_dispatch` is only offered for a workflow present on it — the
   existing `mutate` job's comment in `.github/workflows/ci.yml` already says the first half. So
   this gate starts working when the branch merges, and not before. That replaces the old shape's
   `ref: design/return-path` — a line no check could see — with a plain "not reachable yet", which
   is at least visible in the trigger.
4. **A stale baseline goes red and stays red.** Intended (§Decision 3). The failure mode it trades
   for is a person who bumps the SHA to clear a build without reading the findings, and nothing
   detects that: the bump *is* the acknowledgement, so a bump made without looking is
   indistinguishable from one made after looking.
5. **`check_ratchet.py` still has no automated reader, and a nightly delta gate is not the
   ratchet.** The earlier version of this record surfaced the sharper version of this: the pins were
   tracked *in the corpus* and no CI anywhere read them, so the ratchet's policy — "closing a
   finding must be declared" — was unenforced. The move fixes the side of the merge: the pins are
   now in this repository, where this repository's CI could read them and where a rule change and a
   pin edit can land together. It does **not** fix the enforcement, and the nightly deliberately does
   not run the ratchet: its policy fails a *closure* too, so a second step would red the job the
   first time anybody fixes a metric. `check_ratchet.py` stays a manual instrument, declared as one
   in `tests/conformance/test_the_lint_gates_fire_on_a_synthetic_violation.py`, and the pin file
   keeps no automated reader. A closure makes it stale with nothing noticing.
6. **The two records of 125/101 can drift.** `tools/corpus_baseline.py` carries the counts and
   `.conformance/bird-corpus-pins.txt` carries the 101 identities by name. That is two records of
   one fact, kept honest by a test asserting they agree rather than by a reader.
7. **The baseline equals the corpus tip today, so the first green run proves little.** At
   `BASELINE_SHA` = `main` the delta is empty by construction. The gate becomes informative only
   after the corpus moves.
8. **A bad base ref is exit 2, not a green build.** When there is nothing to compare against,
   nothing was checked, and the tool says so naming the ref
   (`test_a_bad_base_ref_exits_two_and_says_which`). The discarded workflow attributed this to "the
   first push of a branch", and that is wrong; the tool's docstring did not state the condition at
   all. With `fetch-depth: 0` the parent of any non-root commit is present, so the conditions that
   actually fire are a **root commit** (which has no parent) or a **base ref absent from the
   clone** — a SHA on an unfetched fork, or a baseline whose commit was rewritten away. The docstring
   now says that. Not verified on a runner.
9. **Three rules can still go quiet.** V11, V12 and V15 report "not evaluated" if the dataset
   checkout directory is misnamed, indistinguishable from passing. `--every-rule-must-run` turns it
   into exit 2, which means the protection is one flag in one YAML line with no runner test behind
   it. Its two directions are pinned locally
   (`test_every_rule_must_run_is_fatal_when_a_manifest_is_absent`,
   `test_without_the_flag_an_unevaluated_rule_is_not_fatal`).
10. **Identities key on a file's basename.** Two assets with the same basename in different
    directories share an identity, and moving a file between directories is invisible to both tools.
    A property of conformance's `_where`, inherited on purpose rather than re-derived.
11. **The comparison is duplicated between the two tools rather than imported**, and
    `check_corpus_delta.py` says so where it happens. The arithmetic is shared; the block that
    prints and decides is not. Extracting it is left to a commit that owns both files.
12. **A run costs a full `uv sync --frozen` and two whole-tree conformance runs.** The two
    conformance runs measured **78 s** end to end on the real corpus, 2026-08-24 (82 s when the
    tool landed in `3b191d5` — the same order, a different laptop minute). The `uv sync` is on
    top of that and is not measured here.

## Acceptance criteria

1. The job is observed: one green run, and one red run — exit 1, naming the finding — on a corpus
   revision that adds one. Neither has happened.
2. `tools/check_corpus_delta.py` and `tools/corpus_baseline.py` are on `main`, so the schedule can
   reach them. Not yet: `git cat-file -e main:tools/check_corpus_delta.py` fails today.
3. `corpus_content_hash('../BIRD-corpus')` still starts `6e5c7b4b`. Held 2026-08-24, with both
   tooling directories absent from the tree.
4. A misnamed dataset directory produces exit 2 on a runner, not a green build. Pinned locally only.
5. `tools/corpus_baseline.py`'s counts and `.conformance/bird-corpus-pins.txt` agree, asserted by a
   test rather than read.

## Open questions

1. **Whether the ratchet should ever get an automated reader.** Answered "not in the nightly" and no
   further (§Consequences 5): running it there would red the job on the first genuine fix. So
   `.conformance/bird-corpus-pins.txt` is documentation of accepted debt with a manual checker, and
   it should be described as that until somebody argues otherwise. What has changed is only that the
   argument is now *possible* in one commit, since the pins and the rules are in one repository.
2. **What makes anyone bump a stale baseline.** The signal is a red nightly on the default branch.
   Whether that is read is not established, and nothing escalates it.
3. **What a corpus author does with a V17a finding they did not cause.** The 107 existing ones are
   grandfathered by the baseline. A rule that gets stricter cannot redden the build, but a corpus
   commit that *touches* an already-failing asset can, because the count on that identity may rise.
   Nobody has walked that case.

## What this ADR does not cover

- **What the rules say.** ADR 0005 §1.2 is the field spec; the `conform/` package is its executable
  form, and `WHOLE_TREE_CHECKS` is the dispatch that decides which rules `--file` mode cannot
  answer. (Written against `tools/check_corpus_conformance.py` and
  `tools/conformance_rules_metric_and_content.py`, which held the rules at the time. Amended
  2026-08-25: see §Context 1.)
- **Whether the corpus is any good.** Retrieval quality, coverage and the measured arms are
  `docs/measurement.md` and `docs/open-work.md`.
- **The rest of this repository's CI.** The other jobs in `.github/workflows/ci.yml`, and the five
  gates ADR 0005 §6 declares, are unchanged by this decision.
- **How a fix reaches the corpus.** ADR 0015 owns the return path: a bundle, `git apply`, and a
  human's commit. This gate reads the result; it does not participate in producing it.
</content>
</invoke>
