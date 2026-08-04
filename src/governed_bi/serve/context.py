"""Render retrieval context for the USER message (ADR 0005 §3.6).

Hit ⇒ structural line + body. Pulled-in ⇒ structure only. ``summary`` never
enters the prompt. Field newlines are escaped losslessly (``\\n``).
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from typing import Any

from governed_bi.serve.runtime import DEFAULT_CONTEXT_BUDGET

__all__ = ["render_context", "escape_field", "EMPTY_CONTEXT"]

EMPTY_CONTEXT = "(no context)"
_STRUCTURAL = ("schema", "table", "column", "join", "metric", "term")


def escape_field(value: str) -> str:
    """Lossless escape so embedded newlines cannot open a prompt section."""
    return value.replace("\\", "\\\\").replace("\r", "\\r").replace("\n", "\\n")


def render_context(
    *,
    retrieved: Mapping[str, Any],
    assets_by_id: Mapping[str, Any],
    schemas: Sequence[str],
    budget_chars: int = DEFAULT_CONTEXT_BUDGET,
) -> tuple[str, str]:
    """Return ``(context_block, context_hash)`` — sha256 hex of UTF-8 block."""
    pieces = _build_pieces(retrieved, assets_by_id, schemas)
    block = _assemble_and_evict(pieces, budget_chars)
    if not block.strip():
        block = EMPTY_CONTEXT
    return block, hashlib.sha256(block.encode("utf-8")).hexdigest()


def _build_pieces(
    retrieved: Mapping[str, Any],
    assets_by_id: Mapping[str, Any],
    schemas: Sequence[str],
) -> list[dict[str, Any]]:
    selected = retrieved.get("selected") or {}
    pulled_in = retrieved.get("pulled_in") or {}
    by_type = retrieved.get("by_type") or {}
    hit_ids = {str(i) for i in selected}
    pulled_ids = {str(i) for i in pulled_in}
    by_type_ids = {str(x) for xs in by_type.values() for x in (xs or ())}

    ids: list[str] = []
    seen: set[str] = set()

    def _add(aid: str) -> None:
        if aid not in seen:
            seen.add(aid)
            ids.append(aid)

    for name in schemas:
        sid = _schema_asset_id(str(name), assets_by_id, by_type)
        if sid:
            _add(sid)
    for type_key in (*_STRUCTURAL, "few_shot"):
        for aid in by_type.get(type_key) or ():
            _add(str(aid))
    for aid in pulled_in:
        _add(str(aid))

    pieces: list[dict[str, Any]] = []

    rules = _collect_rules(ids, assets_by_id, schemas)
    if rules:
        lines = ["## Must honour", *(f"- {escape_field(r)}" for r in rules)]
        pieces.append(_piece("rules", "\n".join(lines)))

    rows: list[tuple[str, Any, bool, float]] = []
    for aid in ids:
        asset = assets_by_id.get(aid)
        if asset is None:
            continue
        at = _asset_type(asset)
        if at not in _STRUCTURAL:
            continue
        is_hit = aid not in pulled_ids and (aid in hit_ids or aid in by_type_ids)
        rows.append((aid, asset, is_hit, _score_for(aid, selected, retrieved)))
    rows.sort(key=lambda r: (_STRUCTURAL.index(_asset_type(r[1])), r[0]))

    if rows:
        pieces.append(_piece("context_header", "## Context"))
    for aid, asset, is_hit, score in rows:
        at = _asset_type(asset)
        line = _structural_line(asset)
        body = _field(asset, "body") if is_hit else None
        if body:
            pieces.append(
                {
                    "kind": "struct_with_body",
                    "asset_id": aid,
                    "asset_type": at,
                    "evictable": False,
                    "score": score,
                    "text": f"{line}\nbody: {escape_field(str(body))}",
                    "struct_text": line,
                    "body_droppable": True,
                }
            )
        else:
            pieces.append(
                {
                    "kind": "struct",
                    "asset_id": aid,
                    "asset_type": at,
                    "evictable": (not is_hit) and at == "table" and aid in pulled_ids,
                    "score": score,
                    "text": line,
                }
            )

    caveats = _collect_caveats(ids, assets_by_id)
    if caveats:
        pieces.append(_piece("caveats", "\n".join(["## Reliability caveats", *caveats])))

    few_blocks: list[str] = []
    for aid in (str(x) for x in (by_type.get("few_shot") or ())):
        asset = assets_by_id.get(aid)
        if asset is None:
            continue
        body = _field(asset, "body")
        if not body:
            sql, summary = _field(asset, "sql"), _field(asset, "summary")
            if summary and sql:
                body = f"{summary}\n{sql}"
            elif sql:
                body = str(sql)
            else:
                continue
        few_blocks.append(escape_field(str(body)))
    if few_blocks:
        pieces.append(_piece("few_shot", "## Few-shots\n" + "\n\n".join(few_blocks)))

    return pieces


def _piece(kind: str, text: str) -> dict[str, Any]:
    return {"kind": kind, "evictable": False, "score": 1.0, "text": text}


def _collect_rules(
    ids: Sequence[str],
    assets_by_id: Mapping[str, Any],
    schemas: Sequence[str],
) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    schema_names = {str(s) for s in schemas}
    for aid in ids:
        asset = assets_by_id.get(aid)
        if asset is None:
            continue
        at = _asset_type(asset)
        if at == "schema" and str(_field(asset, "name") or "") not in schema_names:
            continue
        if at not in ("schema", "table"):
            continue
        for rule in _field(asset, "rules") or ():
            text = str(rule)
            if text and text not in seen:
                seen.add(text)
                out.append(text)
    return out


def _collect_caveats(ids: Sequence[str], assets_by_id: Mapping[str, Any]) -> list[str]:
    lines: list[str] = []
    for aid in sorted(ids):
        asset = assets_by_id.get(aid)
        if asset is None or _asset_type(asset) != "column":
            continue
        rel = _field(asset, "reliability")
        if rel is None or isinstance(rel, str):
            continue
        status = getattr(_field(rel, "status"), "value", _field(rel, "status"))
        if str(status) != "suspect":
            continue
        note = _field(rel, "note")
        note_text = escape_field(str(note)) if note else "UNRELIABLE. DO NOT USE"
        lines.append(f"- {aid}: suspect - {note_text}")
    return lines


def _assemble_and_evict(pieces: list[dict[str, Any]], budget: int) -> str:
    active = [dict(p) for p in pieces]
    text = _join(active)
    if len(text) <= budget:
        return text

    def _key(i: int) -> tuple[float, str]:
        return (float(active[i].get("score") or 0.0), str(active[i].get("asset_id") or ""))

    for i in sorted(
        (i for i, p in enumerate(active) if p.get("kind") == "struct_with_body" and p.get("body_droppable")),
        key=_key,
    ):
        active[i]["text"] = active[i]["struct_text"]
        active[i]["body_droppable"] = False
        text = _join(active)
        if len(text) <= budget:
            return text

    drop: set[int] = set()
    for i in sorted(
        (
            i
            for i, p in enumerate(active)
            if p.get("kind") == "struct" and p.get("evictable") and p.get("asset_type") == "table"
        ),
        key=_key,
    ):
        drop.add(i)
        text = _join(p for j, p in enumerate(active) if j not in drop)
        if len(text) <= budget:
            return text
    return text


def _join(pieces) -> str:
    sections: list[str] = []
    ctx: list[str] = []

    def flush() -> None:
        if ctx:
            sections.append("\n".join(ctx))
            ctx.clear()

    for p in pieces:
        text = p.get("text")
        if not text:
            continue
        kind = p.get("kind")
        if kind in ("struct", "struct_with_body", "context_header"):
            if kind == "context_header":
                flush()
                ctx[:] = [text]
            else:
                if not ctx:
                    ctx[:] = ["## Context"]
                ctx.append(text)
        else:
            flush()
            sections.append(text)
    flush()
    return "\n\n".join(sections)


def _structural_line(asset: Any) -> str:
    at = _asset_type(asset)
    if at == "schema":
        return f"schema {escape_field(str(_field(asset, 'name') or _field(asset, 'id') or ''))}"
    if at == "table":
        schema, phys = _field(asset, "schema") or "", _field(asset, "physical_name") or _field(asset, "id") or ""
        parts = [f"table {escape_field(f'{schema}.{phys}' if schema else str(phys))}"]
        if _field(asset, "grain"):
            parts.append(f"grain={escape_field(str(_field(asset, 'grain')))}")
        if _field(asset, "row_count") is not None:
            parts.append(f"rows={_field(asset, 'row_count')}")
        return " ".join(parts)
    if at == "column":
        schema, parent, phys = (
            _field(asset, "schema") or "",
            _field(asset, "parent_table") or "",
            _field(asset, "physical_name") or "",
        )
        if schema and parent:
            qual = f"{schema}.{parent}.{phys}"
        elif parent:
            qual = f"{parent}.{phys}"
        else:
            qual = str(_field(asset, "id") or phys)
        parts = [f"column {escape_field(qual)}"]
        logical, physical = _field(asset, "logical_type"), _field(asset, "physical_type")
        type_val = getattr(logical, "value", logical) if logical is not None else physical
        if type_val is not None:
            parts.append(f"type={escape_field(str(type_val))}")
        role = _field(asset, "role")
        if role is not None:
            parts.append(f"role={escape_field(str(getattr(role, 'value', role)))}")
        rel = _field(asset, "reliability")
        if rel is not None and not isinstance(rel, str):
            status = getattr(_field(rel, "status"), "value", _field(rel, "status"))
            if str(status) == "suspect":
                parts.append("suspect=true")
        return " ".join(parts)
    if at == "join":
        left = _field(asset, "left_table") or ""
        right = _field(asset, "right_table") or ""
        on = _field(asset, "on") or ""
        parts = [f"join {escape_field(str(left))} >< {escape_field(str(right))} on {escape_field(str(on))}"]
        card = _field(asset, "cardinality")
        if card is not None:
            parts.append(f"cardinality={escape_field(str(getattr(card, 'value', card)))}")
        return " ".join(parts)
    if at == "metric":
        name = _field(asset, "name") or _field(asset, "id") or ""
        return (
            f"metric {escape_field(str(name))} = {escape_field(str(_field(asset, 'expression') or ''))} "
            f"base={escape_field(str(_field(asset, 'base_table') or ''))}"
        )
    if at == "term":
        parts = [f"term {escape_field(str(_field(asset, 'name') or _field(asset, 'id') or ''))}"]
        syns = _field(asset, "synonyms") or ()
        if syns:
            parts.append("synonyms=" + ",".join(escape_field(str(s)) for s in syns))
        binding = _field(asset, "binding")
        if binding is not None:
            tid = _field(binding, "target_id") if not isinstance(binding, str) else binding
            if tid:
                parts.append(f"binding={escape_field(str(tid))}")
        return " ".join(parts)
    return f"{at} {escape_field(str(_field(asset, 'id') or ''))}"


def _field(obj: Any, name: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, Mapping):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _asset_type(asset: Any) -> str:
    raw = _field(asset, "asset_type")
    return "" if raw is None else str(getattr(raw, "value", raw))


def _score_for(asset_id: str, selected: Mapping[str, Any], retrieved: Mapping[str, Any]) -> float:
    hit = selected.get(asset_id)
    if hit is not None:
        return _selected_hit_score(hit)
    best = 0.0
    for h in (retrieved.get("attributions") or {}).get(asset_id) or ():
        best = max(best, _selected_hit_score(h))
    return best


def _selected_hit_score(hit: Any) -> float:
    if hit is None:
        return 0.0
    score = hit.get("score") if isinstance(hit, Mapping) else getattr(hit, "score", None)
    if score is None:
        return 0.0
    try:
        return float(score)
    except (TypeError, ValueError):
        return 0.0


def _schema_asset_id(
    name: str,
    assets_by_id: Mapping[str, Any],
    by_type: Mapping[str, Any],
) -> str | None:
    for aid in by_type.get("schema") or ():
        asset = assets_by_id.get(str(aid))
        if asset is not None and str(_field(asset, "name") or "") == name:
            return str(aid)
    if name in assets_by_id and _asset_type(assets_by_id[name]) == "schema":
        return name
    for aid, asset in assets_by_id.items():
        if _asset_type(asset) == "schema" and str(_field(asset, "name") or "") == name:
            return str(aid)
    return None
