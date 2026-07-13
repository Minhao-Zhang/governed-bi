"""Simulated SME for the three-arm experiment (A3).

An eval-only :class:`~governed_bi.curator.clarifications.Responder` briefed with
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

    # ALL unique evidence hints — never capped. In BIRD the ``evidence`` field is
    # the key domain hint (e.g. "higher CSS ranking value = higher prospect"), so
    # dropping any (the old 40-question cap did) starves the SME of exactly what it
    # needs to answer. Deduped to stay compact.
    seen_ev: set[str] = set()
    evidences: list[str] = []
    for item in train_items:
        ev = (item.evidence or "").strip()
        if ev and ev not in seen_ev:
            seen_ev.add(ev)
            evidences.append(ev)
    if evidences:
        sections.append("")
        sections.append("## Domain hints (evidence attached to analyst questions)")
        sections.extend(f"- {ev}" for ev in evidences)

    sections.append("")
    sections.append("## Example analyst questions (train only; for domain context)")
    for item in train_items[:max_train_questions]:
        sections.append(f"- {item.question}")

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


def build_sme_agent(model, *, gateway, brief: str):
    """A read-only deep-agent SME.

    The SME answers from the brief (domain descriptions + all evidence) and may
    **probe the live DB read-only** (`run_probe_query`) to verify a claim before
    answering — the same way a real SME would sanity-check against the data. It
    holds no write tools: it cannot touch the corpus.
    """
    from deepagents import create_deep_agent

    from .deep_agent import _CURATOR_IDENTITY, _render_rows

    def run_probe_query(sql: str) -> str:
        """Run a read-only SELECT to check the actual data before answering.
        Returns rows (truncated) or an error string. Never mutates data."""
        try:
            result = gateway.execute(sql, _CURATOR_IDENTITY)
        except Exception as err:  # noqa: BLE001 — surface as a tool result
            return f"error: {err}"
        return _render_rows(result)

    return create_deep_agent(model=model, tools=[run_probe_query], system_prompt=brief)


def _last_message_text(result) -> str:
    msgs = result.get("messages") if isinstance(result, dict) else None
    if not msgs:
        return ""
    last = msgs[-1]
    content = getattr(last, "content", None)
    if content is None and isinstance(last, dict):
        content = last.get("content")
    if isinstance(content, list):  # some models return content as parts
        content = " ".join(
            str(p.get("text", p)) if isinstance(p, dict) else str(p) for p in content
        )
    return content or ""


class SimulatedSme:
    """SME :class:`Responder` briefed with :func:`build_sme_brief`.

    With a live LangChain model + a gateway it runs as a **read-only deep agent**
    that can probe the DB to verify its answers; otherwise (offline / no gateway)
    it falls back to a single-shot completion. Never receives write tools.
    """

    def __init__(self, chat: "ChatClient", brief: str, *, gateway=None) -> None:
        self.chat = chat
        self.brief = brief
        self._agent = None
        model = getattr(chat, "model", None)  # LangChainChatClient exposes .model
        if gateway is not None and model is not None:
            try:
                self._agent = build_sme_agent(model, gateway=gateway, brief=brief)
            except Exception:  # noqa: BLE001 — degrade to single-shot, never crash curation
                self._agent = None

    def answer(self, question: str) -> str:
        user = (
            "Answer the following curator clarification in plain prose only "
            "(no SQL). You may run read-only probe queries to check the data "
            "first if it helps.\n\n"
            f"Clarification: {question}"
        )
        if self._agent is not None:
            from ..obs import tracing_callbacks

            result = self._agent.invoke(
                {"messages": [{"role": "user", "content": user}]},
                config={"recursion_limit": 40, "callbacks": tracing_callbacks()},
            )
            raw = _last_message_text(result)
        else:
            raw = self.chat.complete(self.brief, user)
        return _sanitize_sme_answer(raw)
