"""Offline analysis over scored eval rows — no model, no database, no API cost.

Every function here reads ``generations.<arm>.jsonl`` (plus the BIRD split file
for gold SQL) and computes something the run itself does not: where a wrong
answer went wrong, and whether an arm-to-arm difference is real.

Three questions drive the module:

**Where does a right-schema failure actually fail?** ``EX = routing_recall x
cond_ex_given_routing`` locates the loss at routing or generation, but not
further. :func:`table_selection_report` splits the generation half again, into
questions that used the wrong *tables* and questions that used the right tables
and still produced the wrong SQL — and, using the ``retrieved_tables`` /
``licensed_tables`` provenance, whether a wrong table was **never offered** by
retrieval or **offered and ignored**. Those two need opposite fixes.

**Is this delta real?** Serve decoding is not pinned, so two runs of the *same*
arm disagree on a nontrivial share of questions. :func:`mcnemar` is the paired
test for that; comparing point estimates across unpaired runs is not.

**What can no generator ever win?** A gold answer that is a literal
``VALUES (...)`` constant is unmatchable, and order-sensitive golds are
ungradeable for the same EX reason the summary uses. :func:`gradeable_report`
reports EX with both removed from the denominator — the same rule as
``run_datalake``'s ``ex_gradeable`` — which is where real effects are visible.

CLI::

    uv run python -m governed_bi.eval.analysis runs/datalake/<ts> \\
      --bird-dir ../BIRD-Data-Obfuscation
"""

from __future__ import annotations

import argparse
import json
import math
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

import sqlglot
from sqlglot import exp

from .arms import ARM_ORDER, skipped_rungs, step_mechanisms
from .leakage import is_gradeable_eval_row
from .power import holm_adjust

_FROZEN_GOLD_RE = re.compile(r"\bVALUES\s*\(", re.IGNORECASE)


# --------------------------------------------------------------------------- #
# Row / gold loading
# --------------------------------------------------------------------------- #


def load_rows(path: Path | str) -> list[dict[str, Any]]:
    """Read one ``generations.<arm>.jsonl``, skipping blank/truncated lines."""
    rows: list[dict[str, Any]] = []
    with Path(path).open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue  # truncated tail of a killed run
    return rows


def load_arm_rows(run_dir: Path | str) -> dict[str, list[dict[str, Any]]]:
    """Every ``generations.<arm>.jsonl`` under ``run_dir``, keyed by arm name."""
    run_dir = Path(run_dir)
    return {
        p.name[len("generations.") : -len(".jsonl")]: load_rows(p)
        for p in sorted(run_dir.glob("generations.*.jsonl"))
    }


def load_gold_sql(bird_dir: Path | str, *, split: str, field: str = "sql_rename") -> dict[str, str]:
    """``{question_id: gold SQL}`` for one split."""
    path = Path(bird_dir) / "eval_dataset" / f"{split}_final.jsonl"
    out: dict[str, str] = {}
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                row = json.loads(line)
                out[str(row["question_id"])] = row.get(field) or ""
    return out


# --------------------------------------------------------------------------- #
# Corpus census — the eval ladder's independent variable
# --------------------------------------------------------------------------- #


def corpus_census(corpus: Any) -> dict[str, Any]:
    """Count what an arm's corpus actually contains.

    The rungs of the eval ladder differ **only** by the corpus fed to one serve
    path, so the corpus *is* the independent variable — and it was the one thing
    a run never recorded. Without this, an arm that authored nothing and an arm
    whose content was authored but never delivered both read as "no effect", and
    the two demand opposite work.

    Notes are broken out by activation and scope kind because a globally-scoped
    note behaves nothing like a schema- or table-scoped one at injection time.
    """
    from ..corpus.schemas import (
        FewShotAsset,
        JoinAsset,
        MetricAsset,
        NegativeExampleAsset,
        NoteAsset,
        TableAsset,
        TermAsset,
    )

    tables = [a for a in corpus.assets if isinstance(a, TableAsset)]
    notes = [a for a in corpus.assets if isinstance(a, NoteAsset)]
    descriptions = [(t.description or "").strip() for t in tables]
    described = [d for d in descriptions if d]

    n_columns = 0
    n_col_described = 0
    n_col_suspect = 0
    for t in tables:
        for c in t.columns:
            n_columns += 1
            if (getattr(c, "description", None) or "").strip():
                n_col_described += 1
            reliability = getattr(c, "reliability", None)
            if getattr(reliability, "status", None) is not None and str(
                getattr(reliability.status, "value", reliability.status)
            ) == "suspect":
                n_col_suspect += 1

    def _scope_kind(note: Any) -> str:
        scope = list(getattr(note, "scope", ()) or ())
        if not scope:
            return "global"
        if any(str(s).startswith("schema:") for s in scope):
            return "schema"
        if any(str(s).startswith("db:") for s in scope):
            return "db"
        return "asset"

    by_activation: dict[str, int] = {}
    by_scope: dict[str, int] = {}
    for note in notes:
        act = getattr(note.activation, "value", note.activation)
        by_activation[str(act)] = by_activation.get(str(act), 0) + 1
        kind = _scope_kind(note)
        by_scope[kind] = by_scope.get(kind, 0) + 1

    return {
        "n_schemas": len({t.schema for t in tables}),
        "n_tables": len(tables),
        "n_tables_described": len(described),
        "mean_description_chars": (
            sum(len(d) for d in described) / len(described) if described else 0.0
        ),
        "n_columns": n_columns,
        "n_columns_described": n_col_described,
        "n_columns_suspect": n_col_suspect,
        "n_joins": sum(1 for a in corpus.assets if isinstance(a, JoinAsset)),
        "n_metrics": sum(1 for a in corpus.assets if isinstance(a, MetricAsset)),
        "n_terms": sum(1 for a in corpus.assets if isinstance(a, TermAsset)),
        "n_few_shots": sum(1 for a in corpus.assets if isinstance(a, FewShotAsset)),
        "n_negative_examples": sum(
            1 for a in corpus.assets if isinstance(a, NegativeExampleAsset)
        ),
        "n_notes": len(notes),
        "notes_by_activation": dict(sorted(by_activation.items())),
        "notes_by_scope": dict(sorted(by_scope.items())),
    }


def census_delta(lower: dict[str, Any], higher: dict[str, Any]) -> dict[str, Any]:
    """What one rung added over the rung below it, on the numeric counts.

    This is the number to read before any arm-to-arm EX delta: an arm that added
    nothing cannot be evidence that its layer does not work.
    """
    return {
        k: round(higher[k] - lower[k], 3)
        for k, v in higher.items()
        if isinstance(v, (int, float)) and isinstance(lower.get(k), (int, float))
        and higher[k] != lower[k]
    }


# --------------------------------------------------------------------------- #
# SQL table extraction
# --------------------------------------------------------------------------- #


def sql_tables(sql: str | None, *, dialect: str = "postgres") -> frozenset[str]:
    """Physical table names referenced by ``sql``, lowercased.

    Parsed from the AST, not matched by regex: a regex over quoted identifiers
    also captures column names and ``AS T1`` aliases, which inflates any
    table-overlap statistic built on it.

    CTE names are **excluded**. ``WITH recent AS (...) SELECT ... FROM recent``
    references no table called ``recent``, and counting one would score a
    correct query as touching a table the gold never mentions.

    Known limitation: exclusion is by name, not by scope, so a CTE that *shadows*
    a real table (``WITH orders AS (SELECT * FROM orders WHERE ...)``) also drops
    the genuine reference and yields an empty set. Scope-accurate resolution needs
    sqlglot's optimizer, which requires a schema to bind against. Such a row lands
    in the report's ``n_gold_unparseable`` bucket rather than being silently
    miscounted, so the shortfall is visible rather than absorbed.

    Schema qualifiers are dropped: the comparison is scoped to rows whose schema
    already routed correctly, so the discriminating part is the table name.
    Unparseable SQL yields an empty set (the caller reports those separately).
    """
    if not sql or not sql.strip():
        return frozenset()
    try:
        tree = sqlglot.parse_one(sql, read=dialect)
    except sqlglot.errors.SqlglotError:
        return frozenset()
    if tree is None:
        return frozenset()
    cte_names = {
        cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE) if cte.alias_or_name
    }
    names = {
        t.name.lower() for t in tree.find_all(exp.Table) if t.name
    }
    return frozenset(names - cte_names)


# --------------------------------------------------------------------------- #
# Table-selection report
# --------------------------------------------------------------------------- #


def _bare(name: str) -> str:
    """Last dot-segment of a possibly schema-qualified name, lowercased."""
    return str(name).rsplit(".", 1)[-1].lower()


def _offered_bare(offered: Iterable[Any], db_id: Any) -> set[str]:
    """Bare names of tables offered to the model **from the question's own schema**.

    Provenance is schema-qualified (``address.zip_data``) while :func:`sql_tables`
    yields bare names, so comparison happens on the bare form. Dropping the
    qualifier alone would be unsafe: with ``schema_route_llm_pick`` off the routed
    set can span several schemas, and ``other_schema.standort`` would then be
    credited as an offer of the gold ``standort``, turning a retrieval miss into a
    selection miss. Unqualified entries are kept — older runs recorded bare names.
    """
    want = str(db_id).lower()
    out: set[str] = set()
    for t in offered:
        text = str(t)
        if "." in text and text.rsplit(".", 1)[0].lower() != want:
            continue
        out.add(_bare(text))
    return out


@dataclass(frozen=True)
class TableSelectionReport:
    """Where right-schema failures lose the answer."""

    arm: str
    n_rows: int
    n_right_schema_wrong_sql: int
    # Rows excluded from the table comparison, kept apart so a broken input (wrong
    # split, wrong gold field) reads as "no gold to compare" and not as a clean
    # zero-mismatch bill of health.
    n_no_sql: int  # refused or crashed: no SQL, so no table selection to score
    n_gold_missing: int
    n_gold_frozen: int
    n_gold_unparseable: int
    n_compared: int
    n_table_mismatch: int
    # ``None``, not 0.0, when nothing was comparable: a zero mismatch rate over zero
    # comparisons reads as "the tables were fine" — the opposite of what happened.
    table_mismatch_rate: float | None
    mean_table_recall: float | None
    mean_table_precision: float | None
    # Of the mismatches, was a MISSING gold table ever offered to the model?
    n_with_provenance: int
    n_retrieval_miss: int  # gold table absent from retrieved_tables -> fix retrieval
    n_selection_miss: int  # gold table retrieved but unused -> fix generation
    n_extra_tables_only: int  # every gold table used, plus spurious ones (over-join)
    retrieval_miss_rate: float | None
    # Correct-answer rate among right-schema rows that DID use the gold tables:
    # the conversion rate a perfect table selector would inherit.
    p_correct_given_right_tables: float | None
    top_missed_tables: list[tuple[str, int]]


def table_selection_report(
    rows: Iterable[dict[str, Any]],
    gold_sql: dict[str, str],
    *,
    arm: str = "",
    dialect: str = "postgres",
    top_n: int = 15,
) -> TableSelectionReport:
    """Split right-schema failures into wrong-table vs right-table-wrong-SQL.

    Only rows that routed correctly are considered: a mis-routed question cannot
    have used the gold tables, so including them would measure routing again. For
    the same reason a failure that produced no SQL is counted in ``n_no_sql`` and
    excluded from the comparison — a refusal is not a table-selection mistake.
    """
    rows = list(rows)
    considered = [r for r in rows if r.get("routed_hit")]
    failures = [r for r in considered if not r.get("correct")]

    n_no_sql = n_missing = n_frozen = n_unparseable = 0
    n_mismatch = n_extra_only = 0
    recalls: list[float] = []
    precisions: list[float] = []
    retrieval_miss = selection_miss = with_prov = 0
    missed: dict[str, int] = {}

    for r in failures:
        # A refused or crashed row has no SQL, so it made no table selection at all.
        # Left in, its empty table set reads as "every gold table absent" and the row
        # is charged to retrieval or selection — manufacturing a table-selection
        # failure out of a refusal, in the one bucket this report exists to size.
        # Checked before the gold buckets so the exclusions stay a partition.
        pred_sql = r.get("generated_sql")
        if not pred_sql or not str(pred_sql).strip():
            n_no_sql += 1
            continue
        qid = str(r.get("question_id"))
        # Three distinct reasons a row cannot be compared, kept apart: an absent
        # gold (wrong split / wrong field), a frozen VALUES gold no generator can
        # match, and gold that genuinely fails to parse. Collapsing them hides a
        # broken input behind a zero mismatch count.
        if qid not in gold_sql:
            n_missing += 1
            continue
        raw_gold = gold_sql[qid]
        if _FROZEN_GOLD_RE.search(raw_gold or ""):
            n_frozen += 1
            continue
        gold = sql_tables(raw_gold, dialect=dialect)
        if not gold:
            n_unparseable += 1
            continue
        pred = sql_tables(pred_sql, dialect=dialect)
        recalls.append(len(gold & pred) / len(gold))
        if pred:
            precisions.append(len(gold & pred) / len(pred))
        if gold == pred:
            continue
        n_mismatch += 1
        absent = gold - pred
        if not absent:
            # Every gold table was used, plus extras. That is an over-join, not a
            # missing-table failure, and must not be scored as one — it would
            # inflate exactly the bucket this report exists to size.
            n_extra_only += 1
            continue
        for t in absent:
            missed[t] = missed.get(t, 0) + 1
        offered = r.get("retrieved_tables")
        if offered is None:
            continue
        with_prov += 1
        offered_bare = _offered_bare(offered, r.get("db_id"))
        # A gold table the model never saw is a retrieval failure; one it saw and
        # did not use is a selection failure. Mixed rows count as retrieval, the
        # upstream cause.
        if absent - offered_bare:
            retrieval_miss += 1
        else:
            selection_miss += 1

    # Conversion rate for a hypothetical perfect table selector: among right-schema
    # rows that already covered the gold tables, how many were actually correct?
    # A no-SQL row drops out on its own here — an empty table set cannot be a
    # superset of a non-empty gold — so this denominator needs no refusal filter.
    covered = [
        r
        for r in considered
        if (g := sql_tables(gold_sql.get(str(r.get("question_id")), ""), dialect=dialect))
        and g <= sql_tables(r.get("generated_sql"), dialect=dialect)
    ]
    p_correct = (
        sum(1 for r in covered if r.get("correct")) / len(covered) if covered else None
    )

    n_fail = len(failures)
    n_compared = n_fail - n_no_sql - n_missing - n_frozen - n_unparseable
    return TableSelectionReport(
        arm=arm or (str(rows[0].get("arm")) if rows else ""),
        n_rows=len(rows),
        n_right_schema_wrong_sql=n_fail,
        n_no_sql=n_no_sql,
        n_gold_missing=n_missing,
        n_gold_frozen=n_frozen,
        n_gold_unparseable=n_unparseable,
        n_compared=n_compared,
        n_table_mismatch=n_mismatch,
        # Rate over rows actually comparable, not over every failure: dividing by
        # n_fail would shrink the rate purely because some golds are unmatchable.
        table_mismatch_rate=(n_mismatch / n_compared) if n_compared else None,
        mean_table_recall=(sum(recalls) / len(recalls)) if recalls else None,
        mean_table_precision=(sum(precisions) / len(precisions)) if precisions else None,
        n_with_provenance=with_prov,
        n_retrieval_miss=retrieval_miss,
        n_selection_miss=selection_miss,
        n_extra_tables_only=n_extra_only,
        retrieval_miss_rate=(retrieval_miss / with_prov) if with_prov else None,
        p_correct_given_right_tables=p_correct,
        top_missed_tables=sorted(missed.items(), key=lambda kv: (-kv[1], kv[0]))[:top_n],
    )


# --------------------------------------------------------------------------- #
# Paired significance
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class McNemarResult:
    n_paired: int
    a_only: int  # correct in A, wrong in B
    b_only: int  # correct in B, wrong in A
    n_discordant: int
    p_value: float
    net: int  # b_only - a_only, i.e. B's advantage in questions


def _outcome_by_key(
    rows: Iterable[dict[str, Any]], *, key: str, outcome: str, side: str
) -> dict[str, bool]:
    """``{question_id: outcome}``, rejecting duplicate ids.

    A duplicated question id means the generations file is corrupt (a resume that
    appended a question twice). Silently keeping the last one would produce a
    p-value from data that is wrong in an unknown direction, so this is fatal
    rather than a warning.
    """
    out: dict[str, bool] = {}
    dupes: set[str] = set()
    for r in rows:
        if key not in r:
            continue
        k = str(r[key])
        if k in out:
            dupes.add(k)
        out[k] = bool(r.get(outcome))
    if dupes:
        sample = sorted(dupes)[:5]
        raise ValueError(
            f"side {side} has {len(dupes)} duplicate {key}(s) (e.g. {sample}); the "
            "generations file is corrupt and any paired test over it is meaningless"
        )
    return out


def mcnemar(
    rows_a: Iterable[dict[str, Any]],
    rows_b: Iterable[dict[str, Any]],
    *,
    key: str = "question_id",
    outcome: str = "correct",
) -> McNemarResult:
    """Exact two-sided McNemar test over questions scored by BOTH arms.

    Uses the exact binomial rather than the chi-square approximation: discordant
    counts here are often small enough that the approximation misleads, and the
    exact form costs nothing at these sizes.

    Only the discordant pairs carry information — questions both arms got right
    (or both wrong) say nothing about which is better, which is exactly why an
    unpaired difference of point estimates is not a substitute.
    """
    a = _outcome_by_key(rows_a, key=key, outcome=outcome, side="A")
    b = _outcome_by_key(rows_b, key=key, outcome=outcome, side="B")
    shared = a.keys() & b.keys()
    a_only = sum(1 for k in shared if a[k] and not b[k])
    b_only = sum(1 for k in shared if b[k] and not a[k])
    n = a_only + b_only
    if n == 0:
        p = 1.0
    else:
        lo = min(a_only, b_only)
        tail = sum(math.comb(n, i) for i in range(lo + 1)) / (2**n)
        p = min(1.0, 2 * tail)
    return McNemarResult(
        n_paired=len(shared),
        a_only=a_only,
        b_only=b_only,
        n_discordant=n,
        p_value=p,
        net=b_only - a_only,
    )


# --------------------------------------------------------------------------- #
# Gradeable EX / rank distribution
# --------------------------------------------------------------------------- #


def gradeable_report(
    rows: Iterable[dict[str, Any]], gold_sql: dict[str, str] | None = None
) -> dict[str, Any]:
    """EX with frozen and order-sensitive golds removed from the denominator.

    Same exclusion rule and empty-denominator semantics as
    ``run_datalake._summarise_rows`` / ``ex_gradeable``, via
    :func:`governed_bi.eval.leakage.is_gradeable_eval_row`. Uses per-row stamps
    when present; falls back to detecting frozen gold from ``gold_sql`` so older
    runs stay analysable.
    """
    rows = list(rows)
    n = len(rows)
    gradeable = [r for r in rows if is_gradeable_eval_row(r, gold_sql=gold_sql)]
    n_g = len(gradeable)
    n_frozen = sum(
        1
        for r in rows
        if (
            bool(r["gold_frozen"])
            if r.get("gold_frozen") is not None
            else bool(
                gold_sql is not None
                and _FROZEN_GOLD_RE.search(
                    gold_sql.get(str(r.get("question_id")), "") or ""
                )
            )
        )
    )
    n_order = sum(1 for r in rows if r.get("gold_order_sensitive"))
    n_correct = sum(1 for r in rows if r.get("correct"))
    return {
        "n": n,
        "n_frozen_gold": n_frozen,
        "n_order_sensitive_gold": n_order,
        "n_gradeable": n_g,
        # ``None``, not 0.0, on an empty denominator — matching
        # ``run_datalake._summarise_rows``. These three names appear in both
        # ``summary.json`` and ``analysis.json``, so a difference in what they mean
        # at the edges makes two files disagree about the same run.
        "ex_lenient": (n_correct / n) if n else None,
        "ex_gradeable": (
            sum(1 for r in gradeable if r.get("correct")) / n_g if n_g else None
        ),
        "decoy_touch_rate": (
            sum(1 for r in rows if r.get("decoy_touch"))
            / sum(1 for r in rows if r.get("generated_sql"))
            if any(r.get("generated_sql") for r in rows)
            else None
        ),
    }


def rank_report(rows: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """EX bucketed by the true schema's rank in the embedding shortlist.

    Separates the two routing failure modes: ``miss`` means retrieval ran and never
    surfaced the schema (widen the shortlist or fix the embedder), while a wrong
    answer at rank 1 means the picker overrode a correct top hit.

    A third bucket, ``no_shortlist``, holds turns that recorded no shortlist at all —
    an oracle rung, or a turn that ended before retrieval. It is separate because
    ``gold_schema_rank`` is ``None`` for both, and folding them together read as a
    100% retrieval failure on runs where retrieval was never asked to do anything:
    the whole-split oracle ceiling reported ``{"miss": {"n": 2030, "ex_lenient":
    1.0}}``, a bucket that means "fix the embedder" sitting at a perfect score.
    """
    buckets: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        rank = r.get("gold_schema_rank")
        if rank is not None:
            key = str(rank)
        elif r.get("shortlisted_schemas") is None:
            key = "no_shortlist"
        else:
            key = "miss"
        buckets.setdefault(key, []).append(r)

    def _sort_key(k: str) -> tuple[int, int]:
        if k == "miss":
            return (1, 0)
        if k == "no_shortlist":
            return (2, 0)
        return (0, int(k))

    def _bucket_stats(rows_in: list[dict[str, Any]]) -> dict[str, Any]:
        # Denominator is rows that actually HAVE a pick, matching
        # ``schema_pick_accuracy``. Dividing by every row would report 0.0 for a
        # ``--no-llm-pick`` run (where no row has a pick at all) and quietly
        # disagree with the summary's own headline pick accuracy.
        picked = [r for r in rows_in if r.get("pick_hit") is not None]
        return {
            "n": len(rows_in),
            "ex_lenient": sum(1 for r in rows_in if r.get("correct")) / len(rows_in),
            "n_picked": len(picked),
            "pick_accuracy": (
                sum(1 for r in picked if r.get("pick_hit")) / len(picked)
                if picked
                else None
            ),
        }

    return {
        k: _bucket_stats(v)
        for k, v in sorted(buckets.items(), key=lambda kv: _sort_key(kv[0]))
    }


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def analyse_run(
    run_dir: Path | str, *, bird_dir: Path | str, split: str | None = None
) -> dict[str, Any]:
    """Full offline report for one run directory.

    ``split`` is derived from the rows and must be passed explicitly for runs that
    predate the ``split`` field; it is never guessed, because the split chooses the
    gold file and a wrong gold file silently empties every comparison.
    """
    run_dir = Path(run_dir)
    arms = load_arm_rows(run_dir)
    if not arms:
        raise FileNotFoundError(f"no generations.*.jsonl under {run_dir}")

    if split is None:
        recorded = {r.get("split") for rows in arms.values() for r in rows}
        splits = recorded - {None}
        if len(splits) > 1:
            raise RuntimeError(f"{run_dir} mixes splits {sorted(map(str, splits))}")
        # Guessing the split picks the gold file, and the wrong gold file makes every
        # question unmatchable — which this report would show as "no gold to compare",
        # a clean bill of health over zero comparisons. The caller knows what a legacy
        # run scored; this function does not, so it refuses rather than assume "test".
        if not splits:
            raise RuntimeError(
                f"{run_dir} records no split on any row (empty, or predating the "
                "field); pass --split explicitly instead of letting the gold file "
                "be guessed"
            )
        if None in recorded:
            raise RuntimeError(
                f"{run_dir} mixes rows recording split {next(iter(splits))!r} with "
                "rows recording no split at all; the unlabelled rows may be from "
                "another split. Pass --split explicitly to assert they are not."
            )
        split = splits.pop()
    gold_sql = load_gold_sql(bird_dir, split=split)

    # Even an explicit --split can name the wrong file. Zero id overlap is never a
    # real run, and every downstream count would otherwise read as "nothing to
    # compare" rather than as the wrong gold being loaded.
    qids = {str(r.get("question_id")) for rows in arms.values() for r in rows}
    if qids and not qids & gold_sql.keys():
        raise RuntimeError(
            f"none of the {len(qids)} question ids under {run_dir} appear in the "
            f"{split!r} gold file; wrong --split or wrong --bird-dir"
        )

    out: dict[str, Any] = {"run_dir": str(run_dir), "split": split, "arms": {}}
    for arm, rows in arms.items():
        out["arms"][arm] = {
            "gradeable": gradeable_report(rows, gold_sql),
            "tables": asdict(table_selection_report(rows, gold_sql, arm=arm)),
            "by_gold_rank": rank_report(rows),
        }

    # Arms are only comparable over questions all of them scored. A truncated or
    # still-running arm otherwise yields a paired test on a silent subset.
    id_sets = {arm: {str(r.get("question_id")) for r in rows} for arm, rows in arms.items()}
    common = set.intersection(*id_sets.values()) if id_sets else set()
    # The reference set is the UNION of every arm's ids, not the intersection. The
    # intersection is itself dragged down by whichever arm is short, so measuring
    # against it names every arm EXCEPT the truncated one — the opposite of the
    # question being asked. Against the union, an arm is incomplete exactly when it
    # is missing a question another arm scored, which also catches two equally sized
    # arms that cover different questions (a max-length rule would miss that).
    any_arm = set().union(*id_sets.values())
    out["question_coverage"] = {
        "n_common_to_all_arms": len(common),
        "n_scored_by_any_arm": len(any_arm),
        "per_arm": {arm: len(ids) for arm, ids in id_sets.items()},
        "incomplete_arms": sorted(a for a, ids in id_sets.items() if ids != any_arm),
    }

    # One corrupt arm must not discard the per-arm reports already computed above,
    # so a failed pairing is recorded as an error beside the ones that succeeded.
    #
    # Pairs are keyed in LADDER order where both arms are on the ladder, not in the
    # alphabetical order the generations filenames arrive in. Alphabetically
    # ``curated`` precedes ``seeded``, so the same step this report called
    # ``curated_vs_seeded`` is ``seeded -> curated`` everywhere else, and a reader
    # comparing the two artifacts by hand had to know the two files spell one pair
    # two ways. Off-ladder arms keep the alphabetical order; there is no ladder
    # position to sort them by.
    names = list(arms)
    pairs: dict[str, Any] = {}
    on_ladder: dict[str, tuple[str, str]] = {}
    for i, a in enumerate(names):
        for b in names[i + 1 :]:
            if a in ARM_ORDER and b in ARM_ORDER:
                lo, hi = sorted((a, b), key=ARM_ORDER.index)
            else:
                lo, hi = a, b
            key = f"{lo}_vs_{hi}"
            try:
                pairs[key] = asdict(mcnemar(arms[lo], arms[hi]))
            except ValueError as err:
                pairs[key] = {"error": str(err)}
            if lo in ARM_ORDER and hi in ARM_ORDER:
                on_ladder[key] = (lo, hi)

    # Which pairs are single-variable steps is not cosmetic. ``baseline_vs_curated``
    # bundles the deterministic seed and the curator LLM, and its delta cannot say
    # which paid — the exact conflation the ``seeded`` rung was added to break. This
    # report enumerates *all* pairs by construction, so each one carries what it
    # bundles rather than leaving the reader to know the ladder by heart.
    #
    # ``bundles`` is spelled and computed the same way as ``summary.json``'s
    # ``deltas.*_bundles`` (same ``skipped_rungs``), and is likewise absent rather
    # than empty when there is nothing to report. Note that a pair can be
    # consecutive among the arms this run scored and still bundle: the default arm
    # set makes ``curated -> curated_sme`` consecutive while skipping
    # ``curated_sme_blind``, which is why the flag is named for the property that
    # matters (one thing changed) and not for position in the run.
    #
    # An arm this module cannot place on the ladder gets ``None``, not ``True``.
    # ``analyse_run`` reads whatever ``generations.*.jsonl`` are in the directory, and
    # a real run holds more than the fair rungs: ``--replicate`` writes
    # ``<arm>__replicate`` and ``--oracle`` writes ``oracle_sql``. ``skipped_rungs``
    # returns ``[]`` for a name it cannot place, so ``not bundles`` would read as
    # "one thing changed" — the opposite of what an off-ladder pair is.
    for key, entry in pairs.items():
        placed = on_ladder.get(key)
        if placed is None:
            entry["single_variable"] = None
            entry["mechanisms_changed"] = []
            continue
        bundles = skipped_rungs(*placed)
        # Adjacency and "one mechanism" are different claims — see
        # ``arms.step_mechanisms`` and AUDIT E5.
        mechanisms = step_mechanisms(*placed)
        entry["adjacent_rung"] = not bundles
        entry["mechanisms_changed"] = list(mechanisms)
        entry["single_variable"] = not bundles and len(mechanisms) == 1
        if bundles:
            entry["bundles"] = bundles

    # Every pair, all at once, is a multiplicity problem: four arms is six tests, and
    # six independent tests at alpha=.05 carry a ~26% chance of at least one false
    # positive. ``summary.json`` corrects for this and the runbook's checklist asks
    # for ``p_value_holm``; this report used to publish the six raw p-values under
    # the name ``p_value`` and nothing else, so following that checklist here was
    # impossible and the honest-looking number was the wrong one to read.
    #
    # The family is the pairs that actually tested a hypothesis this report is
    # asking. Three kinds are excluded, all for the same reason — spending
    # significance on a test that was not asked makes every real comparison harder to
    # call, which is the correction working against its own purpose:
    #
    # * an **errored** pair (duplicate question ids) produced no p-value at all;
    # * a pair sharing **no questions** produced ``p_value = 1.0`` from an empty
    #   discordance count, which is not a measurement — it is the arithmetic of
    #   having nothing to compare;
    # * an **off-ladder** pair (replicate, oracle) is not a hypothesis. A replicate
    #   exists to measure the noise floor, and every pair it forms duplicates the one
    #   its source arm already forms, so a four-arm run plus one replicate would
    #   correct across ten tests where six distinct questions are being asked.
    tested = [
        k
        for k, v in pairs.items()
        if "p_value" in v and v.get("n_paired", 0) > 0 and k in on_ladder
    ]
    for key, adjusted in zip(tested, holm_adjust([pairs[k]["p_value"] for k in tested])):
        pairs[key]["p_value_holm"] = adjusted
        pairs[key]["n_family"] = len(tested)
    # Excluded pairs carry an explicit ``None``, matching ``summary.json``'s
    # convention and for its reason: a reader scanning for the adjusted column should
    # see why a row has none. Omitting the key instead made the two artifacts differ
    # in shape for the same fact, which a hand-rolled reader of both had to know.
    # ``n_family`` is still stamped, so an excluded row says how large the family it
    # was left out of was. An *errored* pair is the exception — it has no p-value of
    # any kind, so a ``p_value_holm`` beside its ``error`` would imply it was tested.
    for key, entry in pairs.items():
        if key not in tested and "error" not in entry:
            entry["p_value_holm"] = None
            entry["n_family"] = len(tested)

    out["mcnemar"] = pairs
    # Named so a reader cannot mistake the absence of a resolution claim for the
    # presence of one. ``summary.json`` is the only artifact that measures the run's
    # own noise floor, and a p-value below .05 on a delta smaller than the run can
    # resolve is still not a result.
    out["mcnemar_caveats"] = {
        "correction": "holm",
        "no_noise_floor": (
            "This report states no resolution: it has no replicate arm, so it cannot "
            "say whether a significant delta exceeds run-to-run noise. Read "
            "summary.json's comparisons[] for that."
        ),
    }
    return out


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("run_dir", type=Path, help="A runs/datalake/<timestamp> directory")
    p.add_argument("--bird-dir", type=Path, default=Path("../BIRD-Data-Obfuscation"))
    p.add_argument(
        "--split",
        default=None,
        help="Override the split recorded in rows; required if no row records one",
    )
    p.add_argument("--out", type=Path, default=None, help="Write JSON here as well")
    args = p.parse_args(argv)

    report = analyse_run(args.run_dir, bird_dir=args.bird_dir, split=args.split)
    text = json.dumps(report, indent=2, ensure_ascii=False)
    print(text)
    out = args.out or (args.run_dir / "analysis.json")
    out.write_text(text + "\n", encoding="utf-8")
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
