"""Deterministic input check. No model call. See ADR 0006. One message for callers; ``g_encoding`` runs before NFKC."""

from __future__ import annotations

import re
import warnings
from typing import Callable, Literal, Mapping, TypedDict

from ..registry.settings import Unset
from .check import GovernanceUsageError
from .policy import BI_SCOPE_RULE_ID, GUARD_RULE_IDS, GovernancePolicy

__all__ = [
    "GuardResult",
    "GUARD_RULES",
    "GUARD_PUBLIC_MESSAGE",
    "guard",
    "has_control_characters",
]


class GuardResult(TypedDict):
    """Outcome of one screen. See ADR 0006."""

    outcome: Literal["clear", "blocked", "error_failed_open"]
    rule_id: str | None
    #: Ledger only. Never surfaced, so a caller cannot probe which rule fired.
    detail: str | None


#: One string, so neither a rule nor its absence is inferable from the response.
GUARD_PUBLIC_MESSAGE = (
    "This request cannot be processed. Rephrase the question in terms of the data you "
    "want to see."
)

#: Controls, bidi overrides, and zero-width characters, written as escapes. A literal
#: zero-width joiner in a character class is invisible in review, which is what this
#: rule rejects. Tab, newline, and return stay.
_CONTROL = re.compile(
    "["
    "\x00-\x08\x0b\x0c\x0e-\x1f"  # C0 controls, keeping tab, newline, return
    "\x7f-\x9f"  # DEL and the C1 block
    "\u00ad"  # soft hyphen
    "\u200b-\u200f"  # zero-width space / non-joiner / joiner, LRM, RLM
    "\u202a-\u202e"  # bidi embeddings and overrides
    "\u2060-\u2069"  # word joiner, invisible operators, bidi isolates
    "\ufeff"  # BOM / zero-width no-break space
    "]"
)

#: English only. Each pattern needs a verb plus an object: "ignore" and "system" alone
#: are ordinary words in questions about data.
_INSTRUCTION_OVERRIDE = re.compile(
    r"(?ix)"
    r"(ignore|disregard|forget|override)\s+(all\s+|any\s+|the\s+)?"
    r"(previous|prior|earlier|above|preceding)\s+(instruction|prompt|rule|direction)"
    r"|(reveal|print|repeat|show|output)\s+(me\s+)?(your|the)\s+"
    r"(system\s+)?(prompt|instructions|rules)"
    r"|you\s+are\s+now\s+(a|an|the)\b"
    r"|(new|updated)\s+(instructions|system\s+prompt)\s*:"
    r"|disregard\s+your\s+(guidelines|governance|safety)"
)

#: Markers that would forge a message boundary.
_ROLE_INJECTION = re.compile(
    r"(?im)"
    r"^\s*(system|assistant|developer|tool)\s*:"
    r"|<\|(im_start|im_end|endoftext|system|assistant|user)\|>"
    r"|\[/?INST\]"
    r"|<\|start_header_id\|>"
    r"|^\s*###\s*(system|assistant)\b"
)

#: A tool call or a pasted tool result. The model cannot tell a result it asked for
#: from one pasted into the question.
_TOOL_FORGERY = re.compile(
    # ``^`` is per line, matching ``_ROLE_INJECTION``. Without ``m`` the anchor is the
    # start of the question, so a forged result after a newline is missed.
    r"(?imx)"
    r'"(tool_calls|tool_call_id|function_call)"'
    r"|</?tool_(call|result|use)>"
    r'|"function"\s*:\s*\{\s*"name"'
    r"|^\s*(observation|tool[ _]result|function[ _]result)\s*:"
)


def has_control_characters(text: str) -> bool:
    """Exported so the statement pipeline uses this class before it normalises. A second copy would be a second answer.
    """
    return _CONTROL.search(text) is not None


def _rule_encoding(text: str, _settings: GovernancePolicy) -> str | None:
    match = _CONTROL.search(text)
    if match is None:
        return None
    return f"U+{ord(match.group()):04X} at offset {match.start()}"


def _rule_instruction_override(text: str, _settings: GovernancePolicy) -> str | None:
    match = _INSTRUCTION_OVERRIDE.search(text)
    return match.group()[:80] if match else None


def _rule_role_injection(text: str, _settings: GovernancePolicy) -> str | None:
    match = _ROLE_INJECTION.search(text)
    return match.group()[:80] if match else None


def _rule_tool_forgery(text: str, _settings: GovernancePolicy) -> str | None:
    match = _TOOL_FORGERY.search(text)
    return match.group()[:80] if match else None


def _rule_length(text: str, settings: GovernancePolicy) -> str | None:
    limit = settings.g_length_max_chars
    if len(text) <= limit:
        return None
    return f"{len(text)} characters, bound is {limit}"


#: ``g_encoding`` is first because it must precede NFKC. The rest are order-independent;
#: first to fire wins, so a question cites at most one rule.
GUARD_RULES: Mapping[str, Callable[[str, GovernancePolicy], str | None]] = {
    "g_encoding": _rule_encoding,
    "g_length": _rule_length,
    "g_instruction_override": _rule_instruction_override,
    "g_role_injection": _rule_role_injection,
    "g_tool_forgery": _rule_tool_forgery,
}

# Names stay in policy.py. A re-export would be a second home for the same set.

_WARNED: set[str] = set()


def _assert_the_predicates_match_the_vocabulary() -> None:
    """Names and predicates are apart so a policy can name a rule. A name on one side only can be enabled and never run.
    """
    if set(GUARD_RULES) != GUARD_RULE_IDS:  # pragma: no cover - import-time guard
        raise AssertionError(
            f"guard.GUARD_RULES dispatches {sorted(GUARD_RULES)} but policy.GUARD_RULE_IDS "
            f"declares {sorted(GUARD_RULE_IDS)}. A name on one side only is a rule that can "
            "be enabled and never runs, or runs and can never be enabled."
        )
    if BI_SCOPE_RULE_ID in GUARD_RULES:  # pragma: no cover - import-time guard
        raise AssertionError(
            f"{BI_SCOPE_RULE_ID!r} is in GUARD_RULES. It is the model-backed rule and runs in "
            "serve/nodes/guard.py; dispatching it here would need a model inside govern/."
        )


_assert_the_predicates_match_the_vocabulary()


def guard(question: str, settings: GovernancePolicy) -> GuardResult:
    """Screens the string it is given, not a later rewrite. ``guard_rules_enabled`` must be explicit. See ADR 0006."""
    if isinstance(settings.guard_rules_enabled, Unset):
        raise GovernanceUsageError(
            "guard_rules_enabled is UNSET: no rule has both of its numbers yet (ADR 0006 "
            "OQ3), so guard cannot decide what is on. Pass an explicit per-rule mapping; "
            "an empty mapping is a deliberate 'nothing enabled', which is a different "
            "statement from a missing one."
        )
    for rule_id, predicate in GUARD_RULES.items():
        if not settings.guard_rule_enabled(rule_id):
            continue
        try:
            detail = predicate(question, settings)
        except Exception as err:  # noqa: BLE001 - a broken rule must not end the turn
            if rule_id not in _WARNED:
                _WARNED.add(rule_id)
                warnings.warn(
                    f"guard rule {rule_id} raised {type(err).__name__}: {err}. Failing open "
                    "once per process, per ADR 0006 §1 — but this is a bug on the safety "
                    "path and the outcome is recorded so it is countable.",
                    RuntimeWarning,
                    stacklevel=2,
                )
            return GuardResult(
                outcome="error_failed_open",
                rule_id=rule_id,
                detail=f"{type(err).__name__}: {err}",
            )
        if detail is not None:
            return GuardResult(outcome="blocked", rule_id=rule_id, detail=detail)
    return GuardResult(outcome="clear", rule_id=None, detail=None)
