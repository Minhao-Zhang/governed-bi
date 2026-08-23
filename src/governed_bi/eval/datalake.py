"""Eval over the pooled BIRD data lake — one Postgres, 57 curated schemas, no pin.

**Two stages, cheap one first.** :func:`routing_recall` needs no model — with no extraction
model the facet queries fall back to the raw question — so it answers *is the gold schema
even a candidate* for free. The paid **live arm** (``harness.run_arm`` with ``arms.live_arm``,
driven by ``tools/run_datalake_eval.py``) runs second, because paying to discover the router
never shortlisted the right schema buys a result available for nothing.

**The reference for correctness is the gold result set, never the SQL string.** Each question
carries ``sql_rename``, the gold written against the obfuscated Postgres schemas; grading
executes both and compares fingerprints (:mod:`governed_bi.eval.grade`), so a different join
order is not a wrong answer.

:func:`observed_tokens` measures volume per stage and stops there. It does not convert to
money: a hand-maintained price table has to track a provider's list by hand, and the one that
lived here shipped a stale row that overstated a measured run nine-fold.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Collection, Iterable, Mapping, MutableMapping, Sequence
from pathlib import Path
from typing import Any

from governed_bi.register.stages import Outcome

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
    "table_coverage",
    "retrieval_funnel",
]

#: The lists ``order_sensitive_qids.json`` publishes, and the only names read for them.
QID_LIST_NAMES = ("order_sensitive", "exec_failed")


def dataset_qid_lists(dataset: str | Path) -> dict[str, set[str]]:
    """``{list_name -> question ids}`` from ``order_sensitive_qids.json``.

    A file that exists but carries none of :data:`QID_LIST_NAMES` **raises**: an empty
    exclusion set is indistinguishable from a dataset that declares no exclusions, and a
    misread key silently grades the 97 order-sensitive plus 10 degenerate golds the dataset
    says to exclude as ordinary engine misses. A *missing* file is fine — that is a real
    "nothing declared".

    The dataset's note: *"order_sensitive: gold has LIMIT-without-total-order or float
    aggregate; returns a different-but-valid result on the decoy instances ... exec_failed:
    pre-existing degenerate BIRD gold (>200k rows / 60s timeout). Exclude both from
    cross-variant EX."*
    """
    path = Path(dataset) / "order_sensitive_qids.json"
    if not path.exists():
        return {name: set() for name in QID_LIST_NAMES}
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, list):
        # Older flat form: one bare list, order-sensitive only.
        return {"order_sensitive": {str(q) for q in raw}, "exec_failed": set()}
    if not any(name in raw for name in QID_LIST_NAMES):
        raise KeyError(
            f"{path} carries none of {QID_LIST_NAMES}; its keys are {sorted(raw)}. "
            "Refusing to report an empty exclusion set as though the dataset declared one."
        )
    return {name: {str(q) for q in (raw.get(name) or ())} for name in QID_LIST_NAMES}


def dataset_leakage_qids(dataset: str | Path) -> set[str]:
    """Question ids the dataset's own split-leakage check flags, from ``leakage_test_qids.json``.

    Its note: *"Test question_ids recoverable from the train split by retrieval rather than
    induction. Same-database comparisons only."* Reads the ``union`` of its four detectors, so
    a question is suspect if any flagged it — 9 of the pooled arm's 1,351 (0.67%).

    Tagged rather than dropped (:func:`attach_quality_flags`). A missing file means no leakage
    declared; unreadable keys raise, for the reason in :func:`dataset_qid_lists`.
    """
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
    """Tag each question with what the *dataset* says is wrong with it. Returns per-flag counts.

    **Tagged, not dropped.** Which exclusions a headline is computed under is a claim about
    the number; applying them here would mean no reader can recover the other figure without
    paying for the run again. Flags on the row let one artifact answer both.

    ``order_sensitive`` is flagged even though the harness already grades those questions with
    row order preserved: the dataset's note says the gold *"returns a different-but-valid
    result on the decoy instances"*, and no comparison rule fixes a gold that is not a function
    of the query.

    ``degenerate`` is the one flag **derived here rather than published by the dataset**: a
    gold that reads no table is a frozen answer literal, not a query --
    ``SELECT "v"."c0" FROM (VALUES ('captain eli''s')) AS "v"("c0")``. The engine writes a real
    statement against the schema and can only match by reproducing the frozen shape, so these
    are won by accident. 127 of the 1 351 test questions are like this and the engine matched
    42 of them (2026-08-09 full run, corpus 30872d3). Derived rather than listed because the
    judgement is a property of the gold text and needs no curation --
    :func:`gold_tables` already computes it, and ``table_coverage`` already excludes the same
    rows under the name ``gold_reads_no_table``. Two places deciding "is this gold real" by
    two rules is the drift this repository keeps paying for; one rule, read twice.
    """
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
        # An unparseable gold is **not** degenerate: `gold_tables` returns None there, and
        # collapsing "no tables" with "could not tell" would flag a parser gap as a dataset
        # defect and quietly shrink the denominator.
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
    """Give each question the dataset's published gold digest. Returns why each one did or didn't.

    The dataset ships a digest for every question, recorded against this database
    (``gold_result_hashes_rename_decoy.jsonl``, ``dsn_key="rename_decoy"``). It is what makes
    the grader-ceiling arm measurable at all (:mod:`governed_bi.eval.oracle`), saves executing
    1,351 gold statements, and stops gold depending on database state at run time.

    Four guards, each because using the digest anyway would be wrong rather than unhelpful:

    ``dsn_key``
        a digest recorded against a different database is a different gold.
    ``error``
        the gold did not execute when the digest was taken; there is no digest to use.
    ``sql_sha256``
        the digest belongs to a *statement* and the dataset's statement can move under it — it
        disagrees with ``sha256(gold_sql)`` on 2 of the arm's 1,351 questions today, which
        would grade every prediction against the wrong target.
    ``order_sensitive``
        ``hash_lenient`` is ``normalise_result``, which always sorts, so it is an
        *order-insensitive* digest. The harness grades these questions with row order
        preserved, so comparing against it fails all 23 of them.

    Questions passing no guard keep no ``gold_fingerprint`` and the harness executes their gold
    live.
    """
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
    """Test questions, filtered to schemas the corpus actually carries.

    A question whose ``db_id`` is not in the corpus is **not** an engine failure: the corpus
    covers 57 of the database's 70 schemas, so scoring the other 13 reports a curation gap as
    a retrieval gap. Filtered against the caller's declared schema set, so the exclusion count
    is the caller's.

    ``gold_sql`` comes from ``sql_rename``, written against the obfuscated schemas this
    database has. ``sql_base`` is the un-renamed original and ``sql_sqlite`` a different
    engine; either fails to execute here, and an unexecutable gold grades every prediction
    wrong.

    ``per_schema`` caps questions per schema — without it a sample is weighted by whichever
    schema BIRD asked most about, and a per-schema effect reads as an overall one.
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
    facets, routing, retrieval, resolve and connect all run for real, no provider call.

    **Runs the compiled graph, never the nodes by hand.** The five facet nodes all write to
    one ``facets`` channel whose reducer merges by name, so assembling state with
    ``dict.update`` replaces it four times and measures 0.000 recall with every gold schema
    "never scored" — wrong in a way that looks like a finding.

    ``rank`` is the gold schema's position in ``schema_ranking``, which holds **all** scored
    schemas pre-truncation. Without it, "not a candidate" and "ranked 4th" are the same
    observation.
    """
    from governed_bi.serve.graph import compile_graph

    graph = compile_graph()
    # The saver, held once so the loop can empty it. `compile_graph()` defaults to an
    # `InMemorySaver` and nothing in it expires: `serve/graph.compile_graph`'s docstring measures
    # 101 KB after one turn and 844 KB after six, so one thread per question over a pooled run
    # retains on the order of 135 MB for state no later question reads. This driver is the one
    # most likely to be run at full scale on a laptop, because retrieval measurement is free --
    # the vector cache is warm and a stub-answer arm calls no provider.
    saver = getattr(graph, "checkpointer", None)
    rows: list[dict[str, Any]] = []
    for index, question in enumerate(questions):
        turn = session.turn(str(question["question"]), turn_index=1)
        if top_n is not None:
            turn["route_top_n"] = int(top_n)
        config = session.configurable(question=str(question["question"]))
        # One thread per question: a shared thread would carry the previous question's
        # per-turn channels into this one, which is the defect `PER_TURN_RESET` exists for.
        thread_id = f"routing-{index}-{question['question_id']}"
        config["configurable"]["thread_id"] = thread_id
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
                # The table ids themselves, not only their schemas: `table_coverage` reads
                # exactly this key, and without it reports `all_gold_tables_licensed: 0.0`
                # for every arm — a plausible number rather than an error.
                "licensed": licensed,
                # What survived `connect`'s component pick. `hit` says the router
                # shortlisted the gold schema; this says the turn could still reach it.
                "licensed_schemas": sorted({t.split(".", 1)[0] for t in licensed}),
                "reached_gold": any(t.startswith(f"{gold}.") for t in licensed),
                "path_kind": out.get("path_kind"),
                "terminal_reason": out.get("terminal_reason"),
            }
        )
        # After the row, not before: every field above comes out of the returned state, and a
        # future resumed question would read the checkpoint itself.
        #
        # Sync, and that is checked rather than assumed: `InMemorySaver.delete_thread` is a plain
        # `def` over three dicts (`langgraph/checkpoint/memory/__init__.py:505`), so no loop is
        # involved. The async spelling `eval/harness._evict` reaches for is required by the
        # *durable* saver, whose sync `delete_thread` blocks forever here; getting the two
        # backwards is how a housekeeping call becomes a hang.
        if saver is not None:
            saver.delete_thread(thread_id)
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

    Sharper than schema reachability: a turn can route to the right schema and still fail,
    because the per-type budget licenses at most ``ASSET_REGISTER[table].budget`` ranked
    tables. That splits "was the question answerable under this retrieval" from "did the model
    convert it", which one EX number cannot. Measured on the xhigh arm at 344 rows: 51.2% of
    questions had all their gold tables against 62.5% schema reachability — **not** retired
    with the EX figures, because these measure what was *licensed* and no grader touches them.

    Compared case-insensitively. Licensed ids carry the slug (ADR 0008 D1) and gold statements
    carry the engine's spelling; those agree for every identifier whose slug is its own name,
    655 of 656 tables here. The exception (``Air Carriers``) is reported uncovered rather than
    guessed at, because guessing is the fail-open shape ``structure.py`` refuses.
    """
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
            # A gold statement that reads no table is not a coverage failure. Constant-folded
            # ``VALUES`` literals (e.g. ``SELECT "v"."c0" FROM (VALUES (121.0)) AS "v"("c0")``)
            # name no table — 13 of the 114-question stratified sample — and counting them as
            # misses caps the achievable ceiling at 0.886 with no corpus change able to fix it.
            # Excluded from the denominator, as ``gold_sql_unparsed`` already is, and reported,
            # because a silently smaller denominator is the other half of the same defect.
            tableless += 1
            continue
        # A row with no ``licensed`` key is a caller error, not a coverage of zero: absent
        # means the producer does not carry the field, empty means the turn licensed nothing
        # and is a real measurement. Scoring absent as zero published a 0.000 ceiling for two
        # arms that had in fact routed well (their recalls are retired; citations.py).
        if "licensed" not in row:
            raise KeyError(
                "table_coverage needs `licensed` (the table ids) on every row and this one "
                f"carries {sorted(row)}. Scoring it as zero coverage would publish a ceiling "
                "of 0.000 for a run that licensed tables on every turn."
            )
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
        #: Gold statements that read no table (constant-folded ``VALUES`` rows). Excluded from
        #: ``n``. A run comparing itself against an older number must check this moved the
        #: denominator: on the 114-question sample it is 13.
        "gold_reads_no_table": tableless,
    }


def retrieval_funnel(
    rows: Sequence[Mapping[str, Any]],
    gold_sql_by_qid: Mapping[str, str],
    gold_db_by_qid: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Where a question is lost, as **conditional** stages over one population.

    ``summarise_routing`` and ``table_coverage`` each report over all rows and nothing joins
    them, so unconditional coverage beside unconditional recall cannot distinguish *the router
    sent us to the wrong schema* from *the router was right and the budget cut the table we
    needed* — two findings that want opposite work.

    Each stage is conditioned on the one above it, and each carries its own denominator:

    ``schema_routed``
        gold ``db_id`` among the routed schemas. Unconditional.
    ``tables_in_routed_schemas``
        given that, every gold table lives in a routed schema. A drop here is a genuinely
        cross-schema question; on BIRD-obfuscated there are none.
    ``all_gold_tables_licensed``
        given that, every gold table survived pass two, the budget and ``connect``.
    ``answered``
        given a licensed set that could support an answer. The gap from the stage above is
        generation, the only one a corpus change cannot touch.
    ``graded`` / ``correct``
        given an answer this grader could judge at all. ``graded`` is the *instrument's* stage:
        a turn with no comparable gold is unmeasured, and keeping it out of EX's denominator is
        the difference between "the system was wrong" and "we did not look".

    Rates come from :meth:`~governed_bi.register.quantity.Measured.rate`, so a stage with no
    population reports *unmeasured* rather than ``0.000`` — the ``or 1`` idiom elsewhere in
    this module is what lets a zero-row coverage read as a real ceiling of zero.

    The stages above are over ``scorable``, which excludes golds that read no table. On the
    2026-08-07 pooled run that set is 127 of 1 351 and every arm does badly on it, so
    ``gold_reads_no_table`` is returned beside them with **its own EX** — published as a
    population rather than dropped, because an exclusion that lifts every arm by the same
    ~3 points is a reporting choice and the reader is the one who has to make it.
    """
    from governed_bi.register.quantity import Measured

    counts = {
        "rows": 0,
        "scorable": 0,
        "schema_routed": 0,
        "tables_in_routed_schemas": 0,
        "all_gold_tables_licensed": 0,
        "answered": 0,
        # Counted rather than skipped, this module's own rule one stage on: before the
        # `Outcome.no_sql` split these rows arrived as `answered` with no `generated_sql`, so they
        # sat in `answered` and `graded` as guaranteed misses. They now leave the funnel at the
        # `answered` gate, which *raises* `correct/graded` -- so the count they left with is on
        # the artifact beside it. (`headline_ex` in `eval/report.py` is over the whole arm and
        # keeps scoring them wrong, so no published EX moved.)
        "answered_without_a_statement": 0,
        "graded": 0,
        "unmeasured": 0,
        "correct": 0,
        # Two different facts, counted apart. They were one counter, so a gold this metric
        # *cannot read* and a gold that genuinely reads nothing were indistinguishable —
        # ``table_coverage`` has always separated them and the funnel disagreed with it.
        "gold_sql_unparsed": 0,
        "gold_reads_no_table": 0,
        # The tableless population's own funnel tail, so its EX can be read off the artifact
        # instead of inferred from a denominator that changed. See the docstring.
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
            # Counted, not skipped. A row silently leaving the denominator is the same defect
            # as counting it wrongly, one level quieter.
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

        routed = {str(s) for s in (row.get("licensed_schemas") or ())}
        if not routed:
            routed = {str(t).split(".", 1)[0] for t in (row.get("licensed") or ())}
        gold_db = str((gold_db_by_qid or {}).get(qid) or row.get("db_id") or "")
        if gold_db and gold_db not in routed:
            continue
        counts["schema_routed"] += 1

        if not all(str(t).split(".", 1)[0] in routed for t in needed):
            continue
        counts["tables_in_routed_schemas"] += 1

        licensed = {str(t).lower() for t in (row.get("licensed") or ())}
        if not all(str(t).lower() in licensed for t in needed):
            continue
        counts["all_gold_tables_licensed"] += 1

        if str(row.get("outcome") or "") != "answered":
            if str(row.get("outcome") or "") == Outcome.no_sql.value:
                counts["answered_without_a_statement"] += 1
            continue
        counts["answered"] += 1

        # A stage for the grader itself, because ``correct`` has three values: under
        # ``if row.get("correct")`` an unmeasured row counts in ``answered`` and not in
        # ``correct``, reading exactly like a wrong answer. ``graded given answered`` is the
        # grader's own coverage; below 1.000, the EX under it is over a smaller population.
        if row.get("correct") is None:
            counts["unmeasured"] += 1
            continue
        counts["graded"] += 1
        if row["correct"]:
            counts["correct"] += 1

    stages = (
        ("schema_routed", "scorable"),
        ("tables_in_routed_schemas", "schema_routed"),
        ("all_gold_tables_licensed", "tables_in_routed_schemas"),
        ("answered", "all_gold_tables_licensed"),
        ("graded", "answered"),
        ("correct", "graded"),
    )
    def _stage(numerator: int, denominator: int, what: str) -> dict[str, Any]:
        """A rate with its own denominator beside it, and a reason when there is no rate.

        Serialised rather than returned as a :class:`Measured` because these land in a JSON
        artifact: ``json.dumps(..., default=str)`` would render an absence as the *string*
        ``"unmeasured"``, which sorts and compares like a value.
        """
        # ``.rounded`` and not ``round()``: ``tools/check_measurement_locality.py`` forbids the
        # builtin in ``src/`` because a rounding helper turns an unmeasured quantity into
        # ``0.0``. ``rounded`` carries the absence through instead of defaulting it.
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
    # ``scorable`` minus the rows the grader could not judge: they are scorable in principle
    # (a gold that reads tables) and were not scored in fact, so leaving them in the denominator
    # would charge the pipeline for the instrument's gaps.
    end_to_end = _stage(
        counts["correct"],
        counts["scorable"] - counts["unmeasured"],
        "correct over scorable, graded questions",
    )
    # The excluded population, with the same shape as ``end_to_end`` so the two are read side
    # by side. These questions *are* gradeable — an engine that queries the database and gets
    # the right value still matches the digest — but the gold names no table and no join, so
    # nothing above ``answered`` can be conditioned on them and every arm scores poorly.
    # Measured over the seven ``proxy_*`` arms in ``runs/eval/`` (corpus 30872d3, 1 351
    # questions each): 127 tableless, and their EX runs 0.29 to 0.34 against 0.60 to 0.71 on
    # the other 1 224. Reported, not dropped: the ~3-point lift from excluding them is uniform
    # across arms and changes no ranking, which makes it a choice about what a headline means
    # rather than a correction, and the reader owns that choice.
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
    """What this batch spent, in tokens. **Not in money.**

    No pricing: a hand-maintained price table has to track a provider's list by hand (the one
    that lived here overstated a measured run nine-fold), and LangSmith already reports cost
    per trace.

    ``calls`` prefers each row's own ``model_calls`` and falls back to counting the row. The
    fallback is not equivalent and the difference is the point: one row per model call holds
    for the guard and the four rewriters, and **not** for ``agent_core``, which aggregates a
    whole tool loop into one row. Counting rows there reported 1 call for a turn that made up
    to 13, which understated the repeated share of the input -- the only part prompt caching
    can remove -- by an order of magnitude. Rows written before ``model_calls`` existed still
    count as one, so a mixed batch under-reports rather than inventing.
    """
    per_stage: dict[str, dict[str, int]] = {}
    calls = 0
    calls_measured = 0
    tokens_in = 0
    tokens_out = 0

    def _count(value: Any) -> int:
        """An int, or 0 for anything else — including an unmeasured ``Measured``.

        Safe *here* and nowhere near the record: a total may be a lower bound and the row still
        says it was never measured. The same 0 written into a field would be the
        absence-becomes-a-value defect the register's ``Absence`` enum exists for.
        """
        return int(value) if isinstance(value, int) and not isinstance(value, bool) else 0

    for row in rows:
        record = row.get("record") or {}
        for entry in row.get("usage") or record.get("usage") or ():
            if not isinstance(entry, Mapping):
                continue
            got_in = _count(entry.get("input_tokens"))
            got_out = _count(entry.get("output_tokens"))
            # The row's own count when it has one; otherwise the row is one call. `agent_core`
            # aggregates a whole tool loop, so counting rows there reported 1 for a turn that
            # made up to 13.
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
        # How much of ``calls`` came from a row that actually counted, rather than from the
        # one-row-is-one-call fallback. Below ``calls`` means the batch predates ``model_calls``
        # and its agent loops are under-counted -- stated, because a total that is partly a
        # fallback and says so is a different fact from one that is not.
        "calls_measured": calls_measured,
        "input_tokens": tokens_in,
        "output_tokens": tokens_out,
        # Per stage: ``llm_utility_model`` is a comparability knob justified by cost and
        # latency, and "which stage spent it" is the only way to argue either.
        "by_stage": dict(sorted(per_stage.items())),
    }
