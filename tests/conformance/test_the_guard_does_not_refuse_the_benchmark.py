"""The benign half of ADR 0006 OQ3's condition, over the population every number is taken on.

OQ3 held ``govern/guard.py``'s five deterministic rules off until each had **two** numbers:
adversarial recall and benign firing rate. The first is
``tests/govern/test_the_prompt_injection_half.py``, where ten attacks are caught by the rule
each was written for. Six controls sit beside them, and six is not a rate.

This is the rate. Every question in the arm's declared test split is run through all five
rules, and the answer has to be **zero** — not "low", because a false refusal here is not a
degraded answer, it is a question the engine declines to consider at all, and a single one on
a 1,351-question benchmark would move a published figure.

**Read from the arm's pinned dataset ref, not the working tree.** ``git cat-file`` against
``arm_profile(...).dataset`` is the same read
``test_arm_profiles_are_declared.py::test_every_arm_names_a_dataset_whose_split_it_can_hash``
does, for the same reason: a checkout that drifted would quietly change the denominator, and
a rate whose population moved is not the rate anybody quoted.

**Question *and* evidence.** The engine sees both — ``eval/harness.py`` records that evidence
is one of the two conditions for EX being comparable to published BIRD, and ``accept`` carries
it onto the turn. Screening only the question would measure a narrower input than the one the
rules actually run on.

Skipped where ``../BIRD-Data-Obfuscation`` is absent, which is the same precondition the arm
tests carry and which CI's ``test`` job does not meet — so this number is established locally
and on the ``corpus`` job, and the six controls in ``tests/govern/`` are what run everywhere.
"""

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
    """The benign firing rate OQ3 asked for, and the reason turning the rules on is free.

    This is what makes the five safe to ship enabled: the arm they would have perturbed is the
    one every figure in ``docs/`` comes from, and they perturb none of it. If this ever goes
    non-zero, the honest response is to re-measure the arm, not to widen the rule — the number
    and the guard configuration are one claim.
    """
    from governed_bi.govern.guard import guard
    from governed_bi.govern.policy import GUARD_RULE_IDS, GovernancePolicy
    from governed_bi.register.arm_profiles import arm_profile

    repo = ROOT.parent / "BIRD-Data-Obfuscation"
    if not (repo / ".git").exists():
        pytest.skip(f"{repo} is not on this machine; the dataset repository is a sibling")

    profile = arm_profile("v4")
    questions = _questions(repo, profile.dataset)
    assert questions, f"{profile.dataset}'s test split parsed to no questions"

    policy = GovernancePolicy(guard_rules_enabled={rule: True for rule in GUARD_RULE_IDS})
    fired = [
        (qid, verdict["rule_id"], text[:80])
        for qid, text in questions
        if (verdict := guard(text, policy))["outcome"] != "clear"
    ]

    assert not fired, (
        f"{len(fired)} of {len(questions)} benchmark questions are refused by an input rule: "
        f"{fired[:5]}. Every published figure in this repository is taken on this population, "
        "so a rule that fires here changes a number as well as an answer"
    )


def test_the_longest_benchmark_question_is_far_below_the_length_bound() -> None:
    """``g_length`` is the one rule whose firing is a threshold rather than a pattern.

    Zero firings above says nothing about *margin*: a bound one character above the longest
    question passes that test and refuses the first slightly longer one a user types. This
    records the headroom so a future change to ``g_length_max_chars`` has to look at it.
    """
    from governed_bi.govern.policy import GovernancePolicy
    from governed_bi.register.arm_profiles import arm_profile

    repo = ROOT.parent / "BIRD-Data-Obfuscation"
    if not (repo / ".git").exists():
        pytest.skip(f"{repo} is not on this machine; the dataset repository is a sibling")

    questions = _questions(repo, arm_profile("v4").dataset)
    longest = max(len(text) for _, text in questions)
    bound = GovernancePolicy().g_length_max_chars

    assert longest * 4 < bound, (
        f"the longest benchmark input is {longest} characters against a bound of {bound}. "
        "That is less than 4x headroom; the bound is now close enough to real input that "
        "`g_length` is a limit on questions rather than on payloads"
    )
