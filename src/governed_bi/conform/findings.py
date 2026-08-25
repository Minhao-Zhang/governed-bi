"""What a rule returns, and the identity a gate keys it on.

Its own module because both directions need it: every rule module here produces
:class:`Finding`, and the CLI that reports them consumes it. Beside either one the import would
be circular, and a copy on each side would be two ``Finding`` types that compare unequal.

An identity is ``file:asset`` and never ``file:kind`` -- :func:`where_of` for an asset,
:func:`where_of_file` for the two rules that are about a file. ``tools/check_ratchet.py`` pins
``(rule, where)``, so an identity two assets share is a pin that cannot move.
"""


from __future__ import annotations

from pathlib import Path
from typing import Any


class Finding(str):
    """One violation line. A ``str`` so the report can just sort them."""


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def where_of(kind: str, asset: dict[str, Any], path: Path) -> str:
    """``file:asset`` for a finding, and it must name an *asset* and never a kind.

    Inline columns carry no ``id`` in YAML -- the loader derives it -- so the label falls back to
    ``physical_name`` and then to ``name``. Both fallbacks are there because the alternative was
    measured: on a two-column table with neither field set, five findings collapsed to three
    identities, both columns reporting as ``t.yaml:column``.

    That costs more than a vague report. ``check_ratchet.py`` pins ``(rule, where)``, so two columns
    sharing one identity means fixing one while breaking the other moves no line in the pin file and
    the ratchet reports a hold on a tree that changed. A column has a stable name; there is no
    reason to key on its kind.
    """
    label = asset.get("id") or _column_label(kind, asset) or kind
    return f"{path.name}:{label}"


def _column_label(kind: str, asset: dict[str, Any]) -> str:
    if kind != "column":
        return ""
    return _text(asset.get("physical_name")) or _text(asset.get("name"))


def where_of_file(path: Path) -> str:
    """The identity of a **file-level** finding, in the same two-part shape as an asset's.

    V14 (the loader rejected it) and V16 (the rendered closure is over cap) are properties of a
    file and not of one asset in it, so they have no asset half. They used to emit ``file.yaml: ...``
    with a single colon, which ``_where_of`` could not key and the reporter silently dropped -- both
    report zero on the corpus we measure, so nobody noticed that the ratchet could not see them. The
    literal ``<file>`` cannot collide with an asset id, which may not contain angle brackets.
    """
    return f"{path.name}:<file>"
