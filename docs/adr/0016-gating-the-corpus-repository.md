# 0016: Gating the served corpus

- **Status:** **Reversed** (P5 of the pivot, [ADR 0017](0017-a-served-chat-app-over-a-layer-the-user-brings.md)).
  Nothing below is built. The nightly corpus job, the conformance rules, and the baseline
  they compared against are deleted.
- **Deciders:** project owner (2026-08-24; reversed 2026-10).
- **Scope:** whether a served corpus is checked for new findings, and which repository runs that check.

## What was decided

On 2026-08-24 the repository took on a gate for the corpus it serves. The rules were
statements about what this engine does with an asset, so the check lived here rather than
in the corpus repository. A nightly job compared the current tree with a recorded corpus
revision and failed when a finding was new. A separate pin file failed when a finding
closed, until a person rewrote the pins. Three rules needed the obfuscation dataset's
manifests, including the one that forbids quoting a held-out question.

## What is true instead

There is no conformance package and no nightly corpus job. CI runs lint, types, tests,
the adversarial suite, and the data-free leak check. Quoting a held-out question is still
refused, by the train-only check, which also needs a corpus and the question file and so
stays off CI. The other conformance rules are gone with the package.

## What was learned

- **A gate that has never run is a document.** The nightly could not fire from a feature
  branch, and the first baseline was the corpus tip, so a green run would have proved
  nothing. The tool's exit codes were driven by hand; the job itself was not.
- **The direction of the dependency was the part that held.** The rules described this
  engine, so a copy beside the data would have been a second answer. Deleting the rules
  does not put them in the corpus repository.
- **Most of the rules were not the leak.** The held-out question is the property the pivot
  keeps, and it does not need the other rules that shipped beside it.
