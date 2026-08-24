#!/usr/bin/env python
"""One answer to "what did this change do to the finding set", for the two tools that ask.

``check_ratchet.py`` compares a corpus against a pin file committed beside it. ``check_corpus_delta.py``
compares it against a git revision. **They differ only in where the baseline comes from** -- and
before this module they each carried their own copy of the arithmetic underneath, which is how one
comes to call a second finding on an already-listed asset "new" while the other calls it nothing.

What is shared and what is not:

* **Shared: the arithmetic.** How to run the conformance tool, how a finding becomes an identity,
  and what ``added``/``grew``/``closed``/``shrank`` mean against a baseline.
* **Not shared: the policy.** The ratchet fails on all four, because a closure that does not update
  the pin file leaves the ratchet loose by exactly that many findings. The delta gate fails on two,
  because a closure is the outcome it wants to be cheap and git needs no updating. That is a real
  disagreement between two tools, and it belongs in each of them rather than here.

A finding's **identity** is ``(rule, where)`` -- a rule id and the ``file:asset`` it is about, never
the message, because a reworded message is not a new finding and a finding that moved to another
asset is not the same one. Each identity carries a **count**, because ``(rule, where)`` alone cannot
see growth: an asset already carrying a finding could take on any number more without the set
growing by one entry. Measured on ``../BIRD-corpus``: 125 findings on 101 identities, so 24 are
invisible in exactly that direction.
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections import Counter, defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: A finding's identity: the rule id and the ``file:asset`` it is about.
Identity = tuple[str, str]


class CannotRun(RuntimeError):
    """The conformance tool could not be run or did not answer.

    Distinct from "it answered and there are findings", which is the normal case both callers
    exist to interpret. A caller maps this to its own "could not run" exit code, which must not be
    the same as "you made it worse".
    """


@dataclass(frozen=True, slots=True)
class Report:
    """One conformance run: the identities, how many findings each covers, and what did not run."""

    counts: Counter[Identity]
    messages: Mapping[Identity, list[str]]
    not_evaluated: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class Delta:
    """What changed between a baseline and a head, by identity."""

    added: list[Identity] = field(default_factory=list)
    grew: list[Identity] = field(default_factory=list)
    closed: list[Identity] = field(default_factory=list)
    shrank: list[Identity] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.added or self.grew or self.closed or self.shrank)


def conformance_argv(corpus: Path, dataset_dir: Path | None = None) -> list[str]:
    """The command a run is made with, built in one place so two runs cannot differ.

    ``dataset_dir`` overrides where V11, V12 and V15 look for their manifests. Conformance resolves
    them relative to *this* repository's parent, so a CI job whose dataset checkout lands elsewhere
    cannot run those three rules at all -- which are the rules a "did every rule run" check is
    about.
    """
    argv = [
        sys.executable,
        str(ROOT / "tools" / "check_corpus_conformance.py"),
        "--corpus-dir",
        str(corpus),
        "--json",
    ]
    if dataset_dir is not None:
        argv += [
            "--trap-manifest", str(dataset_dir / "trap_manifest.json"),
            "--table-manifest", str(dataset_dir / "trap_table_manifest.json"),
            "--rename-map", str(dataset_dir / "schema_rename_map.json"),
            "--test-split", str(dataset_dir / "test_final.jsonl"),
        ]
    return argv


def read(corpus: Path, dataset_dir: Path | None = None) -> Report:
    """Run conformance over ``corpus`` and read its findings. Raises :class:`CannotRun`.

    A subprocess rather than an import: the conformance tool is a script with a ``main`` that parses
    ``sys.argv`` and owns its own exit codes, and running it the way CI runs it is the only way a
    gate checks what CI checks.
    """
    done = subprocess.run(
        conformance_argv(corpus, dataset_dir),
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=str(ROOT),
    )
    if done.returncode != 0:
        raise CannotRun(
            f"conformance --json on {corpus} exited {done.returncode}:\n{done.stderr[:2000]}"
        )
    try:
        payload = json.loads(done.stdout)
    except json.JSONDecodeError as err:
        raise CannotRun(
            f"conformance --json on {corpus} did not emit JSON: {err}\n{done.stdout[:500]}"
        ) from err

    counts: Counter[Identity] = Counter()
    messages: dict[Identity, list[str]] = defaultdict(list)
    for finding in payload["findings"]:
        key = (str(finding["rule"]), str(finding["where"]))
        counts[key] += 1
        messages[key].append(str(finding.get("message", "")))
    return Report(counts, messages, dict(payload.get("not_evaluated") or {}))


def compare(base: Mapping[Identity, int | None], head: Mapping[Identity, int]) -> Delta:
    """What ``head`` did to ``base``, by identity and by count.

    ``base`` may carry ``None`` for a count, which means **not recorded** -- the form a pin file
    written before pins carried counts still has. Those identities are compared on presence alone
    and are silently excluded from ``grew`` and ``shrank``, because a gate must not report a rise
    against a number it does not have. The caller is expected to say how many are in that state:
    checking less than you claim is the defect these tools exist to prevent.
    """
    added = sorted(set(head) - set(base))
    closed = sorted(set(base) - set(head))
    both = [key for key in set(base) & set(head) if base[key] is not None]
    grew = sorted(key for key in both if head[key] > (base[key] or 0))
    shrank = sorted(key for key in both if head[key] < (base[key] or 0))
    return Delta(added=added, grew=grew, closed=closed, shrank=shrank)
