"""Eval over the pooled BIRD data lake — one Postgres, 57 curated schemas, no pin.

**Two stages, and the cheap one runs first on purpose.**

``routing_recall`` needs no model at all: with no extraction model the facet queries fall
back to the raw question, so routing is BM25 over the index and costs nothing. It answers
the question that currently dominates every other number — *is the gold schema even a
candidate* — over all 1 351 test questions for free.

``run_live`` is the arm that costs money. Running it before the free measurement would be
paying to discover that the router never shortlisted the right schema, which is a result
you can have for nothing.

**The reference for correctness is the gold result set, never the SQL string.** Each
question carries ``sql_rename``, the gold statement written against the obfuscated
Postgres schemas, and grading executes both and compares fingerprints
(:mod:`governed_bi.eval.grade`). Comparing SQL text would mark a correct answer wrong for
choosing a different join order.

Costs are **measured, not estimated**: :func:`observed_spend` reads the usage rows the
record already carries and prices them through :mod:`governed_bi.measure.price`, so a
decision to scale a run up is made against observed spend on a small batch.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

__all__ = [
    "load_questions",
    "routing_recall",
    "run_live",
    "observed_spend",
    "summarise_routing",
    "gold_tables",
    "table_coverage",
]


def load_questions(
    path: str | Path,
    *,
    schemas: Iterable[str] | None = None,
    limit: int | None = None,
    per_schema: int | None = None,
) -> list[dict[str, Any]]:
    """Test questions, filtered to schemas the corpus actually carries.

    A question whose ``db_id`` is not in the corpus is **not** a failure of the engine and
    must not be scored as one: the corpus covers 57 of the database's 70 schemas, so
    including the other 13 would report a curation gap as a retrieval gap. The filter is
    the caller's declared schema set, so what was excluded is a number the caller has.

    ``gold_sql`` is set from ``sql_rename`` — the statement written against the obfuscated
    schemas, which is what this database is. ``sql_base`` names the un-renamed originals
    and ``sql_sqlite`` is a different engine; either would fail to execute here, and a
    gold statement that fails to execute grades every prediction wrong.

    ``per_schema`` caps questions per schema. Without it a stratified sample is
    accidentally weighted by whichever schema BIRD happened to ask most about, and a
    per-schema effect then reads as an overall one.
    """
    allowed = None if schemas is None else {str(s) for s in schemas}
    kept: list[dict[str, Any]] = []
    seen_per_schema: dict[str, int] = {}
    skipped_uncovered = 0

    with Path(path).open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            db = str(row.get("db_id") or "")
            if allowed is not None and db not in allowed:
                skipped_uncovered += 1
                continue
            if per_schema is not None and seen_per_schema.get(db, 0) >= per_schema:
                continue
            gold = row.get("sql_rename")
            if not gold:
                continue
            seen_per_schema[db] = seen_per_schema.get(db, 0) + 1
            kept.append(
                {
                    "question_id": str(row.get("question_id")),
                    "question": str(row.get("question") or ""),
                    "evidence": row.get("evidence_rename") or row.get("evidence"),
                    "db_id": db,
                    "gold_sql": str(gold),
                    "difficulty": row.get("difficulty") or "",
                }
            )
            if limit is not None and len(kept) >= limit:
                break

    if kept:
        kept[0]["_skipped_uncovered"] = skipped_uncovered
    return kept


def routing_recall(
    questions: Sequence[Mapping[str, Any]],
    *,
    session: Any,
    top_n: int | None = None,
) -> list[dict[str, Any]]:
    """Per question: was the gold schema shortlisted, and at what rank. **No model.**

    Costs nothing because a session with ``agent_model=None`` serves the stub answer path:
    facets, routing, retrieval, resolve and connect all run for real and no provider call
    is made.

    **Runs the compiled graph, not the nodes by hand.** The first version of this called
    ``facet_*_node`` then ``route_node`` and merged their returns with ``dict.update`` — and
    measured **0.000 recall with every gold schema "never scored"**, because the five facet
    nodes all write to one ``facets`` channel whose reducer merges by name, and a plain
    update replaces it four times instead. Assembling state by hand is a second answer to
    how a turn runs, and the second answer was wrong in a way that looked like a finding.

    ``rank`` is the gold schema's position in ``schema_ranking``, which holds **all** scored
    schemas pre-truncation. That is the field's whole purpose: without it "the gold schema
    was not a candidate" and "it ranked 4th" are the same observation, and v1 published a
    documented failure bucket at a perfect score over 2 030 rows because of exactly that
    collapse.
    """
    from governed_bi.serve.graph import compile_graph

    graph = compile_graph()
    rows: list[dict[str, Any]] = []
    for index, question in enumerate(questions):
        turn = session.turn(str(question["question"]), turn_index=1)
        if top_n is not None:
            turn["route_top_n"] = int(top_n)
        config = session.configurable(question=str(question["question"]))
        # One thread per question: a shared thread would carry the previous question's
        # per-turn channels into this one, which is the defect `PER_TURN_RESET` exists for.
        config["configurable"]["thread_id"] = f"routing-{index}-{question['question_id']}"
        out = graph.invoke(turn, config)

        selected = [str(s) for s in (out.get("schemas") or ())]
        ranking = [
            str(pair[0]) for pair in ((out.get("retrieved") or {}).get("schema_ranking") or ())
        ]
        licensed = [str(x) for x in (out.get("licensed") or ())]
        gold = str(question["db_id"])
        rows.append(
            {
                "question_id": str(question["question_id"]),
                "db_id": gold,
                "selected": selected,
                "hit": gold in selected,
                # 1-based; None means the router scored it nowhere at all, which is a
                # different failure from ranking it low.
                "rank": (ranking.index(gold) + 1) if gold in ranking else None,
                "n_scored": len(ranking),
                # What survived `connect`'s component pick. `hit` says the router
                # shortlisted the gold schema; this says the turn could still reach it,
                # which are two different failures.
                "licensed_schemas": sorted({t.split(".", 1)[0] for t in licensed}),
                "reached_gold": any(t.startswith(f"{gold}.") for t in licensed),
                "path_kind": out.get("path_kind"),
                "terminal_reason": out.get("terminal_reason"),
            }
        )
    return rows


def summarise_routing(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Recall at the shortlist, plus recall at 1/3/5/10 over the full ranking.

    Reported together because they answer different questions: recall@shortlist is what
    the engine currently does, and recall@k is what raising ``route_top_n`` could buy. A
    gold schema at rank 7 is not fixable by a better picker and is fixable by a better
    index; one at rank 2 is the opposite.
    """
    total = len(rows) or 1
    ranks = [r.get("rank") for r in rows]
    at = {
        f"recall@{k}": sum(1 for rank in ranks if rank is not None and rank <= k) / total
        for k in (1, 3, 5, 10)
    }
    return {
        "n": len(rows),
        "recall_at_shortlist": sum(1 for r in rows if r.get("hit")) / total,
        # The shortlist is not the whole story: `connect` keeps one component, so the gold
        # schema can be shortlisted and still dropped. This is the number an answer needs.
        "reached_gold": sum(1 for r in rows if r.get("reached_gold")) / total,
        **at,
        "never_scored": sum(1 for rank in ranks if rank is None),
        "median_rank_when_scored": _median([r for r in ranks if r is not None]),
    }


def _median(values: Sequence[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[mid])
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def run_live(
    questions: Sequence[Mapping[str, Any]],
    *,
    session: Any,
    order_sensitive_qids: Iterable[str] = (),
    run_id: str | None = None,
) -> list[dict[str, Any]]:
    """Serve every question with the session's real model and grade against gold.

    Delegates to :func:`~governed_bi.eval.harness.run_arm` with ``session=`` so every turn
    is minted by ``Session.turn`` — the run constants are the session's own, not a
    fabricated ``f"corpus-{arm}"``.
    """
    from governed_bi.eval.arms import live_arm
    from governed_bi.eval.harness import run_arm

    return run_arm(
        questions,
        live_arm(session),
        order_sensitive_qids=frozenset(str(q) for q in order_sensitive_qids),
        run_id=run_id,
        session=session,
    )


def gold_tables(sql: str) -> set[str] | None:
    """The tables a gold statement reads, qualified. ``None`` when it does not parse.

    CTE names are excluded: a CTE is a name the statement *defines*, so counting it as a
    required table would make every gold query with a ``WITH`` clause look unsatisfiable.
    """
    import sqlglot
    from sqlglot import expressions as exp

    try:
        tree = sqlglot.parse_one(sql, dialect="postgres")
    except Exception:  # noqa: BLE001 — an unparseable gold statement is a data fact
        return None
    if tree is None:
        return None
    defined = {str(c.alias_or_name).lower() for c in tree.find_all(exp.CTE) if c.alias_or_name}
    out: set[str] = set()
    for table in tree.find_all(exp.Table):
        name = str(table.name or "")
        if not name or name.lower() in defined:
            continue
        out.add(f"{table.db}.{name}" if table.db else name)
    return out


def table_coverage(
    rows: Sequence[Mapping[str, Any]], gold_sql_by_qid: Mapping[str, str]
) -> dict[str, Any]:
    """**The EX ceiling.** How often every table the gold statement reads was licensed.

    Sharper than schema reachability, and it is the number a run should lead with. A turn can
    route to the right schema and still be unable to answer, because the per-type retrieval
    budget licenses at most ``ASSET_REGISTER[table].budget`` ranked tables — so a question
    needing a table outside that set cannot succeed however good the model is. Measured on the
    xhigh arm at 344 rows: **51.2%** of questions had all their gold tables, against a
    *schema* reachability of 62.5%. EX was 0.049.

    That splits the problem in two, which one EX number cannot: whether a question was
    *answerable at all* under this retrieval, and whether the model converted it when it was.

    Compared case-insensitively. Licensed ids carry the slug (ADR 0008 D1) and a gold
    statement carries the engine's spelling; those agree for every identifier whose slug is
    its own name, which is 655 of 656 tables here. The exception (``Air Carriers``) is
    reported as uncovered rather than silently matched, because a comparison that guessed
    would be the fail-open shape ``structure.py`` exists to refuse.
    """
    full = partial = none = unparsed = 0
    for row in rows:
        sql = gold_sql_by_qid.get(str(row.get("question_id")))
        if not sql:
            continue
        needed = gold_tables(sql)
        if needed is None:
            unparsed += 1
            continue
        licensed = {str(t).lower() for t in (row.get("licensed") or ())}
        hits = sum(1 for table in needed if table.lower() in licensed)
        if needed and hits == len(needed):
            full += 1
        elif hits:
            partial += 1
        else:
            none += 1
    total = full + partial + none or 1
    return {
        "n": full + partial + none,
        "all_gold_tables_licensed": full / total,
        "some_licensed": partial / total,
        "none_licensed": none / total,
        "gold_sql_unparsed": unparsed,
    }


def observed_spend(
    rows: Sequence[Mapping[str, Any]], *, model: str, asof: Any
) -> dict[str, Any]:
    """What this batch cost, as a :class:`~governed_bi.register.quantity.Measured`.

    Delegates the arithmetic to :func:`~governed_bi.measure.price.estimate_run_cost` — one
    implementation of "what did these calls cost", which is also why this is not called
    ``estimate_cost``: that name is taken by the per-call function next door, and two
    functions with one name is what ``tools/check_one_implementation.py`` refuses.

    The total stays ``Measured`` and is **not** rounded here. A rounding helper in ``src/``
    is how v1 turned an unmeasured quantity into ``0.0`` on the way to a report, so
    formatting lives only in ``Measured.render()`` and a caller that wants a string asks
    for one. One unpriced model makes the whole batch unpriceable, which is
    ``Measured.combine``'s behaviour and the behaviour wanted: a total that silently
    excludes the model it could not price is a total of something else.
    """
    from governed_bi.measure.price import estimate_run_cost

    usages: list[Mapping[str, Any]] = []
    tokens_in = 0
    tokens_out = 0
    for row in rows:
        record = row.get("record") or {}
        for entry in row.get("usage") or record.get("usage") or ():
            if not isinstance(entry, Mapping):
                continue
            usages.append(entry)
            tokens_in += int(entry.get("input_tokens") or 0)
            tokens_out += int(entry.get("output_tokens") or 0)

    total = estimate_run_cost(model, usages, asof=asof)
    return {
        "rows": len(rows),
        "calls_priced": len(usages),
        "usd_total": total.render() if hasattr(total, "render") else str(total),
        "usd_measured": bool(getattr(total, "is_measured", False)),
        "input_tokens": tokens_in,
        "output_tokens": tokens_out,
    }
