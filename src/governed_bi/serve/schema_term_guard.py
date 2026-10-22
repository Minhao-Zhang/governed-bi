"""Reject identifier-shaped tokens in ``ask_user`` prose. A prompt instruction is not a control.

Shape, not a corpus list: ordinary words stay, dotted and snake and camel tokens do not.
"""

from __future__ import annotations

import re

__all__ = ["find_schema_leak"]

#: ``table.column`` or longer. The shape ``ask_user``'s own example leaked (``payments.amount``).
_DOTTED_IDENTIFIER = re.compile(r"\b[a-zA-Z_][a-zA-Z0-9_]*(?:\.[a-zA-Z_][a-zA-Z0-9_]*)+\b")

#: Two or more underscore-joined segments. English prose rarely has that.
_SNAKE_IDENTIFIER = re.compile(r"\b[a-zA-Z][a-zA-Z0-9]*_[a-zA-Z0-9_]+\b")

#: A lower-then-upper boundary, so a single capital does not match. A brand name still can.
_CAMEL_IDENTIFIER = re.compile(r"\b[a-zA-Z]*[a-z][A-Z][a-zA-Z0-9]*\b")


def find_schema_leak(*texts: str) -> str | None:
    """The token, not a bool, so the caller can name the word back to the model."""
    for text in texts:
        if not text:
            continue
        for pattern in (_DOTTED_IDENTIFIER, _SNAKE_IDENTIFIER, _CAMEL_IDENTIFIER):
            match = pattern.search(text)
            if match:
                return match.group(0)
    return None
