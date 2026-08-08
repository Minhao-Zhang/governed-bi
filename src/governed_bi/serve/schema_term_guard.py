"""Detect a raw schema identifier leaking into human-facing clarification text.

Power Kiosk's and Kindling's action plans both require this by name (Kindling's,
verbatim: "in plain business language, never schema terms" — utku-ai-deployment-
targets.md). ``ask_user``'s own instructions used to be the only guard against
this, and its own worked example demonstrated the opposite (``choices=
["payments.amount", "line_items.unit_price"]``) — a prompt instruction is not a
control (the same lesson ADR 0005 §1.5 already drew about ``governance.excluded``);
this module is one.

**Shape-based, not corpus-based, on purpose.** Comparing against a corpus's known
physical names would false-positive constantly: a column named ``status`` or
``name`` is an ordinary English word, and a business question is allowed to say
"what does *status* mean here?" without tripping a guard. What a business
sentence essentially never contains is a **dotted path**, a **camelCase run**, or
a **snake_case token** — those are how identifiers look, not how English does.
Shape detection also needs no corpus wiring and generalizes to any schema without
per-deployment configuration.
"""

from __future__ import annotations

import re

__all__ = ["find_schema_leak"]

#: `table.column` / `schema.table.column` — the exact shape of the leaked example
#: this module exists to catch (`payments.amount`).
_DOTTED = re.compile(r"\b[a-zA-Z_][a-zA-Z0-9_]*(?:\.[a-zA-Z_][a-zA-Z0-9_]*)+\b")

#: snake_case: two or more identifier segments joined by underscores
#: (`unit_price`, `pct_delivered`). A single leading/trailing underscore alone
#: (rare in English prose either way) still matches; that is intentional.
_SNAKE_CASE = re.compile(r"\b[a-zA-Z][a-zA-Z0-9]*_[a-zA-Z0-9_]+\b")

#: camelCase / PascalCase: a lowercase-then-uppercase boundary inside one token
#: (`PurchasePrice`, `unitPrice`). Requires at least one letter on each side of
#: the case boundary so a single capitalized word (a sentence's first word, or
#: a proper noun) does not match.
_CAMEL_CASE = re.compile(r"\b[a-zA-Z]*[a-z][A-Z][a-zA-Z0-9]*\b")


def find_schema_leak(*texts: str) -> str | None:
    """The first identifier-shaped token across ``texts``, or ``None`` if none.

    Checks in the order a reviewer would find most obviously wrong first: a
    dotted path names its table, which is the clearest schema leak; snake_case
    and camelCase are the two shapes a bare physical column name most commonly
    takes when it escapes into prose.
    """
    for text in texts:
        if not text:
            continue
        for pattern in (_DOTTED, _SNAKE_CASE, _CAMEL_CASE):
            match = pattern.search(text)
            if match:
                return match.group(0)
    return None
