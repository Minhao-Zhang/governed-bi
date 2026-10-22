"""Benign firing rate over the pinned benchmark, question and evidence together. See ADR 0006. One refusal changes a
published figure."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent


def _questions(repo: Path, dataset: str) -> list[tuple[str, str]]:
    """``(question_id, question + evidence)`` for the split ``dataset`` names."""
    blob = subprocess.run(
        ["git", "-C", str(repo), "cat-file", "-p", f"{dataset}:eval_dataset/test_final.jsonl"],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )
    assert blob.returncode == 0, f"{repo} cannot resolve {dataset}: {blob.stderr}"
    out: list[tuple[str, str]] = []
    for line in blob.stdout.splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        question = str(row.get("question") or "")
        evidence = str(row.get("evidence") or "")
        if question:
            out.append((str(row["question_id"]), f"{question}\n{evidence}".strip()))
    return out


def test_no_rule_fires_on_any_question_in_the_benchmark() -> None:
    """Zero firings on the population published figures use. A firing is a reason to re-measure, not to widen the rule.
    """
    from governed_bi.govern.guard import guard
    from governed_bi.govern.policy import GUARD_RULE_IDS, GovernancePolicy
    from governed_bi.registry.config_profiles import config_profile

    repo = ROOT.parent / "BIRD-Data-Obfuscation"
    if not (repo / ".git").exists():
        pytest.skip(f"{repo} is not on this machine; the dataset repository is a sibling")

    profile = config_profile("v4")
    questions = _questions(repo, profile.dataset)
    assert questions, f"{profile.dataset}'s test split parsed to no questions"

    policy = GovernancePolicy(guard_rules_enabled={rule: True for rule in GUARD_RULE_IDS})
    fired = [
        (qid, result["rule_id"], text[:80])
        for qid, text in questions
        if (result := guard(text, policy))["outcome"] != "clear"
    ]

    assert not fired, (
        f"{len(fired)} of {len(questions)} benchmark questions are refused by an input rule: "
        f"{fired[:5]}. Every published figure in this repository is taken on this population, "
        "so a rule that fires here changes a number as well as an answer"
    )


def test_the_longest_benchmark_question_is_far_below_the_length_bound() -> None:
    """Zero firings say nothing about margin. The longest question must sit well under the bound."""
    from governed_bi.govern.policy import GovernancePolicy
    from governed_bi.registry.config_profiles import config_profile

    repo = ROOT.parent / "BIRD-Data-Obfuscation"
    if not (repo / ".git").exists():
        pytest.skip(f"{repo} is not on this machine; the dataset repository is a sibling")

    questions = _questions(repo, config_profile("v4").dataset)
    longest = max(len(text) for _, text in questions)
    bound = GovernancePolicy().g_length_max_chars

    assert longest * 4 < bound, (
        f"the longest benchmark input is {longest} characters against a bound of {bound}. "
        "That is less than 4x headroom; the bound is now close enough to real input that "
        "`g_length` is a limit on questions rather than on payloads"
    )
