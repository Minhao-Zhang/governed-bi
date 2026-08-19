"""Mine a mistake -> fix pair out of one turn's execution ledger (DetentAI Round 6/8, ported).

**What "mistake" means here.** Not every retry is a mistake worth remembering — a turn whose
first attempt already passed has nothing to teach. This mines only the pattern that generalizes:
at least one attempt failed a governance layer or the connector, and a later attempt in the
*same turn* went on to pass. The corrected SQL becomes a :class:`~governed_bi.corpus.schema.FewShotAsset`
draft (submitted via :mod:`governed_bi.corpus.drafts`, never written directly), on the theory
Round I already tested and confirmed: a corrected example, retrieved for a semantically similar
future question, transfers the fix even across unrelated business topics.

Deliberately does not classify *why* the first attempt failed beyond naming the layer — that
is exactly the open-ended, no-hypothesis-needed shape Round 6's original mechanism had, and
Round H's structured check already covers the one specific, named failure mode (percentage
scaling) this module is not trying to duplicate.
"""

from __future__ import annotations

import hashlib
from typing import Any, Mapping, Sequence

from governed_bi.corpus.schema import FewShotAsset
from governed_bi.register.knobs import knob_default
from governed_bi.serve.ledger import attempt_field

__all__ = ["mine_mistake_from_execution"]


def _mistake_id(schema: str, question: str) -> str:
    digest = hashlib.sha256(question.encode("utf-8")).hexdigest()[:16]
    return f"mistake.{schema}.{digest}"


def _truncated_summary(question: str) -> str:
    cap = int(knob_default("summary_max_chars"))
    if len(question) <= cap:
        return question
    return question[: cap - 1].rstrip() + "…"


def mine_mistake_from_execution(
    question: str, schema: str, execution: Mapping[str, Any]
) -> FewShotAsset | None:
    """``None`` when there is nothing to learn: no SQL executed, the first attempt already
    passed, or the eventual pass carries no executed SQL to show.
    """
    attempts: Sequence[Any] = execution.get("attempts") or ()
    passed_index = next(
        (i for i, a in enumerate(attempts) if attempt_field(a, "passed") is True), None
    )
    if passed_index is None or passed_index == 0:
        return None  # no pass at all, or the first try already worked -- nothing to learn

    corrected_sql = attempt_field(attempts[passed_index], "executed_sql")
    if not corrected_sql:
        return None

    failed = attempts[passed_index - 1]
    failed_layer = attempt_field(failed, "verdict_layer") or attempt_field(failed, "reason_code")

    summary = _truncated_summary(question)
    body = (
        f"{question}\n\n"
        f"An earlier attempt on this question failed ({failed_layer}). "
        f"Corrected SQL:\n{corrected_sql}"
    )
    return FewShotAsset(
        id=_mistake_id(schema, question),
        schema=schema,
        sql=corrected_sql,
        summary=summary,
        body=body,
    )
