# Which questions the published arms ran, and why the file could not say

**Status:** closed for the three shipped arms, and §5.2's pre-flight gap closed the same day.
Three blind spots remain named in §5 and are not scheduled.
**Measured 2026-08-20** on the maintainer's machine. Every number below names where it came from.

## 1. The defect

`src/governed_bi/register/arms.toml` pinned the corpus **twice** — `corpus = "30872d3"`, a git ref
a human checks out in `../BIRD-corpus`, and `corpus_content_hash`, the digest every measurement
row carries — and recorded **nothing** identifying the question set. `arm_profiles.py::reconcile`
therefore had nothing to compare.

The consequence is not abstract. `BIRD-Data-Obfuscation`'s test split has been replaced three
times (§3) and one of those versions holds exactly 1,351 questions, the same *n* every published
arm reports. So a rerun against a replaced dataset produced the same `n`, a substantially
different question population, and **passed every quotability gate** — the gates compare the
corpus digest and the comparability knobs, and both matched.

That is the failure `arms.toml`'s own header exists to prevent, one field over. Its words: *"An
arm that cannot be reconciled must say so, not report agreement."* It enforced that for the
corpus completely and for the question set not at all.

There is direct precedent in the same file. `v3_fold` — the baseline `v4` is measured against —
once declared no `corpus_content_hash`, so `reconcile` skipped its only comparison and the driver
printed *"every row agrees with the profile in arms.toml"* from a check that examined nothing.

## 2. How it surfaced

A downstream fork (`utkuai/detentai-fork`, `docs/questions-for-minhao-2026-08-14.md` §0) needed to
know which questions the three arms ran and could not read it anywhere. It recovered the answer by
filtering four historical versions of `eval_dataset/test_final.jsonl` against the 57 schemas
`BIRD-corpus@30872d3` covers, then cross-checking the survivor against prose in two of our own
documents. Its conclusion: `BIRD-Obfuscation@22fe2a6`, "Dedupe before splitting", 2026-07-29.

Its own summary of the cost is the point: *"Recovering `22fe2a6` took a schema-filtered count
across four dataset versions and a cross-check against prose in two documents; it should have been
one field."*

## 3. The fork's claim, verified rather than attributed

The fork wrote that the artifacts which would settle it — `runs/eval/*.jsonl`, gitignored — were
not on its machine. **They are on the maintainer's.** So the question set did not have to be
inferred from counts.

Question-id counts of every version of `eval_dataset/test_final.jsonl` in
`../BIRD-Data-Obfuscation` (`git log --all -- eval_dataset/test_final.jsonl` returns exactly four):

| dataset commit | date | questions |
|---|---|---:|
| `d178efd` initial commit | 07-08 | 2,030 |
| `efc655e` purge 2,739 bad-gold | 07-29 | 1,441 |
| `1711248` resplit after purge | 07-29 | 1,389 |
| **`22fe2a6` dedupe before splitting** | **07-29** | **1,351** |

At `22fe2a6` those 1,351 questions carry 57 distinct `db_id` values, and that set is **equal** to
the 57 schema directories `BIRD-corpus@30872d3` holds — no `db_id` outside the corpus, no corpus
schema without a question.

Then, directly against the artifacts:

| artifact | rows | question ids vs `22fe2a6` |
|---|---:|---|
| `runs/eval/proxy_v3_fold_opus_high_corpus30872d3.jsonl` | 1,351 | set-equal, 0 extra, 0 missing |
| `runs/eval/proxy_v4_corpus30872d3.jsonl` | 1,351 | set-equal, 0 extra, 0 missing |
| `runs/eval/proxy_v5_corpus30872d3.jsonl` | 1,351 | set-equal, 0 extra, 0 missing |

`short_digest` of that id set is `423a3f4b65fb` (full SHA-256
`423a3f4b65fb389f001edae9998feb775f8b76e566ebf3dbb27429731a999113`).

**An independent producer agrees.** `runs/eval/live_full_gpt-5.6-luna_xhigh_topdefault_lexical.jsonl`
is a later run, made after `eval/provenance.py::scope_identity` gained a writer (2026-08-12), and
its rows record `knobs_resolved["question_subset"] = "1351:423a3f4b65fb"` — the harness resolving
the same value for itself, from the dataset file rather than from the artifact.

So the fork's reconstruction is **confirmed**, by a different method, and the value declared in
`arms.toml` is measured rather than reconstructed. Two of its supporting facts also hold here:
`22fe2a6:eval_dataset` and today's `dacf037:eval_dataset` are the same tree
(`bc0246cbea33d736a2470ac655b99eb53d5b4192`), so the dataset in the working copy today *is* the
dataset the arms ran.

## 4. What was built

* `ArmProfile.dataset` — the dataset repository's git ref, for a human. Nothing reconciles against
  it, exactly like `corpus`.
* `ArmProfile.question_subset` — **mandatory**; `_parse_profiles` refuses a profile without one,
  mirroring `corpus_content_hash`. Its value is verbatim what the `question_subset` `Role.scope`
  knob records, `"<count>:<12 hex>"`. The profile field carries the knob's *name* as well as its
  format, so the "two identifiers in two namespaces compared with `startswith`" defect cannot
  recur here.
* `reconcile` compares it against what a **row** records, reading `knobs_resolved` — which is the
  opposite of the corpus rule and deliberately so. `corpus_content_hash` is a `RecordField` and
  sits at the top of the row; `question_subset` is a knob and sits in the knob mapping. The
  2026-08-11 defect was reading a field *where it never is*, not reading the knob mapping as such.
* `eval/provenance.py::derived_question_set` — the fork's method, in code. The three published
  arms predate `scope_identity`'s writer, so no row of them records the knob; but every row
  carries its own `question_id`, so the set is *in* the artifact. `reconciliation_lines` derives
  it and compares, and labels the weaker claim as weaker: *"no row records question_subset (the
  writer postdates this artifact); the 1351 question ids it holds hash to the declared
  1351:423a3f4b65fb"*.
* No new `RecordField` and no new knob. `question_subset` already existed, already had a producer
  in the driver, and adding a second home for the same fact is the defect `AGENTS.md` names.

## 5. What this does not detect

Written down because a gate whose blind spot is unrecorded gets read as covering it.

1. **A gold or prose edit in place.** The digest is over question **ids**. A dataset that rewrote a
   question's text, its evidence, or its gold SQL while keeping the ids moves nothing here.
   `1711248` → `22fe2a6` moved ids and would have been caught; a gold correction would not.
   `gold_fingerprint` (`eval/datalake.py::attach_gold_fingerprints`) is the field for that, and
   this one does not subsume it.
2. ~~**The pre-flight is still corpus-only.**~~ **Closed the same day.** It was: the driver called
   `arm_startup_refusal` above `dataset_file`, so it passed the corpus alone and a `--arm v4` run
   on the wrong dataset was caught at **report** time, over the finished artifact — after the money
   is spent. The call now sits directly below `covered_qids`, the first line at which the question
   set is knowable, and passes both keys. That is still above `append_refusal` and `--truncate`, so
   nothing has been paid for or destroyed when it fires. `scope_identity` is called there and again
   inside `harness_knobs`: the same producer twice on the same three arguments, which is the only
   way to guarantee the pre-flight compares the value the rows will actually carry.
   `tests/eval/test_the_arm_profile_wire_is_exercised.py::test_the_driver_supplies_both_locks_to_the_pre_flight`
   reads the call site as an AST — a regex would break on reformatting and pass on a renamed key.
3. **No cross-arm gate.** `eval/report.py::knobs_comparable` compares `comparability_keys()`, and
   `question_subset` is `Role.scope`. Nothing stops two arms measured on two question sets from
   both being quotable. What the field buys is that each arm's rows are checked against a claim a
   reader can diff, so such a pair now has to say so in `arms.toml`.
4. **48 bits.** Twelve hex characters, sized for drift between two attempts at one run, not
   against an adversary looking for a collision.
5. **A partial artifact, on the derived path only.** 900 rows of a 1,351-question arm derive a
   different value — correctly, but uselessly. So the derivation is used only when no row records
   the knob; a recorded knob always wins.
