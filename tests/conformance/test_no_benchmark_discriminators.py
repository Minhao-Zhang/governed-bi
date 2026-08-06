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

#: Probes go under a corpus root, because rule B is scoped to corpus data. One path, so a
#: crashed test leaves at most one directory behind.
PROBE_DIR = ROOT / "corpora" / "_conformance_probe"


def _run() -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(GATE)], capture_output=True, text=True, cwd=ROOT
    )


def _write(name: str, text: str) -> None:
    PROBE_DIR.mkdir(parents=True, exist_ok=True)
    (PROBE_DIR / name).write_text(text, encoding="utf-8")


def _clean() -> None:
    if PROBE_DIR.exists():
        for path in PROBE_DIR.iterdir():
            path.unlink()
        PROBE_DIR.rmdir()


def test_rule_a_fires_on_a_retired_prefix_phrase() -> None:
    """The exact phrase, back in a corpus. This is what already happened once.

    ``soccer_2016`` was prefixed with ``cricket IPL batsman bowling`` because that schema's
    held-out questions are about cricket despite its name, and ``summary`` is the only text
    that enters retrieval. 27 of 57 schemas carried one.
    """
    try:
        _write(
            "soccer_2016.yaml",
            "id: soccer_2016\n"
            "summary: 'soccer_2016: cricket IPL batsman bowling match analytics'\n",
        )
        result = _run()
        assert result.returncode == 1, result.stdout
        assert "rule A" in result.stderr
        assert "soccer_2016" in result.stderr
    finally:
        _clean()


def test_rule_a_fires_on_the_second_producers_lead_phrase() -> None:
    """The table the audit never mentioned.

    ``tools/_revise_miss_summaries.py`` held 26 of these and was found by running this gate,
    not by the ten-dimension audit. Its phrases are worse than the ones §6.1 named: they carry
    negative discriminators, and its ``MISS_SCHEMAS`` set names the schemas the router got
    wrong — so the text is a transcription of which held-out questions were missed.
    """
    try:
        _write(
            "hockey.yaml",
            "summary: 'hockey: NHL WHA career scoring goalies Stanley Cup HOF standings "
            "NOT draft prospects'\n",
        )
        result = _run()
        assert result.returncode == 1, result.stdout
        assert "rule A" in result.stderr
        assert "LEADS" in result.stderr
    finally:
        _clean()


def test_rule_b_fires_on_a_newly_invented_shouted_negative() -> None:
    """A phrase in none of the tables, caught by its shape.

    Rule A only catches a repeat. Rule B is what gives the gate any reach beyond the two
    deleted scripts: a summary shouting what its schema is **not** is someone steering a
    router away from a sibling they learned about from an evaluation.
    """
    try:
        _write(
            "invented.yaml",
            "summary: 'widgets: gadget catalogue parts NOT sprockets warehouse'\n",
        )
        result = _run()
        assert result.returncode == 1, result.stdout
        assert "rule B" in result.stderr
    finally:
        _clean()


def test_rule_b_leaves_ordinary_prose_alone() -> None:
    """The paired negative. A gate that fires on normal wording gets switched off.

    Lower-case "does not" is how a real summary says this, and it must pass.
    """
    try:
        _write(
            "ordinary.yaml",
            "summary: 'orders: one row per order line. Does not include returns or refunds.'\n",
        )
        result = _run()
        assert result.returncode == 0, result.stdout + result.stderr
    finally:
        _clean()


def test_rule_b_ignores_python_comments() -> None:
    """Rule B is scoped to corpus data, and this tree writes "NOT a bypass" in prose.

    Scanning ``.py`` for the shouted form would fire on the codebase's own commentary, and a
    gate with false positives in its own repository is a gate that gets an exemption list until
    it means nothing.
    """
    try:
        _write("notes.py", "# This is NOT a bypass; see ADR 0006.\n")
        result = _run()
        assert result.returncode == 0, result.stdout + result.stderr
    finally:
        _clean()
