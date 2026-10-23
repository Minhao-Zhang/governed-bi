"""W3: the answer's Details words each reason code from one table in the UI."""

from __future__ import annotations

import re
from pathlib import Path

from governed_bi.govern.layers import GUARDRAIL_ERROR, RULES

TABLE = Path(__file__).resolve().parents[2] / "ui" / "lib" / "answer-parts.ts"


def test_every_rule_has_words_on_the_page() -> None:
    text = TABLE.read_text(encoding="utf-8")
    body = text.split("export const REASON_WORDS", 1)[1].split("};", 1)[0]
    worded = set(re.findall(r"^\s+(\w+):", body, flags=re.MULTILINE))
    missing = (set(RULES) | {GUARDRAIL_ERROR}) - worded
    assert not missing, f"ui/lib/answer-parts.ts REASON_WORDS has no words for {sorted(missing)}"
