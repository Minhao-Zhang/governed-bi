"""Negative tests for ``tools/check_no_benchmark_discriminators.py`` (audit §6.1).

A check that only leaves a trace when it fires cannot afterwards be told from a check that was
never wired up. Every assertion here makes it fire.

This file is on the check's ``EXEMPT`` list, because it quotes the phrases it is checking for.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CHECK = ROOT / "tools" / "check_no_benchmark_discriminators.py"


def _probe(tmp_path: Path, name: str, text: str) -> Path:
    """The planted file lives in a tree this test owns, so two runs cannot race on one filename."""
    corpora = tmp_path / "corpora"
    corpora.mkdir(parents=True, exist_ok=True)
    (corpora / name).write_text(text, encoding="utf-8")
    return tmp_path


def _run(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CHECK), "--root", str(root)],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )


def test_the_repository_tree_passes() -> None:
    """The check exits 0 on this tree. A skip would look the same as a pass."""
    result = subprocess.run(
        [sys.executable, str(CHECK)],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_rule_a_fires_on_a_retired_prefix_phrase(tmp_path: Path) -> None:
    """``summary`` is the only text that enters retrieval, so a planted phrase there steers the router."""
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
    """A folded block scalar opens with the fold marker, so a head-of-value match never saw the phrase."""
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
    """A misspelled asset type is still scanned. Exempting only an exact ``schema`` hid typos."""
    root = _probe(
        tmp_path,
        "typo.yaml",
        "asset_type: schmea\nsummary: 'typo: a schema that does NOT cover cricket'\n",
    )
    result = _run(root)
    assert result.returncode == 1, f"a misspelled asset_type went exempt: {result.stdout}"
    assert "rule B" in result.stderr


def test_rule_a_fires_on_the_second_producers_lead_phrase(tmp_path: Path) -> None:
    """A helper that lists missed schemas is itself a transcription of held-out questions."""
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
    """A summary that says what a schema is not is steering the router from an evaluation."""
    root = _probe(
        tmp_path,
        "invented.yaml",
        "summary: 'widgets: gadget catalogue parts NOT sprockets warehouse'\n",
    )
    result = _run(root)
    assert result.returncode == 1, result.stdout
    assert "rule B" in result.stderr


def test_rule_b_leaves_ordinary_prose_alone(tmp_path: Path) -> None:
    """The paired negative. A check that fires on normal wording gets switched off.

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
    """Scanning Python for the shouted form would fire on this repository's own commentary."""
    root = _probe(tmp_path, "notes.py", "# This is NOT a bypass; see ADR 0006.\n")
    result = _run(root)
    assert result.returncode == 0, result.stdout + result.stderr
