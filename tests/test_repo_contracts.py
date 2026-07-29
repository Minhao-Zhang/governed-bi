"""Repo-shape rules that AGENTS.md states in prose and nothing enforced.

Two rules in this repo live only as English sentences, and both were broken by
hand in the same week they were written down. Prose is not a gate, so each one
gets a test here — and only these two, per the "no test for everything, unless
it is a problem we encountered in the past" rule in AGENTS.md.

The rules are read out of AGENTS.md rather than restated here. A test carrying
its own copy of the list becomes the thing that drifts.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from urllib.parse import unquote, urlparse

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
AGENTS_MD = REPO_ROOT / "AGENTS.md"

# The sentence in AGENTS.md that introduces the fenced list of docs allowed a twin.
# If someone rewords this, the parse below fails loudly rather than passing vacuously.
_TWIN_RULE_INTRO = "Keep a Chinese twin for these docs only:"


def _tracked(pattern: str) -> list[str]:
    """Git-tracked paths matching `pattern`, as repo-relative POSIX strings.

    Tracked, not globbed: an untracked scratch file in a working tree is nobody's
    contract violation, and CI only ever sees what is committed.
    """
    out = subprocess.run(
        ["git", "ls-files", pattern],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [line for line in out.splitlines() if line.strip()]


def _docs_allowed_a_chinese_twin() -> set[str]:
    """Parse the allow-list straight out of AGENTS.md's fenced block."""
    text = AGENTS_MD.read_text(encoding="utf-8")
    _, marker, after = text.partition(_TWIN_RULE_INTRO)
    assert marker, (
        f"AGENTS.md no longer contains {_TWIN_RULE_INTRO!r}. This test reads the "
        "doc-pairing allow-list from that sentence's fenced block; update the parse "
        "here if the rule moved, do not delete the gate."
    )
    fences = after.split("```")
    assert len(fences) >= 3, (
        "AGENTS.md's doc-pairing rule is no longer followed by a fenced code block "
        "listing the docs that keep a Chinese twin."
    )
    listed = {token for token in fences[1].split() if token.endswith(".md")}
    assert listed, "parsed the AGENTS.md twin allow-list as empty"
    # Anchor the parse to disk. If the block is ever reformatted into something this
    # splitter reads as one token or none, the caller would otherwise pass vacuously
    # on a wrong allow-list.
    missing = sorted(p for p in listed if not (REPO_ROOT / p).is_file())
    assert not missing, (
        f"AGENTS.md lists {missing} as keeping a Chinese twin, but no such English "
        "doc exists — either the doc moved/was deleted without updating AGENTS.md, "
        "or the list's formatting broke this parse."
    )
    return listed


def test_only_the_docs_agents_md_names_have_a_chinese_twin():
    """The rule AGENTS.md states and nothing enforced.

    English is the source of truth and exactly the docs in that fenced list keep a
    `.zh.md`; everything else is English-only. Twenty stray twins accumulated
    against that rule and were removed by hand — hand-removal is not a gate, and
    the cost of a stray twin is silent: it rots out of sync with an English doc
    nobody knows it shadows, and then gets read as current.

    Both directions matter. An extra twin is an unmaintained translation; a missing
    twin is a doc AGENTS.md promises in Chinese and does not deliver.
    """
    expected = {p.removesuffix(".md") + ".zh.md" for p in _docs_allowed_a_chinese_twin()}
    actual = set(_tracked("*.zh.md"))

    unexpected = sorted(actual - expected)
    assert not unexpected, (
        f"these `.zh.md` files are not twins of a doc AGENTS.md allows one for: "
        f"{unexpected}. See the 'Documentation language workflow' section of "
        "AGENTS.md — 'Everything else is English only — do not create a .zh.md for "
        "it.' Delete the file, or add its English doc to that list if it genuinely "
        "needs a twin."
    )

    absent = sorted(expected - actual)
    assert not absent, (
        f"AGENTS.md promises a Chinese twin for these docs but none is tracked: "
        f"{absent}. Write the twin, or drop the English doc from the list in "
        "AGENTS.md's 'Documentation language workflow' section."
    )


# A markdown inline link or image: the target is everything up to whitespace or the
# closing paren, so `[x](y "title")` yields `y`.
_MD_LINK = re.compile(r"\[[^\]]*\]\(\s*([^)\s]+)")


def test_every_relative_markdown_link_resolves():
    """Pins a property that holds today and is cheap to keep.

    A large docs prune just repointed links by hand across 44 tracked markdown
    files and landed at zero broken relative links. Nothing checked that, and the
    next rename is what breaks it — a dead link in `docs/README.md` is the kind of
    rot that survives every existing gate because no code imports a doc.

    Scope is deliberately narrow: relative on-disk targets only. External URLs need
    the network and would make this flaky; bare `#anchor` fragments would need a
    heading parser for a much smaller payoff.
    """
    broken: list[str] = []
    checked = 0

    for rel in _tracked("*.md"):
        doc = REPO_ROOT / rel
        for match in _MD_LINK.finditer(doc.read_text(encoding="utf-8")):
            raw = match.group(1)
            # Skip external targets and same-document anchors.
            if raw.startswith("#") or urlparse(raw).scheme:
                continue
            target = unquote(raw.split("#", 1)[0])
            if not target:
                continue
            checked += 1
            if not (doc.parent / target).exists():
                broken.append(f"{rel} -> {raw}")

    assert checked > 0, "found no relative markdown links at all — the matcher broke"
    assert not broken, "broken relative markdown links:\n  " + "\n  ".join(sorted(broken))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
