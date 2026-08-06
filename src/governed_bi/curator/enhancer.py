"""Dedup/conflict decision for a candidate draft against existing certified assets (UtkuAI
Enhancer, ported).

**Ported design, not a new one.** v1's Enhancer asked the model to compare one answered
clarification against existing notes/metrics, rendered as ``{id, summary}``, and decide
``duplicate_of``/``conflict_with`` — never inventing an id, never silently resolving a
contradiction. The comparison unit changes because the corpus does: v1 compared against
``description`` (unbounded prose); v2 has no such field, only ``summary`` (<=250 chars, I1's
one indexed field) and ``body`` (read on hit, so a model asked to dedup a *candidate* has not
seen any existing asset's body either — the comparison was always summary-shaped once you ask
what actually reaches a prompt cheaply, and v1's decision to hand the model the whole
``description`` list was itself a comparability cost this port does not need to inherit).

**Model call, not a heuristic**, for the same reason v1 chose one: "is this the same claim,
reworded" and "does this contradict that" are not string-similarity questions.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence, TypeVar

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from governed_bi.corpus.drafts import submit_draft
from governed_bi.corpus.schema import Asset

__all__ = ["EnhancerDecision", "EnhancerError", "decide", "apply"]

A = TypeVar("A", bound=Asset)

_SYSTEM_PROMPT = """You compare ONE candidate fact against a list of EXISTING facts from the \
same corpus scope and decide whether the candidate restates or contradicts one of them.

Rules:
1. duplicate_of: set ONLY if the candidate says the SAME thing as an existing fact, just \
reworded. Never invent an id -- it must be one of the ids you were given.
2. conflict_with: set ONLY if the candidate genuinely CONTRADICTS an existing fact (e.g. a \
different formula or threshold for what looks like the same concept). Never invent an id.
3. At most one of duplicate_of / conflict_with may be set. Usually both are null -- a novel \
fact that neither restates nor contradicts anything is the common case, not the exception.
4. Never silently resolve a conflict by picking a winner. Flagging it is your only job.

Respond with JSON only: {"duplicate_of": "<id or null>", "conflict_with": "<id or null>"}"""


@dataclass(frozen=True, slots=True)
class EnhancerDecision:
    duplicate_of: str | None = None
    conflict_with: str | None = None


class EnhancerError(Exception):
    """The model's response could not be turned into a decision, or the call itself failed.

    Callers are expected to catch this and treat the candidate as neither a duplicate nor a
    conflict -- the same fail-open-to-"novel" behavior v1's fold pipeline used, since refusing
    to write a candidate because the *dedup check* broke would make Enhancer's own outage a
    silent data-loss bug in mistake-memory.
    """


def _render(existing: Sequence[Any]) -> list[dict[str, str]]:
    return [{"id": a.id, "summary": a.summary} for a in existing]


def _parse_json(text: str) -> dict[str, Any] | None:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
    if fence:
        text = fence.group(1).strip()
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def decide(model: BaseChatModel, candidate_summary: str, existing: Sequence[Any]) -> EnhancerDecision:
    """Ask ``model`` to compare ``candidate_summary`` against ``existing`` (any assets with an
    ``id``/``summary``, already scoped by the caller to the same type and schema).

    Raises :class:`EnhancerError` on any call/parse/validation failure; never invents an id
    that was not in ``existing``.
    """
    if not existing:
        return EnhancerDecision()  # nothing to compare against -- always novel

    known_ids = {a.id for a in existing}
    payload = {"candidate_summary": candidate_summary, "existing": _render(existing)}
    try:
        response = model.invoke(
            [SystemMessage(content=_SYSTEM_PROMPT), HumanMessage(content=json.dumps(payload))]
        )
    except Exception as err:  # noqa: BLE001 -- normalized to EnhancerError for the caller
        raise EnhancerError(f"chat completion failed: {err}") from err

    content = response.content if isinstance(response.content, str) else str(response.content)
    parsed = _parse_json(content)
    if parsed is None:
        raise EnhancerError(f"could not parse model response as JSON: {content[:200]!r}")

    def _valid_id(key: str) -> str | None:
        value = parsed.get(key)
        if not isinstance(value, str) or not value.strip():
            return None
        value = value.strip()
        if value not in known_ids:
            raise EnhancerError(f"{key}={value!r} is not one of the ids offered: {sorted(known_ids)}")
        return value

    duplicate_of = _valid_id("duplicate_of")
    conflict_with = _valid_id("conflict_with")
    if duplicate_of and conflict_with:
        raise EnhancerError("model set both duplicate_of and conflict_with; rule 3 forbids both")
    return EnhancerDecision(duplicate_of=duplicate_of, conflict_with=conflict_with)


def apply(
    model: BaseChatModel,
    root: Path | str,
    candidate: A,
    *,
    existing: Sequence[Any],
    namespace: str | None = None,
    write_model: str | None = None,
) -> tuple[Path | None, EnhancerDecision]:
    """Decide, then act: duplicate skips the write, conflict writes flagged, novel writes plain.

    Returns ``(path, decision)`` — ``path`` is ``None`` on a duplicate, since "don't mint a
    second copy" (v1's baseline behavior; reinforcing the existing asset was its own,
    never-shipped follow-up round) means nothing was written to skip a caller having to
    distinguish "wrote nothing" from "wrote nothing because it crashed".
    """
    decision = decide(model, candidate.summary, existing)
    if decision.duplicate_of:
        return None, decision
    extra = {"conflict_with": decision.conflict_with} if decision.conflict_with else None
    path = submit_draft(root, candidate, namespace=namespace, model=write_model, extra=extra)
    return path, decision
