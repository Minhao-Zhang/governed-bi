"""``guard``: deterministic input gate (ADR 0006 §6). No model call.

Rules carry a ``rule_id`` to the ledger only; callers see
:data:`GUARD_PUBLIC_MESSAGE`. ``g_encoding`` runs before NFKC (pipeline order):
NFKC is a rewrite, so a post-rewrite rule inspects a string the caller never sent.

``guard_rules_enabled`` is ``UNSET`` until ADR 0006 OQ3 (both calibration numbers
per rule). Out-of-scope is ADR 0005's ``negative_gate``, not a rule here.
"""

from __future__ import annotations

import re
import warnings
from typing import Callable, Literal, Mapping, TypedDict

from ..register.knobs import Unset
from .check import GovernanceUsageError
from .policy import GovernancePolicy

__all__ = [
    "GuardVerdict",
    "GUARD_RULES",
    "GUARD_PUBLIC_MESSAGE",
    "BI_SCOPE_RULE_ID",
    "guard",
    "has_control_characters",
]


class GuardVerdict(TypedDict):
    """ADR 0006 owns this type; ADR 0005 imports it."""

    outcome: Literal["clear", "blocked", "error_failed_open"]
    rule_id: str | None
    #: Ledger only. Never surfaced — see the note on rule-probing above.
    detail: str | None


#: The single string a blocked caller sees. One string, so no rule is inferable from
#: the response, and no rule's *absence* is either.
GUARD_PUBLIC_MESSAGE = (
    "This request cannot be processed. Rephrase the question in terms of the data you "
    "want to see."
)

#: Characters that must not reach the model or the normaliser.
#:
#: C0/C1 controls except tab, newline and carriage return; the bidi override and
#: isolate families; zero-width space/joiner/non-joiner; the word joiner; the
#: byte-order mark; and the soft hyphen. Every one of them survives a copy-paste and
#: none of them is part of a question about data.
#:
#: Written as escapes rather than as the characters themselves: a literal zero-width
#: joiner inside a character class is invisible in review and in a diff, which is the
#: property the rule exists to reject.
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

#: Imperatives aimed at the model rather than at the data.
#:
#: English only, and that is the recorded weakness above. Each pattern is written to
#: need a *verb plus an object*, because "ignore" and "system" on their own are
#: ordinary words in questions about data ("ignore returns", "system uptime").
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

#: Turn and role markers that would forge a message boundary.
_ROLE_INJECTION = re.compile(
    r"(?im)"
    r"^\s*(system|assistant|developer|tool)\s*:"
    r"|<\|(im_start|im_end|endoftext|system|assistant|user)\|>"
    r"|\[/?INST\]"
    r"|<\|start_header_id\|>"
    r"|^\s*###\s*(system|assistant)\b"
)

#: Text shaped like a tool call or a tool result.
#:
#: A forged tool *result* is the more dangerous half and the easier one to miss: the
#: model has no way to tell a result it asked for from one pasted into a question.
_TOOL_FORGERY = re.compile(
    r"(?ix)"
    r'"(tool_calls|tool_call_id|function_call)"'
    r"|</?tool_(call|result|use)>"
    r'|"function"\s*:\s*\{\s*"name"'
    r"|^\s*(observation|tool[ _]result|function[ _]result)\s*:"
)


def has_control_characters(text: str) -> bool:
    """Whether ``text`` holds a character :data:`_CONTROL` rejects.

    Exported because the statement pipeline needs the *same* test before it
    normalises (§3 step 1), and a second copy of the character class is a second
    answer to "which characters are invisible" — the shape B10 was.
    """
    return _CONTROL.search(text) is not None


def _rule_encoding(text: str, _knobs: GovernancePolicy) -> str | None:
    match = _CONTROL.search(text)
    if match is None:
        return None
    return f"U+{ord(match.group()):04X} at offset {match.start()}"


def _rule_instruction_override(text: str, _knobs: GovernancePolicy) -> str | None:
    match = _INSTRUCTION_OVERRIDE.search(text)
    return match.group()[:80] if match else None


def _rule_role_injection(text: str, _knobs: GovernancePolicy) -> str | None:
    match = _ROLE_INJECTION.search(text)
    return match.group()[:80] if match else None


def _rule_tool_forgery(text: str, _knobs: GovernancePolicy) -> str | None:
    match = _TOOL_FORGERY.search(text)
    return match.group()[:80] if match else None


def _rule_length(text: str, knobs: GovernancePolicy) -> str | None:
    limit = knobs.g_length_max_chars
    if len(text) <= limit:
        return None
    return f"{len(text)} characters, bound is {limit}"


#: rule id → predicate. Returns the ledger detail when the rule fires, else ``None``.
#:
#: ``g_encoding`` is first because it is the only rule whose *position* is part of its
#: specification. The rest are order-independent and the first to fire wins, so a
#: question can only ever cite one rule — which is also all the caller is told.
GUARD_RULES: Mapping[str, Callable[[str, GovernancePolicy], str | None]] = {
    "g_encoding": _rule_encoding,
    "g_length": _rule_length,
    "g_instruction_override": _rule_instruction_override,
    "g_role_injection": _rule_role_injection,
    "g_tool_forgery": _rule_tool_forgery,
}

#: The one guard rule that is **not** in :data:`GUARD_RULES`, and the reason is a layer boundary.
#:
#: It asks a model whether the question is a business-intelligence task at all, and refuses the
#: turn if not. Every rule above is a pure ``(str, GovernancePolicy) -> str | None`` predicate,
#: because ``govern/`` must stay importable with no model, no settings and no I/O — the same
#: constraint ``register/stages.py`` states. So the *check* runs in ``serve/nodes/guard.py``,
#: which already reads ``agent_model`` off the runnable config, and only the **id** lives here.
#:
#: The id lives here anyway, rather than being a bare string at the call site, because it is part
#: of a closed vocabulary three other things read: ``guard_rules_enabled`` gates it exactly like
#: the other five, ``GuardVerdict.rule_id`` publishes it, and the record retains it. A rule id
#: invented at its call site is a sixth vocabulary of the kind ``register/stages.py`` exists to
#: prevent.
#:
#: **Enabled with no model configured is ``error_failed_open``, not ``clear``.** The rule was
#: switched on and could not run; reporting that as a pass would be a gate that "leaves a trace
#: only when it fires", which ``register/record.py`` says cannot afterwards be told from a gate
#: that was never wired up.
BI_SCOPE_RULE_ID = "g_bi_scope"

_WARNED: set[str] = set()


def guard(question: str, knobs: GovernancePolicy) -> GuardVerdict:
    """Screen one input. Called on ``question``, and again on ``rewrite.after``.

    The second pass is not belt-and-braces: ADR 0005's ``rewrite`` is a model call
    with unguarded history in scope and every downstream node reads its output, so
    without it **the guarded artifact is never the delivered artifact**. These rules
    are deterministic and cheap, so running them twice costs nothing.

    ``knobs`` is required and its ``guard_rules_enabled`` mapping must be explicit —
    a default would be a decision about what is enabled, made where nobody would look
    for it.
    """
    if isinstance(knobs.guard_rules_enabled, Unset):
        raise GovernanceUsageError(
            "guard_rules_enabled is UNSET: no rule has both of its numbers yet (ADR 0006 "
            "OQ3), so guard cannot decide what is on. Pass an explicit per-rule mapping; "
            "an empty mapping is a deliberate 'nothing enabled', which is a different "
            "statement from a missing one."
        )
    for rule_id, predicate in GUARD_RULES.items():
        if not knobs.guard_rule_enabled(rule_id):
            continue
        try:
            detail = predicate(question, knobs)
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
            return GuardVerdict(
                outcome="error_failed_open",
                rule_id=rule_id,
                detail=f"{type(err).__name__}: {err}",
            )
        if detail is not None:
            return GuardVerdict(outcome="blocked", rule_id=rule_id, detail=detail)
    return GuardVerdict(outcome="clear", rule_id=None, detail=None)
