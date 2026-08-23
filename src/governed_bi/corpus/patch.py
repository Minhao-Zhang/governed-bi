"""Replace one field of one asset, in place, without rewriting the file (ADR 0015 §4).

**Why this exists rather than ``store.write``.** ``store.write`` is a *create* primitive and
measuring it said so. Loading a table asset, changing its ``summary`` and calling ``write``
produced a **second file carrying the same asset id** — the served corpus keeps a table's columns
inline, one file per table, and ``write`` puts an asset at ``<root>/<namespace>/<id>.yaml``, which
is not where the table it came from lives. ``store.load`` returned both files with **zero
problems**, and ``retrieve/index.py`` then raised ``ValueError: duplicate index id``: a total serve
outage arriving *after* the commit, past a conformance checker that cannot see it.

And even where the path is right, ``write`` is a whole-file reformat: ``yaml.safe_dump`` with no
``width``, over ``to_mapping``, which omits defaults. So it reflows every string past the dumper's
wrap column, drops any explicitly-written default, reorders keys into dataclass field order, and
cannot preserve a comment at all — it dumps a plain mapping, and a plain mapping has no comments in
it to dump.

**Measured on the same one-word edit**, against a 311-line table asset with 34 inline columns
(``tbl_world_development_indicators_guo_jia.yaml``, BIRD corpus at ``6e5c7b4b``):

===================  =============  ===========================================
                     changed lines  lands at
===================  =============  ===========================================
``corpus/patch.py``              4  the file it edited
``store.write``                343  ``<namespace>/<id>.yaml`` — a *different* file
===================  =============  ===========================================

343 of 311 lines is the whole file plus the churn of reordering it. And the second column is the
worse half: the new file does not replace the old one, so the corpus then declares the same asset id
twice.

**The 4 is not 1, and the difference is worth knowing.** A *plain* scalar is one line, so a one-word
edit to one is a one-line change. A *folded block* rewraps, so a word inserted near the start pushes
every following line — 4 changed lines for a 2-line block. That is minimal rather than one: the
block reflows and nothing else in the file does.

**So: locate the field's byte span with PyYAML's composer and splice.** Not a regex — an inline
column's ``summary`` is nested two levels inside its table's document and only the composer knows
where. Not a re-dump — that is the thing being avoided.

**Three refusals, and each is a decision.**

* ``governance`` cannot be edited. ADR 0005: exclusion is "human-only, enforced by the absence of a
  tool", and this is the tool whose absence is the control.
* A field outside :data:`~governed_bi.feedback.validate.EDITABLE_FIELD_PATHS` cannot be edited,
  because ``lifecycle.derived_state`` confirms a landing by comparing ``summary``/``body`` text. A
  patch that lands and then reads as ``superseded`` forever is worse than one refused.
* A structural change to a table's ``columns`` — adding, removing or reordering — is not offered at
  all. Column ids are *derived* from position and name, so a structural edit silently re-keys
  downstream assets. That is a hand edit with a person reading the whole file.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from governed_bi.corpus.identity import derive_column_id

__all__ = [
    "Span",
    "FieldNotLocatable",
    "StaleValue",
    "EDITABLE",
    "locate",
    "read_field",
    "apply_edit",
]

#: Field paths this module will replace. Deliberately the same two
#: ``feedback/validate.py::EDITABLE_FIELD_PATHS`` allows, and a test asserts they agree: a path
#: editable here and unconfirmable there produces a patch that lands and reads as superseded.
EDITABLE: frozenset[str] = frozenset({"summary", "body"})

#: Keys that may never be reached, whatever a caller asks for.
_FORBIDDEN_ROOTS: frozenset[str] = frozenset({"governance", "provenance", "audit", "columns"})


class FieldNotLocatable(LookupError):
    """The field is absent, or is a node this module will not rewrite in place.

    An alias or a merge key is the interesting case: the same scalar is referenced from more than
    one place, so replacing it at one site changes the others. Refusing is the only correct answer
    and it is not a limitation to be lifted — a shared scalar is *supposed* to be shared.
    """


class StaleValue(ValueError):
    """The field does not hold what the caller said it held.

    **The concurrency check, and the reason a patch carries ``was`` at all.** Between drafting a
    patch and applying it the corpus can move — another bundle lands, or somebody edits by hand —
    and a patch that overwrote whatever it found would silently discard that. Failing loudly here
    means the failure surfaces at ``git apply`` rather than as a change nobody made.
    """


@dataclass(frozen=True, slots=True)
class Span:
    """One field's value, as a byte range in the file plus how it was written.

    ``style`` is PyYAML's: ``None`` plain, ``'>'`` folded, ``'|'`` literal, ``"'"`` or ``'"'``
    quoted. It is carried because the replacement must be written the *same* way — changing a
    folded block to a quoted string is a whole-paragraph diff for a one-word change, which is the
    defect this module exists to avoid.
    """

    start: int
    end: int
    style: str | None
    #: Column the value's content is indented to, for a block scalar. Zero for a plain one.
    indent: int
    #: Longest content line in the original block, so a rewrap matches the file rather than
    #: imposing a width. ``0`` when there is nothing to measure.
    width: int
    #: The value as YAML resolves it, which is what ``was`` is compared against.
    value: str


def locate(path: Path | str, *, asset_id: str, field_path: str) -> Span:
    """Where ``asset_id``'s ``field_path`` lives in ``path``.

    ``asset_id`` may name the file's top-level asset **or** one of a table's inline columns, whose
    id is derived by ``corpus/identity.py::derive_column_id``. Deriving it here rather than reading
    an ``id`` key is not a detail: an inline column carries no ``id`` in the YAML at all, so there
    is nothing to read.
    """
    field_path = str(field_path)
    if field_path not in EDITABLE:
        raise FieldNotLocatable(
            f"{field_path!r} is not an editable field path; editable: {sorted(EDITABLE)}. These "
            "are the paths lifecycle.derived_state can confirm landed by comparing text, and a "
            "patch that lands and then reads as superseded forever is worse than one refused."
        )
    if field_path.split(".")[0] in _FORBIDDEN_ROOTS:
        raise FieldNotLocatable(f"{field_path!r} is under a key this module never rewrites")

    file = Path(path)
    text = file.read_text(encoding="utf-8")
    root = yaml.compose(text)
    if root is None or not isinstance(root, yaml.MappingNode):
        raise FieldNotLocatable(f"{file} has no top-level mapping")

    top_id = _scalar(root, "id")
    if top_id == asset_id:
        return _span_for(text, root, field_path, document=root)

    for column in _column_nodes(root):
        physical = _scalar(column, "physical_name")
        if physical and top_id and derive_column_id(top_id, physical) == asset_id:
            return _span_for(text, column, field_path, document=root)

    raise FieldNotLocatable(
        f"{file} declares no asset {asset_id!r}. It declares {top_id!r}"
        + (
            f" and {len(list(_column_nodes(root)))} inline column(s)"
            if _column_nodes(root)
            else ""
        )
    )


def read_field(path: Path | str, *, asset_id: str, field_path: str) -> str:
    """The field's current value. What a caller puts in a patch's ``was``."""
    return locate(path, asset_id=asset_id, field_path=field_path).value


def apply_edit(
    path: Path | str,
    *,
    asset_id: str,
    field_path: str,
    was: str,
    becomes: str,
) -> str:
    """Replace one field and return the new file text. **Does not write.**

    Returning the text rather than writing it is deliberate: the caller is a bundle exporter that
    wants a diff against a staging tree, and a function that both computes and commits a change
    cannot be used to preview one. The exporter writes; this decides what to write.
    """
    file = Path(path)
    span = locate(file, asset_id=asset_id, field_path=field_path)
    if span.value != was:
        raise StaleValue(
            f"{file}:{asset_id}.{field_path} holds {span.value[:60]!r}, and the patch was authored "
            f"against {was[:60]!r}. The corpus moved under this patch -- another change landed, or "
            "somebody edited by hand. Re-read the field and re-draft rather than overwriting what "
            "is there."
        )
    text = file.read_text(encoding="utf-8")
    return text[: span.start] + _render(becomes, span) + text[span.end :]


# ── locating ──────────────────────────────────────────────────────────────────


def _column_nodes(root: yaml.MappingNode) -> list[yaml.MappingNode]:
    for key, value in root.value:
        if getattr(key, "value", None) == "columns" and isinstance(value, yaml.SequenceNode):
            return [item for item in value.value if isinstance(item, yaml.MappingNode)]
    return []


def _scalar(node: yaml.MappingNode, name: str) -> str | None:
    for key, value in node.value:
        if getattr(key, "value", None) == name and isinstance(value, yaml.ScalarNode):
            return str(value.value)
    return None


def _appears_more_than_once(target: yaml.Node, *, root: yaml.Node) -> bool:
    """Whether ``target`` is reachable from more than one place in ``root``.

    **By object identity, not by an ``anchor`` attribute** — PyYAML's nodes do not carry one.
    ``yaml.compose`` resolves an alias by returning *the same node object* at every reference, so
    counting identities is both the available check and the precise one: it catches a shared scalar
    whether the sharing came from an anchor, a merge key, or anything else.

    **``root`` is the tree ``target`` came from, and it has to be.** The first version re-composed
    the document from its text, which produces a *new* tree whose nodes are different objects — so
    ``node is target`` was never true, the count was always zero, and the check silently passed on
    every anchored scalar. Identity comparison across two parses is not a subtle bug so much as a
    category error, and it is the reason this takes a node rather than a string.
    """
    seen = 0

    def walk(node: yaml.Node) -> None:
        nonlocal seen
        if node is target:
            seen += 1
        if isinstance(node, yaml.MappingNode):
            for key, value in node.value:
                walk(key)
                walk(value)
        elif isinstance(node, yaml.SequenceNode):
            for item in node.value:
                walk(item)

    if root is not None:
        walk(root)
    return seen > 1


def _span_for(
    text: str, node: yaml.MappingNode, field_path: str, *, document: yaml.Node
) -> Span:
    for key, value in node.value:
        if getattr(key, "value", None) != field_path:
            continue
        if not isinstance(value, yaml.ScalarNode):
            raise FieldNotLocatable(
                f"{field_path!r} is a {type(value).__name__}, not a scalar; this module replaces "
                "scalars in place and will not restructure a document"
            )
        if _appears_more_than_once(value, root=document):
            raise FieldNotLocatable(
                f"{field_path!r} is an anchored scalar referenced from more than one place, so "
                "replacing it here would change those places too. A shared scalar is supposed to "
                "be shared; splitting it is a hand edit."
            )
        raw = text[value.start_mark.index : value.end_mark.index]
        return Span(
            start=value.start_mark.index,
            end=value.end_mark.index,
            style=value.style,
            indent=_block_indent(raw),
            width=_block_width(raw),
            value=str(value.value),
        )
    raise FieldNotLocatable(f"{field_path!r} is absent from this asset")


def _block_indent(raw: str) -> int:
    """Column the block's content sits at, read off the original rather than assumed.

    A file indented with four spaces must come back indented with four. Guessing two because that
    is this corpus's convention would reformat every file that is not this corpus.
    """
    for line in raw.splitlines()[1:]:
        if line.strip():
            return len(line) - len(line.lstrip(" "))
    return 0


def _block_width(raw: str) -> int:
    """The longest content line in the original block.

    Used as the rewrap width, so a replacement matches the file it is editing instead of imposing
    PyYAML's 80 or any other house number. The corpus this was written against wraps near 100, and
    hardcoding 80 would reflow every block it touched -- a whole-paragraph diff for a one-word
    change, which is the whole defect.
    """
    lines = [line.rstrip() for line in raw.splitlines()[1:] if line.strip()]
    return max((len(line) for line in lines), default=0)


# ── rendering ─────────────────────────────────────────────────────────────────


def _render(value: str, span: Span) -> str:
    """``value`` written the way ``span`` was written."""
    if span.style in (">", "|"):
        return _render_block(value, span)
    if span.style in ("'", '"'):
        return _render_quoted(value, span.style)
    return _render_plain(value)


def _render_block(value: str, span: Span) -> str:
    """A folded or literal block scalar at the original indentation and wrap width.

    The chomping indicator is ``-``: the original spans measured here all use ``>-``, and a block
    that ends in a newline the loader then strips is a byte the diff shows and nothing reads.
    """
    pad = " " * (span.indent or 2)
    width = max(span.width, span.indent + 20) if span.width else 100
    body = "\n".join(pad + line for line in _wrap(value, width - len(pad)))
    return f"{span.style}-\n{body}\n"


def _wrap(value: str, width: int) -> list[str]:
    """Greedy word wrap. A word longer than the width gets its own line rather than being cut.

    Greedy and not `textwrap`, for one reason: `textwrap` collapses runs of whitespace and would
    rewrite parts of the value nobody asked to change.
    """
    out: list[str] = []
    current = ""
    for word in value.split(" "):
        candidate = f"{current} {word}".strip()
        if current and len(candidate) > width:
            out.append(current)
            current = word
        else:
            current = candidate
    if current:
        out.append(current)
    return out or [""]


def _render_quoted(value: str, style: str) -> str:
    if style == "'":
        return "'" + value.replace("'", "''") + "'"
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return '"' + escaped + '"'


def _render_plain(value: str) -> str:
    """Plain if it is safe to write plain, single-quoted otherwise.

    The unsafe set is deliberately generous. Getting this wrong produces a file that either fails
    to load or -- worse -- loads as a different value, and quoting a string that did not need it
    costs two characters in a diff.
    """
    unsafe = (
        not value
        or value != value.strip()
        or "\n" in value
        or ": " in value
        or " #" in value
        or value[0] in "-?:,[]{}#&*!|>'\"%@`"
        or value.lower() in ("true", "false", "null", "~", "yes", "no", "on", "off")
        or _looks_numeric(value)
    )
    return _render_quoted(value, "'") if unsafe else value


def _looks_numeric(value: str) -> bool:
    try:
        float(value)
    except ValueError:
        return False
    return True


# The agreement between :data:`EDITABLE` and ``feedback/validate.py::EDITABLE_FIELD_PATHS`` is
# asserted **there** and not here, and the direction is forced: ``feedback`` sits above ``corpus``
# in ``tools/check_imports.py::LAYERS``, so an import of it from this module is an upward one --
# which the layering gate catches wherever the statement sits, function body included. The check
# is the same check; only its home changes.
