#!/usr/bin/env python
"""The corpus revision the nightly gate compares against, and what was accepted at it.

    uv run --frozen python tools/corpus_baseline.py     # prints the sha and nothing else

**Why a module and not a literal in the YAML.** ``.github/workflows/ci.yml`` needs this sha, and
so does a test that checks the sha is a commit-shaped string at all. A literal in the workflow can
only be read by the workflow, and the one thing nothing in this repository can run is the workflow
-- so the number would have exactly one reader, on a runner, and no local check of any kind. Here
it has three: the job, ``tests/conformance/test_the_corpus_gate_is_wired_to_the_nightly.py``, and
whoever bumps it. It prints on stdout with nothing around it because ``tools/`` is not a package
and a workflow step that has to ``sys.path.insert`` to read one constant is less readable than
``base="$(uv run --frozen python tools/corpus_baseline.py)"``.

**What bumping this means, and it is the only ceremony this design keeps.** ADR 0016 rejected a
committed pin file as CI's baseline for three measured reasons (§Rejected alternatives 1), but the
thing the pin file was *good* at survived: a human saying "I have looked at the new findings and I
accept them". That act is this line. Editing :data:`BASELINE_SHA` is not maintenance and it is not
a lockfile bump -- it is the accept, and the numbers beside it are what was on screen when it
happened. Nothing else in either repository asks anyone to look.

**What a stale baseline does: it goes red, and stays red.** The gate is a delta against a *fixed*
revision, not against the previous night, so findings accumulate monotonically -- a corpus commit
that adds one reds tonight's run and every run after it, until somebody either fixes the asset or
bumps this. That is the intended signal and not a defect to work around: a red build that clears
itself overnight is a red build nobody reads. It is also cheap, because the corpus barely moves:
``../BIRD-corpus`` has 9 commits on ``main`` in its whole history, spanning 2026-07-11 to 2026-08-18
(``git rev-list --count main``, read 2026-08-24) — one every four or five days at that rate, and most
of them add no finding. This repository took 485 commits over the same window, which is the ratio
the gate's trigger was chosen against.

**What this is not.** Not a claim the corpus is clean -- the 125 findings below are still there and
the gate's own failure text says it is a delta. Not a pin on the engine: ADR 0016 §Rejected
alternatives 2 records why a version pin buys nothing under a git baseline, and the same argument
applies in this direction. Both sides of the comparison are read with whatever engine revision the
job checked out, so a rule that gets stricter fires at the baseline too and cancels -- which means
this gate cannot see "the engine got stricter and the corpus is now worse". It sees corpus
movement, and that is all it claims.
"""

from __future__ import annotations

#: ``../BIRD-corpus`` at the revision whose findings were read and accepted. Full 40 hex digits and
#: not an abbreviation: ``git rev-parse --verify`` resolves a short sha only while it stays
#: unambiguous in that repository, so an abbreviation is a reference that can rot as the corpus
#: grows without anyone touching this line.
BASELINE_SHA = "74ff80c4842410e54fc81964b30bbe6d4a91f872"

#: When the findings below were read. The sha alone does not say this: a revision can be pointed at
#: years after anyone looked at it, and then "accepted" means nobody looked.
ACKNOWLEDGED = "2026-08-24"

#: How many findings the baseline tree carries, and how many identities they live on. **Different
#: nouns**, and the difference is 24: an identity is ``(rule, where)``, and an asset already
#: carrying a finding can take on more without the set growing by one entry. "The corpus carries
#: 101 findings" is wrong, and so is "125 identities".
#:
#: Measured with ``tools/check_corpus_conformance.py --corpus-dir ../BIRD-corpus --json`` at
#: :data:`BASELINE_SHA` on :data:`ACKNOWLEDGED`: V17a 107 findings on 85 metric assets, V17b 17 on
#: 15, V21 1 on 1. The other 19 rules report zero. The same 101 identities are listed by name in
#: ``.conformance/bird-corpus-pins.txt``, which is ``tools/check_ratchet.py``'s baseline -- two
#: records of one fact, so a test asserts they agree rather than trusting the reader to.
FINDINGS = 125
IDENTITIES = 101


def main() -> int:
    """Print :data:`BASELINE_SHA`, so a shell can substitute it into ``--base``.

    Only the sha, and no trailing commentary: the caller is
    ``base="$(uv run --frozen python tools/corpus_baseline.py)"``, and anything else on stdout
    becomes part of a git ref. The counts are documentation for a human and are deliberately not
    printed here -- a step that echoed them would look like it had verified them.
    """
    print(BASELINE_SHA)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
