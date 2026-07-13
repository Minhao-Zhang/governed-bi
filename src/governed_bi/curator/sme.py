"""Simulated SME for the three-arm experiment (A3).

An eval-only :class:`~governed_bi.curator.clarify_loop.Responder` briefed with
domain meaning from BIRD database_description CSVs + train question/evidence.
Never receives gold SQL or held-out test questions.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import TYPE_CHECKING, Sequence

if TYPE_CHECKING:
    from ..eval.dataset import EvalItem
    from ..llm import ChatClient

_SELECT_RE = re.compile(r"\bSELECT\b", re.IGNORECASE)
_SQL_FENCE_RE = re.compile(r"```(?:sql)?\s*.*?```", re.IGNORECASE | re.DOTALL)

_SME_SYSTEM_RULES = """\
Rules you MUST follow:
- Answer only from the brief below and ordinary domain sense. Do NOT invent \
columns, tables, or labels that are not in the brief.
- Never write database queries. Describe meaning in prose only.
- If a column looks like a decoy or trap, say so explicitly and recommend not \
using it.
- If you are unsure, say you are unsure rather than fabricating a definition.
"""


def build_sme_brief(
    db_description_dir: Path | str,
    train_items: Sequence["EvalItem"],
    *,
    max_train_questions: int = 40,
) -> str:
    """Build the Simulated SME system brief (no gold SQL, no test items).

    Reads every ``*.csv`` under ``db_description_dir`` (BIRD layout:
    ``original_column_name,column_name,column_description,data_format,value_description``)
    and appends a sample of train questions + evidence for domain flavour.
    """
    desc_dir = Path(db_description_dir)
    sections: list[str] = [
        "You are a subject-matter expert for this database. Answer curator "
        "clarification questions with concise, practical descriptions of what "
        "tables and columns mean and whether they are reliable for analysis.",
        "",
        _SME_SYSTEM_RULES,
        "",
        "## Database column descriptions",
    ]

    csv_paths = sorted(desc_dir.glob("*.csv")) if desc_dir.is_dir() else []
    if not csv_paths:
        sections.append("(no description CSVs found)")
    for path in csv_paths:
        sections.append(f"### {path.stem}")
        try:
            with path.open(encoding="utf-8", newline="") as fh:
                reader = csv.DictReader(fh)
                for row in reader:
                    col = (
                        row.get("column_name")
                        or row.get("original_column_name")
                        or ""
                    ).strip()
                    desc = (row.get("column_description") or "").strip()
                    values = (row.get("value_description") or "").strip()
                    fmt = (row.get("data_format") or "").strip()
                    bits = [b for b in (desc, fmt, values) if b]
                    if col and bits:
                        sections.append(f"- {col}: {' | '.join(bits)}")
                    elif col:
                        sections.append(f"- {col}")
        except OSError as err:
            sections.append(f"(failed to read {path.name}: {err})")

    sections.append("")
    sections.append("## Example analyst questions (train only; for domain context)")
    for item in train_items[:max_train_questions]:
        evidence = (item.evidence or "").strip()
        line = f"- {item.question}"
        if evidence:
            line += f" (evidence: {evidence})"
        sections.append(line)

    return "\n".join(sections)


def assert_brief_no_leakage(
    brief: str,
    *,
    gold_sqls: Sequence[str] = (),
    test_questions: Sequence[str] = (),
) -> None:
    """Raise ``AssertionError`` if the brief contains gold SQL or test questions.

    Used by unit tests and the experiment runner leakage invariants.
    """
    if _SELECT_RE.search(brief):
        raise AssertionError("SME brief must not contain SELECT (gold SQL leakage)")
    for sql in gold_sqls:
        snippet = sql.strip()
        if len(snippet) >= 12 and snippet in brief:
            raise AssertionError("SME brief contains a gold SQL substring")
    for q in test_questions:
        q = q.strip()
        if len(q) >= 12 and q in brief:
            raise AssertionError(f"SME brief contains test question text: {q[:60]!r}")


def _sanitize_sme_answer(text: str) -> str:
    """Strip SQL fences / SELECT statements so invented SQL cannot enter provenance."""
    cleaned = _SQL_FENCE_RE.sub("", text).strip()
    if _SELECT_RE.search(cleaned):
        # Keep only the prose before the first SELECT-looking line.
        lines = []
        for line in cleaned.splitlines():
            if _SELECT_RE.search(line):
                break
            lines.append(line)
        cleaned = "\n".join(lines).strip()
    return cleaned or (
        "Unsure — declining to invent a definition; treat this column cautiously."
    )


class SimulatedSme:
    """Live-LLM :class:`Responder` briefed with :func:`build_sme_brief`."""

    def __init__(self, chat: "ChatClient", brief: str) -> None:
        self.chat = chat
        self.brief = brief

    def answer(self, question: str) -> str:
        user = (
            "Answer the following curator clarification in plain prose only "
            "(no SQL).\n\n"
            f"Clarification: {question}"
        )
        raw = self.chat.complete(self.brief, user)
        return _sanitize_sme_answer(raw)
