"""Eval over the pooled BIRD data lake — one Postgres, 57 curated schemas, no pin.

**Two stages, and the cheap one runs first on purpose.**

``routing_recall`` needs no model at all: with no extraction model the facet queries fall
back to the raw question, so routing is BM25 over the index and costs nothing. It answers
the question that currently dominates every other number — *is the gold schema even a
candidate* — over all 1 351 test questions for free.

The **live arm** is the one that costs money — ``harness.run_arm`` with ``arms.live_arm``, driven
by ``tools/run_datalake_eval.py``. Running it before the free measurement would be paying to
discover that the router never shortlisted the right schema, which is a result you can have for
nothing.

**The reference for correctness is the gold result set, never the SQL string.** Each
question carries ``sql_rename``, the gold statement written against the obfuscated
Postgres schemas, and grading executes both and compares fingerprints
(:mod:`governed_bi.eval.grade`). Comparing SQL text would mark a correct answer wrong for
choosing a different join order.

Token volume is **measured**: :func:`observed_tokens` sums the usage rows the record already
carries, per stage, so a decision to scale a run up is made against observed volume on a small
batch. It stops there and does not convert to money -- ``measure/price.py`` did and is deleted,
because a hand-maintained price table has to track a provider's list by hand and the one stale
row it picked up overstated a measured run nine-fold.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Collection, Iterable, Mapping, MutableMapping, Sequence
from pathlib import Path
from typing import Any

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

    **Two callers read this file and both read a key it has never had.**
    ``tools/run_datalake_eval.py`` and ``tools/regrade.py`` each did
    ``raw.get("question_ids") or []``, while the file's keys are ``note``,
    ``order_sensitive``, ``exec_failed`` and ``counts``. So both returned the empty set on
    every run, and the 97 order-sensitive plus 10 degenerate golds the dataset explicitly
    says to exclude were graded as ordinary engine misses instead.

    The ``or []`` is what made it survive: an empty exclusion set is indistinguishable from
    a dataset that declares no exclusions. So a file that exists and carries none of the
    expected names **raises** here rather than reporting nothing to exclude. Absence of the
    file is still fine — a dataset need not ship the list — because that is a real
    "nothing declared", not a misread one.

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
    induction. Same-database comparisons only."* The file publishes four detectors
    (``exact_question_text``, ``exact_gold_sql``, ``template_collision``, ``fuzzy_jaccard_080``)
    and their ``union``, which is what this reads — a question is suspect if any detector flagged
    it.

    **Nothing read this file, and 9 of the pooled arm's 1,351 questions are in the union.** They
    were scored like any other question. That is 0.67%, which changes no conclusion on its own;
    what it changes is whether the number can be defended, because the dataset shipped the
    warning and the harness ignored it. Tagged rather than dropped — see
    :func:`attach_quality_flags` for why that distinction is deliberate.

    A missing file means the dataset declares no leakage, which is different from a file whose
    keys are unreadable: that raises, for the reason spelled out in :func:`dataset_qid_lists`.
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

    **Tagged, not dropped, and that is the whole design.** Every one of these flags is an
    argument for excluding a question from EX, and the dataset says so outright for two of them
    (*"Exclude both from cross-variant EX"*). But which exclusions a headline number is computed
    under is a claim about the number, and silently applying them here would mean no reader could
    recover the other figure without paying for the run again. Flags on the row let one artifact
    answer both, and make the exclusion visible in the place it is applied.

    ``order_sensitive`` is flagged even though the harness already grades those questions with row
    order preserved. Order-sensitivity is not the whole problem with them: the dataset's note says
    the gold *"returns a different-but-valid result on the decoy instances"*, and no comparison
    rule fixes a gold that is not a function of the query. Two of the four golds whose digest
    disagrees with the dataset's own published hash are in this list.
    """
    buckets = (
        ("leakage", {str(q) for q in leakage}),
        ("order_sensitive", {str(q) for q in order_sensitive}),
        ("exec_failed", {str(q) for q in exec_failed}),
    )
    counts = {name: 0 for name, _ in buckets}
    for question in questions:
        qid = str(question.get("question_id"))
        flags = [name for name, ids in buckets if qid in ids]
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

    **The dataset ships a digest for every question and nothing read it.** For the pooled arm that
    is 1,351 of 1,351 covered, recorded against this very database
    (``gold_result_hashes_rename_decoy.jsonl``, ``dsn_key="rename_decoy"``). Reading it buys three
    things, and only the first is about speed:

    * The **grader-ceiling arm starts measuring.** Without an independent gold, every oracle row is
      ``correct=None`` (:mod:`governed_bi.eval.oracle`) — the one arm whose entire purpose is to
      show the grader is not the bottleneck could not show anything.
    * The serve arm stops executing 1,351 gold statements it already knows the answer to.
    * Gold stops depending on database state *at run time*, which is what makes two runs
      comparable rather than merely similar.

    Four guards, each because using the digest anyway would be wrong rather than merely unhelpful:

    ``dsn_key``
        a digest recorded against a different database is a different gold.
    ``error``
        the row records that the gold did not execute when the digest was taken; there is no
        digest to use.
    ``sql_sha256``
        the digest belongs to a *statement*, and the dataset's statement can move under it. It
        disagrees with ``sha256(gold_sql)`` on 2 of the arm's 1,351 questions today, and those two
        would silently grade every prediction against the wrong target.
    ``order_sensitive``
        **the load-bearing one.** ``hash_lenient`` is ``normalise_result``, which always sorts, so
        it is an *order-insensitive* digest. The harness grades these questions with row order
        preserved, so comparing an order-preserving prediction digest against it would fail every
        one of them. 23 of the arm's questions are on that list, and wiring this file without this
        guard would have converted a fix into a 23-question regression.

    Questions that pass no guard keep no ``gold_fingerprint``, and the harness's existing fallback
    executes their gold live — which is what every question did before this.
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
                # The table ids themselves, **not only their schemas.** This key was absent
                # and `table_coverage` reads exactly it, so feeding these rows to the
                # function this module documents as "the EX ceiling" reported
                # ``all_gold_tables_licensed: 0.0`` for every arm -- a plausible-looking
                # number rather than an error, over rows whose `reached_gold` in the same
                # dict proved the tables were there. Two functions in one module, one
                # producing rows the other cannot read, failing as a zero: the shape
                # `register/assets.py` opens by naming.
                "licensed": licensed,
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


# **``run_live`` was here and is gone** (audit §10). It wrapped three lines --
# ``run_arm(questions, live_arm(session), order_sensitive_qids=..., session=session)`` -- and had
# zero callers: ``tools/run_datalake_eval.py``, the actual paid-arm driver, calls ``run_arm`` with
# ``live_arm`` directly. A wrapper with no caller beside the thing it wraps is a second name for
# one operation, and this module's docstring described the arm through it, so a reader looking for
# where the money is spent found a function nothing runs.


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
    *schema* reachability of 62.5%.

    That splits the problem in two, which one EX number cannot: whether a question was
    *answerable at all* under this retrieval, and whether the model converted it when it was.

    The EX figure this docstring used to quote beside those two is retired
    (``register/citations.RETIRED_CLAIMS``): it was graded by a comparator that read Postgres
    ``numeric`` cells as strings, so it is an underestimate of unknown size. These two numbers
    are unaffected — they are measurements of what was *licensed*, and no grader touches them.

    Compared case-insensitively. Licensed ids carry the slug (ADR 0008 D1) and a gold
    statement carries the engine's spelling; those agree for every identifier whose slug is
    its own name, which is 655 of 656 tables here. The exception (``Air Carriers``) is
    reported as uncovered rather than silently matched, because a comparison that guessed
    would be the fail-open shape ``structure.py`` exists to refuse.
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
            # **A gold statement that reads no table is not a coverage failure.** 13 of the 114
            # questions in the stratified sample are constant-folded ``VALUES`` literals -- the
            # dataset pre-computed the answer, e.g.
            # ``SELECT "v"."c0" FROM (VALUES (121.0)) AS "v"("c0")`` -- so they name no table at
            # all. The ``needed and hits == len(needed)`` test made them falsy, ``elif hits``
            # falsy, and they landed in ``none``: an unconditional miss no corpus change could
            # ever fix, holding the achievable ceiling at 101/114 = 0.886 and deflating every
            # coverage figure by a fixed 11.4% of the sample.
            #
            # Excluded from the denominator rather than counted as covered, which is how
            # ``gold_sql_unparsed`` already treats a statement this metric cannot read. Reported,
            # because a silently smaller denominator is the other half of the same defect.
            tableless += 1
            continue
        # **A row with no ``licensed`` key at all is a caller error, not a coverage of zero.**
        # ``routing_recall`` published only ``licensed_schemas``, so this scored every one of
        # its rows as "no gold table licensed" and reported the ceiling as 0.000 for two arms
        # whose routing recall was 0.851 and 0.877. Absent and empty are different facts:
        # empty means the turn licensed nothing, which is a measurement this counts, and
        # absent means the rows came from a producer that does not carry the field.
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

    **The measurement this repository could not make.** ``summarise_routing`` reports schema
    ``recall@k`` over all rows and ``table_coverage`` reports gold-table coverage over all rows,
    and nothing joins them — so "coverage 0.70 against recall@3 0.85" cannot distinguish *the
    router sent us to the wrong schema* from *the router was right and the table budget cut the
    table we needed*. Those two want opposite work, and a whole day of corpus rewriting was
    spent without knowing which one was binding. Every field needed was already on the rows
    (``routing_recall`` publishes ``licensed`` and ``licensed_schemas``; ``harness.project_turn``
    publishes ``outcome`` and ``correct``); only the join was missing.

    Each stage is conditioned on the one above it, and each carries its own denominator:

    ``schema_routed``
        gold ``db_id`` among the routed schemas. Unconditional.
    ``tables_in_routed_schemas``
        given that, every gold table lives in a schema that was routed. A drop here is a
        genuinely cross-schema question — on BIRD-obfuscated there are none, which is worth
        knowing before building for them.
    ``all_gold_tables_licensed``
        given that, every gold table survived pass two, the budget and ``connect``. This is the
        stage the earlier work was actually fighting, and it was invisible.
    ``answered``
        given a licensed set that could support an answer. The gap from the stage above is
        generation, and it is the only one a corpus change cannot touch.
    ``graded`` / ``correct``
        given an answer this grader could judge at all. ``graded`` is the *instrument's* stage,
        not the system's: a turn with no comparable gold is unmeasured, and keeping it out of
        EX's denominator is the difference between "the system was wrong" and "we did not look".

    Rates come from :meth:`~governed_bi.register.quantity.Measured.rate`, so a stage with no
    population reports *unmeasured* rather than ``0.000`` — the ``or 1`` idiom elsewhere in this
    module is what let a zero-row coverage read as a real ceiling of zero.
    """
    from governed_bi.register.quantity import Measured

    counts = {
        "rows": 0,
        "scorable": 0,
        "schema_routed": 0,
        "tables_in_routed_schemas": 0,
        "all_gold_tables_licensed": 0,
        "answered": 0,
        "graded": 0,
        "unmeasured": 0,
        "correct": 0,
        "gold_reads_no_table": 0,
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
        if needed is None or not needed:
            counts["gold_reads_no_table"] += 1
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
            continue
        counts["answered"] += 1

        # **A stage for the grader itself**, because ``correct`` has three values and this funnel
        # used ``if row.get("correct")`` — so an *unmeasured* row (no gold to compare against)
        # counted in ``answered`` and not in ``correct``, and read exactly like a wrong answer.
        # ``graded given answered`` is the grader's own coverage; when it is below 1.000 the EX
        # below it is over a smaller population, and now says so.
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
        # builtin in ``src/`` because v1's rounding helpers turned an unmeasured quantity into
        # ``0.0`` on the way to a report, and that is exactly the failure this funnel exists to
        # stop making. ``rounded`` carries the absence through instead of defaulting it.
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
    return {
        "counts": counts,
        "conditional": conditional,
        "end_to_end": end_to_end,
    }


def observed_tokens(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """What this batch spent, in tokens. **Not in money.**

    This was ``observed_spend`` and priced the rows through ``measure/price.py``, which is
    deleted along with the ``cost_est_usd`` record field. Two reasons, and the second decided it:
    nothing on the serve path ever priced a turn — ``estimate_run_cost``'s only caller was this
    function — so every *served* turn recorded a null cost while the eval got a real one; and a
    price table maintained by hand has to track a provider's list by hand, which that module's own
    docstring opened by naming — *"a stale price entry overstated a measured run nine-fold."*

    Tokens are what this repository can observe. What they cost is the provider's number, and
    LangSmith already reports it per trace without a price list living here.

    ``calls`` counts usage **rows**, which is one per model call now that every calling stage
    writes its own. It went from 1 to 7 per turn when the guard and the rewriters started
    billing, so a batch compared against an older one shows a jump — that is the older batch
    under-reporting six calls, not this one inflating.
    """
    per_stage: dict[str, dict[str, int]] = {}
    calls = 0
    tokens_in = 0
    tokens_out = 0

    def _count(value: Any) -> int:
        """An int, or 0 for anything else — including a ``Measured`` in the unmeasured state.

        Safe *here* and nowhere near the record: a total is allowed to be a lower bound, and the
        row it came from still says it was never measured. Writing the same 0 into a field would
        be the absence-becomes-a-value defect the register spends its ``Absence`` enum on.
        """
        return int(value) if isinstance(value, int) and not isinstance(value, bool) else 0

    for row in rows:
        record = row.get("record") or {}
        for entry in row.get("usage") or record.get("usage") or ():
            if not isinstance(entry, Mapping):
                continue
            got_in = _count(entry.get("input_tokens"))
            got_out = _count(entry.get("output_tokens"))
            calls += 1
            tokens_in += got_in
            tokens_out += got_out
            stage = str(entry.get("stage") or "unattributed")
            bucket = per_stage.setdefault(
                stage, {"calls": 0, "input_tokens": 0, "output_tokens": 0}
            )
            bucket["calls"] += 1
            bucket["input_tokens"] += got_in
            bucket["output_tokens"] += got_out

    return {
        "rows": len(rows),
        "calls": calls,
        "input_tokens": tokens_in,
        "output_tokens": tokens_out,
        # Per stage, because that is the question the agent/utility split raises and one total
        # cannot answer: ``llm_utility_model`` is a comparability knob justified by cost and
        # latency, and "which stage spent it" is the only way to argue either.
        "by_stage": dict(sorted(per_stage.items())),
    }
