"""Eval over the pooled data lake: routing first, then a paid config.

Routing needs no model. Correctness is the gold result set, not the SQL
string, so a different join order is not a wrong answer. Token totals stay
in tokens: a price table here overstated a run nine-fold.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Collection, Iterable, Mapping, MutableMapping, Sequence
from pathlib import Path
from typing import Any

from governed_bi.registry.stages import Outcome
from governed_bi.registry.stored_names import artifact_get, artifact_has

__all__ = [
    "load_questions",
    "dataset_qid_lists",
    "dataset_leakage_qids",
    "attach_quality_flags",
    "attach_gold_fingerprints",
    "routing_recall",
    "observed_tokens",
    "summarise_routing",
    "gold_tables",
    "gold_table_ids",
    "table_coverage",
    "retrieval_funnel",
]

#: The lists ``order_sensitive_qids.json`` publishes, and the only names read for them.
QID_LIST_NAMES = ("order_sensitive", "exec_failed")


def dataset_qid_lists(dataset: str | Path) -> dict[str, set[str]]:
    """A present file that names none of the known lists raises. An empty exclusion set looks like nothing was declared;
    a missing file is that case for real."""
    path = Path(dataset) / "order_sensitive_qids.json"
    if not path.exists():
        return {name: set() for name in QID_LIST_NAMES}
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, list):
        # A bare list is the order-sensitive set only.
        return {"order_sensitive": {str(q) for q in raw}, "exec_failed": set()}
    if not any(name in raw for name in QID_LIST_NAMES):
        raise KeyError(
            f"{path} carries none of {QID_LIST_NAMES}; its keys are {sorted(raw)}. "
            "Refusing to report an empty exclusion set as though the dataset declared one."
        )
    return {name: {str(q) for q in (raw.get(name) or ())} for name in QID_LIST_NAMES}


def dataset_leakage_qids(dataset: str | Path) -> set[str]:
    """Reads ``union``. Tagged, not dropped. A missing file means none declared; no ``union`` raises."""
    path = Path(dataset) / "leakage_test_qids.json"
    if not path.exists():
        return set()
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, list):
        return {str(q) for q in raw}
    if "union" not in raw:
        raise KeyError(
            f"{path} has no 'union'; its keys are {sorted(raw)}. Refusing to report an empty "
            "leakage set as though the dataset declared one."
        )
    return {str(q) for q in (raw.get("union") or ())}


def attach_quality_flags(
    questions: Sequence[MutableMapping[str, Any]],
    *,
    leakage: Collection[str] = (),
    order_sensitive: Collection[str] = (),
    exec_failed: Collection[str] = (),
) -> dict[str, int]:
    """Tags stay on the row. ``degenerate`` is a gold that reads no table; an unparseable gold is not that case."""
    buckets = (
        ("leakage", {str(q) for q in leakage}),
        ("order_sensitive", {str(q) for q in order_sensitive}),
        ("exec_failed", {str(q) for q in exec_failed}),
    )
    counts = {name: 0 for name, _ in buckets}
    counts["degenerate"] = 0
    for question in questions:
        qid = str(question.get("question_id"))
        flags = [name for name, ids in buckets if qid in ids]
        # Unparseable gold is `None`, not degenerate. Collapsing them shrinks the denominator.
        if gold_tables(str(question.get("gold_sql") or "")) == set():
            flags.append("degenerate")
        question["quality_flags"] = flags
        for name in flags:
            counts[name] += 1
    return counts


def attach_gold_fingerprints(
    questions: Sequence[MutableMapping[str, Any]],
    dataset: str | Path,
    *,
    dsn_key: str,
    order_sensitive: Collection[str] = (),
) -> dict[str, int]:
    """Attach a digest only for this statement. Reject another database, a failed gold, a mismatched hash, or an
    order-sensitive question: that digest sorts."""
    path = Path(dataset) / f"gold_result_hashes_{dsn_key}.jsonl"
    counts = {
        "attached": 0,
        "no_row": 0,
        "recorded_error": 0,
        "other_database": 0,
        "statement_changed": 0,
        "order_sensitive": 0,
        "no_file": 0,
    }
    if not path.exists():
        counts["no_file"] = len(questions)
        return counts

    shipped: dict[str, Mapping[str, Any]] = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                row = json.loads(line)
                shipped[str(row.get("question_id"))] = row

    skip_order = {str(q) for q in order_sensitive}
    for question in questions:
        qid = str(question.get("question_id"))
        row = shipped.get(qid)
        if row is None:
            counts["no_row"] += 1
            continue
        if row.get("error"):
            counts["recorded_error"] += 1
            continue
        if str(row.get("dsn_key") or "") != dsn_key:
            counts["other_database"] += 1
            continue
        gold_sql = str(question.get("gold_sql") or "")
        recorded = str(row.get("sql_sha256") or "")
        if recorded and hashlib.sha256(gold_sql.encode("utf-8")).hexdigest() != recorded:
            counts["statement_changed"] += 1
            continue
        if qid in skip_order:
            counts["order_sensitive"] += 1
            continue
        digest = row.get("hash_lenient")
        if not digest:
            counts["no_row"] += 1
            continue
        question["gold_fingerprint"] = str(digest)
        counts["attached"] += 1
    return counts


def load_questions(
    path: str | Path,
    *,
    schemas: Iterable[str] | None = None,
    limit: int | None = None,
    per_schema: int | None = None,
) -> list[dict[str, Any]]:
    """Questions whose schema the corpus carries. A missing schema is a curation gap. ``gold_sql`` is ``sql_rename``;
    ``per_schema`` stops one schema dominating."""
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
    """No model. Runs the compiled graph: hand-built state replaces the shared channel and reports zero recall. ``rank``
    is before truncation."""
    from governed_bi.serve.graph import compile_graph

    graph = compile_graph()
    # One saver for the loop, emptied per question. The default saver never
    # expires, so a thread per question would retain state nothing rereads.
    saver = getattr(graph, "checkpointer", None)
    rows: list[dict[str, Any]] = []
    for index, question in enumerate(questions):
        turn = session.turn(str(question["question"]), turn_index=1)
        if top_n is not None:
            turn["route_top_n"] = int(top_n)
        config = session.configurable(question=str(question["question"]))
        # One thread per question. A shared thread keeps the previous turn's channels.
        thread_id = f"routing-{index}-{question['question_id']}"
        config["configurable"]["thread_id"] = thread_id
        out = graph.invoke(turn, config)

        selected = [str(s) for s in (out.get("schemas") or ())]
        ranking = [
            str(pair[0]) for pair in ((out.get("retrieved") or {}).get("schema_ranking") or ())
        ]
        retrieved_tables = [str(x) for x in (out.get("retrieved_tables") or ())]
        gold = str(question["db_id"])
        rows.append(
            {
                "question_id": str(question["question_id"]),
                "db_id": gold,
                "selected": selected,
                "hit": gold in selected,
                # 1-based. None means never scored, which is different from ranking low.
                "rank": (ranking.index(gold) + 1) if gold in ranking else None,
                "n_scored": len(ranking),
                # Table ids, not only schemas. Coverage reads this key; without it the ceiling is a fake zero.
                "retrieved_tables": retrieved_tables,
                # Schemas `connect` kept. `hit` is only the shortlist.
                "retrieved_tables_schemas": sorted({t.split(".", 1)[0] for t in retrieved_tables}),
                "reached_gold": any(t.startswith(f"{gold}.") for t in retrieved_tables),
                "path_kind": out.get("path_kind"),
                "terminal_reason": out.get("terminal_reason"),
            }
        )
        # Delete after the row: the fields come from the returned state.
        # Sync, because this saver's delete does not block. The durable saver's
        # sync delete hangs, which is why the harness evicts asynchronously.
        if saver is not None:
            saver.delete_thread(thread_id)
    return rows


def summarise_routing(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Recall at the shortlist and at 1/3/5/10. Recall at k is what a larger ``route_top_n`` could reach."""
    total = len(rows) or 1
    ranks = [r.get("rank") for r in rows]
    at = {
        f"recall@{k}": sum(1 for rank in ranks if rank is not None and rank <= k) / total
        for k in (1, 3, 5, 10)
    }
    return {
        "n": len(rows),
        "recall_at_shortlist": sum(1 for r in rows if r.get("hit")) / total,
        # `connect` keeps one component, so a shortlisted schema can still be dropped.
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


def gold_tables(sql: str) -> set[str] | None:
    """Qualified tables a gold reads, or ``None`` if it does not parse. CTE names are excluded."""
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


def gold_table_ids(qualified: str) -> set[str]:
    """Asset ids a gold table could match (ADR 0008). ``slug`` rebuilds the id; the raw spelling is kept. Case-folding,
    normalising spaces, or asking the corpus misses."""
    # Imported here: ``corpus.identity`` imports ``sqlglot`` at module scope, and
    # artifact-only drivers import this module.
    from governed_bi.corpus.identity import UnsafeName, slug

    keys = {qualified.lower()}
    schema, _, name = qualified.rpartition(".")
    if not name:
        return keys
    try:
        derived = slug(name)
    except UnsafeName:
        # A name ``slug`` refuses was never minted as an id. The raw spelling stands.
        return keys
    keys.add((f"{schema}.{derived}" if schema else derived).lower())
    return keys


def table_coverage(
    rows: Sequence[Mapping[str, Any]], gold_sql_by_qid: Mapping[str, str]
) -> dict[str, Any]:
    """How often every gold table was retrieved. The right schema can still drop it. Match ids from ``gold_table_ids``,
    not the gold spelling (ADR 0008)."""
    full = partial = none = unparsed = tableless = 0
    for row in rows:
        sql = gold_sql_by_qid.get(str(row.get("question_id")))
        if not sql:
            continue
        needed = gold_tables(sql)
        if needed is None:
            unparsed += 1
            continue
        if not needed:
            # A gold that reads no table is not a coverage miss. Counting constant
            # ``VALUES`` rows would cap the ceiling with nothing to retrieve. They
            # leave the denominator and stay in the report.
            tableless += 1
            continue
        # No ``retrieved_tables`` key is a caller error, not zero coverage.
        # Absent means the field was not produced; empty means nothing was
        # retrieved.
        if not artifact_has(row, "retrieved_tables"):
            raise KeyError(
                "table_coverage needs `retrieved_tables` (the table ids) on every row and this one "
                f"carries {sorted(row)}. Scoring it as zero coverage would publish a ceiling "
                "of 0.000 for a run that retrieved tables on every turn."
            )
        retrieved_tables = {str(t).lower() for t in (artifact_get(row, "retrieved_tables") or ())}
        hits = sum(1 for table in needed if gold_table_ids(table) & retrieved_tables)
        if needed and hits == len(needed):
            full += 1
        elif hits:
            partial += 1
        else:
            none += 1
    total = full + partial + none or 1
    return {
        "n": full + partial + none,
        "all_gold_tables_retrieved_tables": full / total,
        "some_retrieved_tables": partial / total,
        "none_retrieved_tables": none / total,
        "gold_sql_unparsed": unparsed,
        #: Golds that read no table. Left out of ``n`` and reported so the denominator stays visible.
        "gold_reads_no_table": tableless,
    }


def retrieval_funnel(
    rows: Sequence[Mapping[str, Any]],
    gold_sql_by_qid: Mapping[str, str],
    gold_db_by_qid: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Each stage is conditioned on the one above. Side-by-side unconditional rates cannot tell a wrong schema from a
    budget cut. An empty stage is unmeasured."""
    from governed_bi.registry.quantity import Measured

    counts = {
        "rows": 0,
        "scorable": 0,
        "schema_routed": 0,
        "tables_in_routed_schemas": 0,
        "all_gold_tables_retrieved_tables": 0,
        "answered": 0,
        # Answered with no statement, counted apart so they are not guaranteed misses in `correct`.
        "answered_without_a_statement": 0,
        "graded": 0,
        "unmeasured": 0,
        "correct": 0,
        "gold_sql_unparsed": 0,
        "gold_reads_no_table": 0,
        "gold_reads_no_table_answered": 0,
        "gold_reads_no_table_graded": 0,
        "gold_reads_no_table_correct": 0,
        "no_gold_sql": 0,
    }
    for row in rows:
        counts["rows"] += 1
        qid = str(row.get("question_id"))
        sql = gold_sql_by_qid.get(qid)
        if not sql:
            counts["no_gold_sql"] += 1
            continue
        needed = gold_tables(sql)
        if needed is None:
            counts["gold_sql_unparsed"] += 1
            continue
        if not needed:
            counts["gold_reads_no_table"] += 1
            if str(row.get("outcome") or "") == "answered":
                counts["gold_reads_no_table_answered"] += 1
            if row.get("correct") is not None:
                counts["gold_reads_no_table_graded"] += 1
                if row["correct"]:
                    counts["gold_reads_no_table_correct"] += 1
            continue
        counts["scorable"] += 1

        routed = {str(s) for s in (row.get("retrieved_tables_schemas") or ())}
        if not routed:
            routed = {str(t).split(".", 1)[0] for t in (artifact_get(row, "retrieved_tables") or ())}
        gold_db = str((gold_db_by_qid or {}).get(qid) or row.get("db_id") or "")
        if gold_db and gold_db not in routed:
            continue
        counts["schema_routed"] += 1

        if not all(str(t).split(".", 1)[0] in routed for t in needed):
            continue
        counts["tables_in_routed_schemas"] += 1

        retrieved_tables = {str(t).lower() for t in (artifact_get(row, "retrieved_tables") or ())}
        # Same id rule as ``table_coverage``. A second comparison would disagree on the same rows.
        if not all(gold_table_ids(str(t)) & retrieved_tables for t in needed):
            continue
        counts["all_gold_tables_retrieved_tables"] += 1

        if str(row.get("outcome") or "") != "answered":
            if str(row.get("outcome") or "") == Outcome.no_sql.value:
                counts["answered_without_a_statement"] += 1
            continue
        counts["answered"] += 1

        # ``correct`` is three-valued. Truthiness would count an unmeasured row as wrong.
        # ``graded`` is the grader's own coverage of the answered rows.
        if row.get("correct") is None:
            counts["unmeasured"] += 1
            continue
        counts["graded"] += 1
        if row["correct"]:
            counts["correct"] += 1

    stages = (
        ("schema_routed", "scorable"),
        ("tables_in_routed_schemas", "schema_routed"),
        ("all_gold_tables_retrieved_tables", "tables_in_routed_schemas"),
        ("answered", "all_gold_tables_retrieved_tables"),
        ("graded", "answered"),
        ("correct", "graded"),
    )
    def _stage(numerator: int, denominator: int, what: str) -> dict[str, Any]:
        """A rate and its denominator. JSON would turn an absence into the string ``"unmeasured"``."""
        # ``rounded``, not ``round()``, which turns an unmeasured quantity into 0.0.
        measured = Measured.rate(numerator, denominator, what=what).rounded(4)
        return {
            "rate": measured.value if measured.is_measured else None,
            "n": numerator,
            "of": denominator,
            "why": None if measured.is_measured else measured.why,
        }

    conditional = {
        name: _stage(counts[name], counts[given], f"{name} given {given}")
        for name, given in stages
    }
    # Graded rows only. Leaving unmeasured rows in the denominator charges the pipeline for the grader.
    end_to_end = _stage(
        counts["correct"],
        counts["scorable"] - counts["unmeasured"],
        "correct over scorable, graded questions",
    )
    # Same shape as ``end_to_end``. These golds name no table, so nothing above
    # ``answered`` can be conditioned on them. Reported beside the other rate;
    # dropping them would hide a uniform shift.
    tableless = _stage(
        counts["gold_reads_no_table_correct"],
        counts["gold_reads_no_table_graded"],
        "correct over graded questions whose gold reads no table",
    )
    return {
        "counts": counts,
        "conditional": conditional,
        "end_to_end": end_to_end,
        "gold_reads_no_table": tableless,
    }


def observed_tokens(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Tokens, not money: a hand-kept price table overstated a run. Prefer ``model_calls``; a row without it counts as
    one call and under-reports."""
    per_stage: dict[str, dict[str, int]] = {}
    calls = 0
    calls_measured = 0
    tokens_in = 0
    tokens_out = 0

    def _count(value: Any) -> int:
        """An int, or 0. Safe in this total only: the same 0 in a record field would turn an absence into a value."""
        return int(value) if isinstance(value, int) and not isinstance(value, bool) else 0

    for row in rows:
        record = row.get("record") or {}
        for entry in row.get("usage") or record.get("usage") or ():
            if not isinstance(entry, Mapping):
                continue
            got_in = _count(entry.get("input_tokens"))
            got_out = _count(entry.get("output_tokens"))
            reported_calls = _count(entry.get("model_calls"))
            got_calls = reported_calls or 1
            calls_measured += reported_calls
            calls += got_calls
            tokens_in += got_in
            tokens_out += got_out
            stage = str(entry.get("stage") or "unattributed")
            bucket = per_stage.setdefault(
                stage, {"calls": 0, "input_tokens": 0, "output_tokens": 0}
            )
            bucket["calls"] += got_calls
            bucket["input_tokens"] += got_in
            bucket["output_tokens"] += got_out

    return {
        "rows": len(rows),
        "calls": calls,
        "calls_measured": calls_measured,
        "input_tokens": tokens_in,
        "output_tokens": tokens_out,
        "by_stage": dict(sorted(per_stage.items())),
    }
