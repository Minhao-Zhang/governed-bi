"""Render retrieval context for the USER message (ADR 0005 §3.6).

Hit ⇒ full structural line + body. Pulled-in ⇒ **identifier and type**, and only
the further facts an asset is unusable without. ``summary`` never enters the
prompt. Field newlines are escaped losslessly (``\\n``).

**Why the pulled-in side got narrower.** §3.6 said "structural line always; body
if the asset was hit; nothing further if it was ``pulled_in``", and that is what
this rendered — so the body split was already in place while the *line* was
identical for both. Measured on the gold semantic layer (5 schemas, 923 assets,
``route_top_n = 1``), a turn hit 8 assets and the closure dragged in 77, and the
70 pulled-in **columns** were 4 179 of the block's 7 973 characters: 52 % of the
prompt was the fully-qualified name of a column nobody asked about, written out
once per column. Reference closure is the right rule for *correctness* — a column
whose table is absent is unusable — but it is not a relevance rule, and the two
sets are the only relevance signal the pipeline already computes.

So a pulled-in column is now written under the table that carries it, spelling
its schema and table once for the group instead of once per column, and the
descriptive extras that I3 does not require (table ``grain`` / ``row_count``,
join ``cardinality``, term ``synonyms``) are dropped from pulled-in assets. What
stays is everything I3 names — physical name, logical type, ``role``,
``reliability.suspect`` — plus the one fact per type that makes the asset
spellable at all: a join's ON clause (``complete_joins`` exists to put exactly
those in the prompt, §2.8.1), a metric's expression, a term's binding. §3.6:
*"or the prompt shows a join the model cannot spell"*.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence, Set
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
    evicted: dict[str, Any] | None = None,
) -> tuple[str, str]:
    """Return ``(context_block, context_hash)`` — sha256 hex of UTF-8 block.

    ``evicted`` is an **out-parameter**, filled only when the budget actually bit — same shape
    as ``facets._pass_one_hits``' ``ran`` and ``_rewritten_query``' ``spent``, and for the same
    reason: the caller needs a fact the return value has no room for.

    **The eviction had no witness at all.** :func:`_assemble_and_evict` drops asset bodies and
    then whole pulled-in tables, and reported nothing — no return value, no state field, no
    record field, no test. So a gold table that was routed, retrieved, licensed and then evicted
    for space is indistinguishable in every artifact from one that was rendered. That blind spot
    sits exactly between "table selection" and "generation", which are the two stages any
    attribution of the remaining loss has to tell apart, so every such attribution was
    unfounded. It also **returns the over-budget text** when the ladder is exhausted
    (see below), which nothing recorded either.
    """
    pieces = _build_pieces(retrieved, assets_by_id, schemas)
    block = _assemble_and_evict(pieces, budget_chars, evicted=evicted)
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
        # **A hit that is also in ``pulled_in`` is a hit.** ``pulled_in`` used to win, and
        # it is not merely the tie-break that reads better: ``connect_node`` finishes with
        # ``pulled_in.setdefault(join_id, "connect")`` over ``complete_joins``, so **every
        # join the question actually hit** is added to ``pulled_in`` after the fact and lost
        # its body. Measured on the gold layer: 2 of 2 and 3 of 3 join hits on two turns.
        # ``pulled_in`` records where an asset *entered* the set; it is not a claim that the
        # question missed it.
        is_hit = aid in hit_ids or aid in by_type_ids
        rows.append((aid, asset, is_hit, _score_for(aid, selected, retrieved)))
    rows.sort(key=lambda r: (_STRUCTURAL.index(_asset_type(r[1])), r[0]))

    roster, folded = _fold_pulled_in_columns(rows)

    if rows:
        pieces.append(_piece("context_header", "## Context"))
    for aid, asset, is_hit, score in rows:
        if aid in folded:
            continue
        at = _asset_type(asset)
        line = _structural_line(asset, terse=not is_hit)
        # The table's own columns follow it, so ``struct_text`` — what survives when the
        # budget drops a body — carries them too. Dropping a body must not silently drop
        # the roster: I3's point is that a column the model is never shown does not exist
        # to it, and the eviction ladder is not allowed to reintroduce that.
        tail = "\n" + "\n".join(roster[aid]) if aid in roster else ""
        body = _field(asset, "body") if is_hit else None
        if body:
            pieces.append(
                {
                    "kind": "struct_with_body",
                    "asset_id": aid,
                    "asset_type": at,
                    "evictable": False,
                    "score": score,
                    "text": f"{line}\nbody: {escape_field(str(body))}{tail}",
                    "struct_text": f"{line}{tail}",
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
                    "text": f"{line}{tail}",
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


def _fold_pulled_in_columns(
    rows: Sequence[tuple[str, Any, bool, float]],
) -> tuple[dict[str, list[str]], set[str]]:
    """``(table id -> roster lines, the column ids those lines replace)``.

    A pulled-in column is in the prompt because its table is, and its table is on the
    line above — so spelling ``schema.table.`` again in front of every one of its columns
    buys the reader nothing. On the measured turns that prefix was the single largest
    thing in the block. The roster keeps everything I3 requires (physical name, logical
    type, ``role``, ``reliability.suspect``) and drops only the repetition.

    **A hit column is never folded.** It keeps its own line and its body, because that is
    the whole distinction this render is built on.

    A column whose parent table is *not* among the rendered rows keeps its own line too.
    Reference closure should make that impossible, and if the closure ever fails to hold
    the column must stay independently namable rather than disappear into a table that is
    not there.
    """
    table_ids = {aid for aid, asset, _, _ in rows if _asset_type(asset) == "table"}
    roster: dict[str, list[str]] = {}
    folded: set[str] = set()
    for aid, asset, is_hit, _score in rows:
        if is_hit or _asset_type(asset) != "column":
            continue
        parent = _parent_table_id(asset, table_ids)
        if parent is None:
            continue
        roster.setdefault(parent, []).append(_roster_entry(asset))
        folded.add(aid)
    return roster, folded


def _roster_entry(asset: Any) -> str:
    """One pulled-in column, named relative to the table line it sits under."""
    parts = [f"- {escape_field(str(_field(asset, 'physical_name') or _field(asset, 'id') or ''))}"]
    parts.extend(_column_facts(asset))
    return " ".join(parts)


def _parent_table_id(asset: Any, table_ids: Set[str]) -> str | None:
    """The rendered table this column belongs to, or ``None``.

    ``parent_table`` is a physical name that a corpus may spell bare (``customers``) or
    schema-qualified (``address.country``) — ``retrieve/structure.py`` says so and declines
    to settle which. Both spellings are tried against the tables actually being rendered,
    and nothing is guessed: an unmatched parent returns ``None`` and the column keeps its
    own line.
    """
    parent = _field(asset, "parent_table")
    if not parent:
        return None
    if str(parent) in table_ids:
        return str(parent)
    schema = _field(asset, "schema")
    qualified = f"{schema}.{parent}" if schema else ""
    return qualified if qualified in table_ids else None


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
    """Suspect columns, named the way the context block names them.

    This keyed the prohibition on the **asset id** while the context block composed a
    qualified name, so a corpus whose two spellings differ published "do NOT use X" about
    a column it had shown as Y. :func:`_column_qualifier` is now the single answer for
    both, which is the only way the two halves can be checked against each other.
    """
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
        lines.append(f"- {escape_field(_column_qualifier(asset))}: suspect - {note_text}")
    return lines


def _assemble_and_evict(
    pieces: list[dict[str, Any]], budget: int, *, evicted: dict[str, Any] | None = None
) -> str:
    """The two-rung eviction ladder, now with a record of what it dropped.

    ``evicted`` accumulates ``bodies_dropped``, ``tables_dropped``, ``dropped_ids`` and
    ``over_budget`` — the last because the final ``return text`` hands back an **over-budget**
    block when both rungs are exhausted, and a caller that believes the budget was honoured is
    reading a number that is not true.
    """
    active = [dict(p) for p in pieces]
    text = _join(active)
    if len(text) <= budget:
        return text

    def _key(i: int) -> tuple[float, str]:
        return (float(active[i].get("score") or 0.0), str(active[i].get("asset_id") or ""))

    def _note(field: str, asset_id: Any = None) -> None:
        if evicted is None:
            return
        evicted[field] = int(evicted.get(field, 0)) + 1
        if asset_id:
            evicted.setdefault("dropped_ids", []).append(str(asset_id))

    for i in sorted(
        (i for i, p in enumerate(active) if p.get("kind") == "struct_with_body" and p.get("body_droppable")),
        key=_key,
    ):
        active[i]["text"] = active[i]["struct_text"]
        active[i]["body_droppable"] = False
        _note("bodies_dropped")
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
        _note("tables_dropped", active[i].get("asset_id"))
        text = _join(p for j, p in enumerate(active) if j not in drop)
        if len(text) <= budget:
            return text
    if evicted is not None:
        evicted["over_budget"] = len(text) - budget
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


def _structural_line(asset: Any, *, terse: bool = False) -> str:
    """One asset as a line. ``terse`` is the pulled-in form: identifier and type.

    One function rather than two, because the identifier of an asset must have exactly
    one spelling — a second renderer here would be a second answer to "what is this
    column called", and the ``## Reliability caveats`` block below already names the same
    columns by id. ``terse`` therefore omits fields; it never re-words one.

    What ``terse`` omits, and why each is safe to omit for an asset the question did not
    hit: a table's ``grain`` and ``row_count`` describe a table nobody asked about; a
    join's ``cardinality`` does not change how the join is *written*; a term's
    ``synonyms`` exist so the term can be **retrieved**, and by the time this renders
    retrieval has already happened.

    What ``terse`` keeps is not a judgement call: I3 requires physical name, logical
    type, ``role`` and ``reliability.suspect`` on every asset in context, and §3.6
    requires whatever makes the asset spellable — the join's ON clause, the metric's
    expression, the term's binding target.
    """
    at = _asset_type(asset)
    if at == "schema":
        return f"schema {escape_field(str(_field(asset, 'name') or _field(asset, 'id') or ''))}"
    if at == "table":
        schema, phys = _field(asset, "schema") or "", _field(asset, "physical_name") or _field(asset, "id") or ""
        spelled = f"{schema}.{phys}" if schema else str(phys)
        parts = [f"table {escape_field(spelled)}", *_tool_key(asset, spelled)]
        if not terse:
            if _field(asset, "grain"):
                parts.append(f"grain={escape_field(str(_field(asset, 'grain')))}")
            if _field(asset, "row_count") is not None:
                parts.append(f"rows={_field(asset, 'row_count')}")
        return " ".join(parts)
    if at == "column":
        spelled = _column_qualifier(asset)
        return " ".join(
            [
                f"column {escape_field(spelled)}",
                *_tool_key(asset, spelled),
                *_column_facts(asset),
            ]
        )
    if at == "join":
        left = _field(asset, "left_table") or ""
        right = _field(asset, "right_table") or ""
        on = _field(asset, "on") or ""
        parts = [f"join {escape_field(str(left))} >< {escape_field(str(right))} on {escape_field(str(on))}"]
        card = _field(asset, "cardinality")
        if card is not None and not terse:
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
        if syns and not terse:
            parts.append("synonyms=" + ",".join(escape_field(str(s)) for s in syns))
        binding = _field(asset, "binding")
        if binding is not None:
            tid = _field(binding, "target_id") if not isinstance(binding, str) else binding
            if tid:
                parts.append(f"binding={escape_field(str(tid))}")
        return " ".join(parts)
    return f"{at} {escape_field(str(_field(asset, 'id') or ''))}"


def _tool_key(asset: Any, spelled: str) -> list[str]:
    """``id=<asset id>`` when the engine's spelling is not the key the tools accept.

    **A key is not a name** (ADR 0008 D1), and the prompt was only ever given the name. The
    context renders ``physical_name`` because that is what a ``SELECT`` must say, while
    ``bounds.may_inspect_schema`` / ``may_sample`` / ``may_read_body`` all test membership of
    ``licensed`` and ``readable_assets``, which hold **asset ids**. Wherever ``slug()`` fired
    the two diverge — ``table airline.Air Carriers`` is rendered, ``airline.Air_Carriers_66c534``
    is licensed — so every tool call on the rendered spelling returned
    ``OUT_OF_SCOPE_MESSAGE``, deliberately indistinguishable from "not licensed"
    (``bounds.py``). The table was unreachable and the refusal was unreadable.

    Emitted only on divergence, so the 655 of 656 gold tables whose id and name agree render
    exactly as before and their ``context_hash`` does not move.
    """
    asset_id = str(_field(asset, "id") or "")
    if not asset_id or asset_id == spelled:
        return []
    return [f"id={escape_field(asset_id)}"]


def _column_qualifier(asset: Any) -> str:
    """How the model must spell this column, and how the caveats block already spells it.

    ``parent_table`` may be bare or schema-qualified (``retrieve/structure.py`` binds both
    and declines to settle which). This prefixed ``schema`` unconditionally, so the gold
    semantic layer — whose ``parent_table`` is qualified — rendered
    ``column address.address.CBSA.metro_id`` while ``## Reliability caveats`` two blocks
    later said ``address.CBSA.metro_id: suspect - DECOY column ... Do NOT use it``. One
    turn, two names for one column, and the half carrying the prohibition used the other
    one. The prefix is now added only when it is not already there.
    """
    schema = _field(asset, "schema") or ""
    parent = _field(asset, "parent_table") or ""
    phys = _field(asset, "physical_name") or ""
    if not parent:
        return str(_field(asset, "id") or phys)
    base = str(parent)
    if schema and not base.startswith(f"{schema}."):
        base = f"{schema}.{base}"
    return f"{base}.{phys}"


def _column_facts(asset: Any) -> list[str]:
    """The I3 fields of a column: logical type, ``role``, ``reliability.suspect``.

    **No ``terse`` parameter**, and its absence is the point: I3 names all three as
    always-render, so there is no pulled-in form of a column's facts to select between.
    Shared by the full line and the roster entry so a folded column cannot come to say
    something different about itself from an unfolded one.
    """
    parts: list[str] = []
    logical, physical = _field(asset, "logical_type"), _field(asset, "physical_type")
    type_val = getattr(logical, "value", logical) if logical is not None else physical
    if type_val is not None:
        parts.append(f"type={escape_field(str(type_val))}")
    role = _field(asset, "role")
    if role is not None:
        parts.append(f"role={escape_field(str(getattr(role, 'value', role)))}")
    # **Populated on every seeded column and rendered nowhere.** NULL semantics decide
    # whether `COUNT(col)` and `COUNT(*)` agree, whether `NOT IN (subquery)` returns the
    # empty set, and whether an inner join silently drops rows — three of the standard ways a
    # BIRD answer comes out wrong while the SQL reads correctly. It was reachable only via
    # `inspect_schema`, a tool the analyst prompt does not mention.
    nullable = _field(asset, "nullable")
    if nullable is not None:
        parts.append(f"nullable={'true' if nullable else 'false'}")
    rel = _field(asset, "reliability")
    if rel is not None and not isinstance(rel, str):
        status = getattr(_field(rel, "status"), "value", _field(rel, "status"))
        if str(status) == "suspect":
            parts.append("suspect=true")
    return parts


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
