"""Negative tests for ``tools/check_no_benchmark_discriminators.py`` (audit §6.1).

A gate that only leaves a trace when it fires cannot afterwards be told from a gate that was
never wired up. Every assertion here makes it fire.

This file is on the gate's ``EXEMPT`` list, because it quotes the phrases it is checking for.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GATE = ROOT / "tools" / "check_no_benchmark_discriminators.py"


def _probe(tmp_path: Path, name: str, text: str) -> Path:
    """Plant one file in a corpus tree **the test owns**, and return the root to scan.

    ``--root`` rather than a fixed path under ``corpora/``: a probe in the real tree is shared
    by every process in the working tree, so two pytest runs raced on the same filename, and a
    crashed run left a planted discriminator behind that failed the gate for everything after.
    """
    corpora = tmp_path / "corpora"
    corpora.mkdir(parents=True, exist_ok=True)
    (corpora / name).write_text(text, encoding="utf-8")
    return tmp_path


def _run(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(GATE), "--root", str(root)],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )


def test_rule_a_fires_on_a_retired_prefix_phrase(tmp_path: Path) -> None:
    """The exact phrase, back in a corpus. This is what already happened once.

    ``soccer_2016`` was prefixed with ``cricket IPL batsman bowling`` because that schema's
    held-out questions are about cricket despite its name, and ``summary`` is the only text
    that enters retrieval. 27 of 57 schemas carried one.
    """
    root = _probe(
        tmp_path,
        "soccer_2016.yaml",
        "id: soccer_2016\n"
        "summary: 'soccer_2016: cricket IPL batsman bowling match analytics'\n",
    )
    result = _run(root)
    assert result.returncode == 1, result.stdout
    assert "rule A" in result.stderr
    assert "soccer_2016" in result.stderr


def test_rule_a_fires_on_a_block_scalar_summary(tmp_path: Path) -> None:
    """The form the corpus actually uses, which rule A could not see (audit D6).

    ``leads_with`` strips a ``<id>: `` lead-in and then matches the phrase at the head of the
    value. With ``summary: >-`` the joined value opens with the literal ``>-``, so nothing ever
    matched — and **32 of the 57 live ``asset_type: schema`` files in ``../BIRD-corpus`` use
    ``>-`` or ``>``**. Rule A was blind in the majority form while reporting a clean scan.

    This test is here because ``tools/mutate.py`` said so: the block-scalar fix was verified by
    hand against a temporary tree and never written down, so re-introducing the defect left this
    file green. The mutation ``d6-block-scalar-blind`` survived on the harness's first run.
    """
    root = _probe(
        tmp_path,
        "soccer_2016.yaml",
        "asset_type: schema\n"
        "summary: >-\n"
        "  soccer_2016: cricket IPL batsman bowling match analytics\n",
    )
    result = _run(root)
    assert result.returncode == 1, (
        "a prepended discriminator inside a folded block scalar was not seen; this is the form "
        f"most live schema assets use. stdout={result.stdout}"
    )
    assert "rule A" in result.stderr
    assert "soccer_2016" in result.stderr


def test_rule_b_scans_a_misspelled_asset_type(tmp_path: Path) -> None:
    """Unrecognised must mean *scanned*, which is the fail-closed direction (audit D6).

    Rule B exempts non-``schema`` assets because a *term* negates as a matter of course. The
    test was ``declared.group(1) == "schema"``, so ``asset_type: schmea`` and
    ``asset_type: schema_v2`` were both silently exempt — while the comment directly above it
    promised that "a corpus omitting or misspelling ``asset_type`` is still scanned".
    """
    root = _probe(
        tmp_path,
        "typo.yaml",
        "asset_type: schmea\nsummary: 'typo: a schema that does NOT cover cricket'\n",
    )
    result = _run(root)
    assert result.returncode == 1, f"a misspelled asset_type went exempt: {result.stdout}"
    assert "rule B" in result.stderr


def test_rule_a_fires_on_the_second_producers_lead_phrase(tmp_path: Path) -> None:
    """The table the audit never mentioned.

    ``tools/_revise_miss_summaries.py`` held 26 of these and was found by running this gate,
    not by the ten-dimension audit. Its phrases are worse than the ones §6.1 named: they carry
    negative discriminators, and its ``MISS_SCHEMAS`` set names the schemas the router got
    wrong — so the text is a transcription of which held-out questions were missed.
    """
    root = _probe(
        tmp_path,
        "hockey.yaml",
        "summary: 'hockey: NHL WHA career scoring goalies Stanley Cup HOF standings "
        "NOT draft prospects'\n",
    )
    result = _run(root)
    assert result.returncode == 1, result.stdout
    assert "rule A" in result.stderr
    assert "LEADS" in result.stderr


def test_rule_b_fires_on_a_newly_invented_shouted_negative(tmp_path: Path) -> None:
    """A phrase in none of the tables, caught by its shape.

    Rule A only catches a repeat. Rule B is what gives the gate any reach beyond the two
    deleted scripts: a summary shouting what its schema is **not** is someone steering a
    router away from a sibling they learned about from an evaluation.
    """
    root = _probe(
        tmp_path,
        "invented.yaml",
        "summary: 'widgets: gadget catalogue parts NOT sprockets warehouse'\n",
    )
    result = _run(root)
    assert result.returncode == 1, result.stdout
    assert "rule B" in result.stderr


def test_rule_b_leaves_ordinary_prose_alone(tmp_path: Path) -> None:
    """The paired negative. A gate that fires on normal wording gets switched off.

    Lower-case "does not" is how a real summary says this, and it must pass.
    """
    root = _probe(
        tmp_path,
        "ordinary.yaml",
        "summary: 'orders: one row per order line. Does not include returns or refunds.'\n",
    )
    result = _run(root)
    assert result.returncode == 0, result.stdout + result.stderr


def test_rule_b_ignores_python_comments(tmp_path: Path) -> None:
    """Rule B is scoped to corpus data, and this tree writes "NOT a bypass" in prose.

    Scanning ``.py`` for the shouted form would fire on the codebase's own commentary, and a
    gate with false positives in its own repository is a gate that gets an exemption list until
    it means nothing.
    """
    root = _probe(tmp_path, "notes.py", "# This is NOT a bypass; see ADR 0006.\n")
    result = _run(root)
    assert result.returncode == 0, result.stdout + result.stderr
