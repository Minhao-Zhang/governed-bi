"""Detect a raw schema identifier leaking into ``ask_user``'s human-facing text.

Hand-ported from the downstream fork ``utkuai/detentai-fork``
(``src/governed_bi/serve/schema_term_guard.py``), where two deployment targets require it by
name -- one of them, the fork quotes verbatim, "in plain business language, never schema terms"
(``detent-ai-deployment-targets.md``, a document this tree does not have; the citation is the
fork's evidence, kept as theirs). The origin transfers whether or not the document does:
``ask_user``'s **own prompt instructions were the only guard**, and its own worked example
demonstrated the opposite -- ``choices=["payments.amount", "line_items.unit_price"]``. A prompt
instruction is not a control, which is the same lesson ADR 0005 §1.5 already drew about
``governance.excluded``. This module is one.

**Scoped to ``ask_user``, and not to refusals.** That a *refusal* may name a table is a
recorded owner decision (``docs/analysis/adopting-the-downstream-fork-2026-08-19.md``, decision
three: "Yes, as the fork does -- a refusal may name tables"). ``ask_user``'s ``question`` is a
different surface: prose written for a reader to answer, not a verdict written to explain why a
statement was stopped. A reader cannot answer a question about ``line_items.unit_price``.

**Shape-based, not corpus-based, on purpose.** Comparing against a corpus's known physical
names would false-positive constantly: a column named ``status`` or ``name`` is an ordinary
English word, and a business question is allowed to ask "what does *status* mean here?" without
tripping a guard. What a business sentence essentially never contains is a **dotted path**, a
**camelCase run**, or a **snake_case token** -- those are how identifiers look, not how English
does. Shape detection also needs no corpus wiring, so it generalises to any schema with no
per-deployment configuration.
"""

from __future__ import annotations

import re

__all__ = ["find_schema_leak"]

#: ``table.column`` / ``schema.table.column`` -- the exact shape of the leaked example in the
#: fork's own ``ask_user`` docstring (``payments.amount``) that this module exists to catch.
_DOTTED_IDENTIFIER = re.compile(r"\b[a-zA-Z_][a-zA-Z0-9_]*(?:\.[a-zA-Z_][a-zA-Z0-9_]*)+\b")

#: snake_case: two or more identifier segments joined by underscores (``unit_price``,
#: ``pct_delivered``). A lone leading/trailing underscore also matches; intentional, since it is
#: rare in English prose either way.
_SNAKE_IDENTIFIER = re.compile(r"\b[a-zA-Z][a-zA-Z0-9]*_[a-zA-Z0-9_]+\b")

#: camelCase / PascalCase: a lowercase-then-uppercase boundary *inside* one token
#: (``unitPrice``, ``PurchasePrice``). At least one letter is required on each side of the
#: boundary so a single capitalised word -- a sentence's first word, or a proper noun -- does
#: not match. The residual false positive is a compound-capitalised brand name, which shape
#: detection cannot tell from a column: see the module docstring's tradeoff, and the test that
#: pins it as known rather than letting it be rediscovered.
_CAMEL_IDENTIFIER = re.compile(r"\b[a-zA-Z]*[a-z][A-Z][a-zA-Z0-9]*\b")


def find_schema_leak(*texts: str) -> str | None:
    """The first identifier-shaped token across ``texts``, or ``None`` when there is none.

    Patterns are tried in the order a reviewer would find most obviously wrong first: a dotted
    path names its table, which is the clearest leak; snake_case and camelCase are the two
    shapes a bare physical column name takes when it escapes into prose. The token itself is
    returned rather than a bool so the caller can name it back to the model -- a rejection that
    does not say which word was wrong is one the model has to guess at.
    """
    for text in texts:
        if not text:
            continue
        for pattern in (_DOTTED_IDENTIFIER, _SNAKE_IDENTIFIER, _CAMEL_IDENTIFIER):
            match = pattern.search(text)
            if match:
                return match.group(0)
    return None
