"""Which stage is responsible for a *wrong answer*, and what kind of wrong it is.

:mod:`governed_bi.stages` attributes turns that refused, capped, or crashed. That
leaves the largest population unattributed: turns that ran cleanly, produced SQL,
executed it, and returned the wrong rows. On the last full benchmark those were
45.8% of every question (a RETIRED pre-2026-07-25 figure — see docs/measurement.md)
and they all landed in one bucket called
"right schema, wrong SQL" — a bucket too big to act on, which is why the estimate
of what fixing it was worth ranged over an order of magnitude.

This module splits that population two ways.

**By stage.** Attribution is a cascade from the outside in: a turn that reached the
wrong schema is a routing failure whatever else is wrong with its SQL, because the
SQL was written against tables it should never have seen. Only once the schema is
right does a wrong table set become a table-selection failure, and only once the
tables are right does the remaining difference belong to SQL construction. The
cascade is what makes the buckets mutually exclusive and therefore summable —
each question is charged to exactly one stage, the outermost one that went wrong.

**By class.** Within a stage, the *dimensions* that differ
(:mod:`governed_bi.eval.sql_diff`) are reported as a set, not a single label.
A wrong query typically differs along several at once — the measured distribution
runs to eleven — so classes are counted as incidence, and ``n_classes`` is recorded
per question so a reader can see the overlap directly. This matters: per-class
counts do *not* sum to a headroom estimate, because fixing one class leaves a
query that is still wrong along the other four. The previous report discovered this
after publishing per-class point estimates; recording it in the artifact is cheaper
than rediscovering it.

The cascade gives mutually exclusive stage buckets. It does **not** give causal
headroom — "how much EX would I gain by fixing table selection" is a counterfactual
question, and the honest answer comes from :mod:`governed_bi.eval.oracle`, which
substitutes gold for one stage and re-measures. Read this module for *where* the
errors are and that module for *what they cost*.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable, Mapping

from ..stages import Outcome, Stage, classify_row
from .sql_diff import Dimension, SqlDiff, Verdict, diff_sql

__all__ = [
    "ErrorClass",
    "Attribution",
    "attribute_row",
    "attribute_rows",
    "summarise_attributions",
]


class ErrorClass(str, Enum):
    """What kind of wrong an answer is.

    The first four are not SQL-construction classes at all — they say the question
    never got far enough for the SQL to be the problem, and keeping them distinct is
    what stops a routing failure from being counted as a join bug.
    """

    # Pre-SQL failures.
    embedding_wall = "embedding_wall"  # gold schema absent from the shortlist
    wrong_schema = "wrong_schema"  # shortlist held it; the picker chose another
    unparseable_sql = "unparseable_sql"  # produced text that will not parse
    gold_unusable = "gold_unusable"  # frozen constant / unparseable gold
    #: Parsed fine, then raised when the grader executed it (a type error, an
    #: unknown column, a division by zero). The driver classifies this as a wrong
    #: *answer* rather than a crash — correctly, it is the model's statement that
    #: failed, not the harness — but it must not then be folded in with the
    #: structural classes: a statement that never returned rows cannot be compared
    #: against gold's rows, and it is emphatically not "structurally identical and
    #: still wrong".
    execution_error = "execution_error"

    # Structural classes, one per sql_diff dimension that differs.
    wrong_table = "wrong_table"
    wrong_join_graph = "wrong_join_graph"
    wrong_join_key = "wrong_join_key"
    wrong_join_type = "wrong_join_type"
    wrong_projection = "wrong_projection"
    projection_order = "projection_order"
    wrong_filter_column = "wrong_filter_column"
    wrong_filter_literal = "wrong_filter_literal"
    wrong_aggregation = "wrong_aggregation"
    wrong_group_by = "wrong_group_by"
    wrong_order_limit = "wrong_order_limit"
    wrong_distinct = "wrong_distinct"
    wrong_set_op = "wrong_set_op"

    # Structurally identical to gold and still wrong. Not a residual bucket: it is
    # a positive finding that the error is in a *value* — a literal's casing or
    # format, a LIKE pattern that matches nothing.
    value_level = "value_level"

    # The diff could not decide. At least one dimension resolved to
    # ``Verdict.unknown`` (alias scope resolution failed), so "no mismatch found" is
    # not the same statement as "everything matched". Kept as its own class because
    # folding it into ``value_level`` made an inconclusive comparison look identical
    # to a clean structural match with a bad literal — the two need opposite
    # responses (fix the differ vs. fix the prompt).
    unresolved_diff = "unresolved_diff"


#: Dimension -> the class it produces. Order matters: it is the order in which a
#: query's classes are reported, outermost-structural first.
_DIMENSION_CLASS: tuple[tuple[Dimension, ErrorClass], ...] = (
    (Dimension.table_set, ErrorClass.wrong_table),
    (Dimension.join_graph, ErrorClass.wrong_join_graph),
    (Dimension.join_keys, ErrorClass.wrong_join_key),
    (Dimension.join_type, ErrorClass.wrong_join_type),
    (Dimension.projection, ErrorClass.wrong_projection),
    (Dimension.filter_columns, ErrorClass.wrong_filter_column),
    (Dimension.filter_literals, ErrorClass.wrong_filter_literal),
    (Dimension.aggregation, ErrorClass.wrong_aggregation),
    (Dimension.group_by, ErrorClass.wrong_group_by),
    (Dimension.order_limit, ErrorClass.wrong_order_limit),
    (Dimension.distinct, ErrorClass.wrong_distinct),
    (Dimension.set_ops, ErrorClass.wrong_set_op),
)

#: The cascade. The first entry whose class is present decides the stage, so a
#: question is charged to the outermost thing that went wrong and to nothing else.
#: ``schema_set`` is absent deliberately: a cross-schema reference is caught by
#: ``wrong_schema`` above via routing metadata, which is more reliable than the
#: qualifier the model happened to write.
_STAGE_CASCADE: tuple[tuple[ErrorClass, Stage | None], ...] = (
    (ErrorClass.embedding_wall, Stage.shortlist),
    (ErrorClass.wrong_schema, Stage.schema_pick),
    # Outermost SQL-level failure: it never produced rows, so no dimension of it can
    # be compared. ``Stage.execute`` matches the convention the live path already
    # uses (``refused_by="execution"`` -> ``Stage.execute``), so an execution failure
    # lands in the same bucket whether the agent hit it mid-loop or the grader hit it
    # on the final statement.
    (ErrorClass.execution_error, Stage.execute),
    (ErrorClass.unparseable_sql, Stage.sql_generate),
    (ErrorClass.wrong_table, Stage.table_select),
    (ErrorClass.wrong_join_graph, Stage.sql_generate),
    (ErrorClass.wrong_join_key, Stage.sql_generate),
    (ErrorClass.wrong_join_type, Stage.sql_generate),
    (ErrorClass.wrong_aggregation, Stage.sql_generate),
    (ErrorClass.wrong_group_by, Stage.sql_generate),
    (ErrorClass.wrong_filter_column, Stage.sql_generate),
    (ErrorClass.wrong_filter_literal, Stage.sql_generate),
    (ErrorClass.wrong_projection, Stage.sql_generate),
    (ErrorClass.projection_order, Stage.sql_generate),
    (ErrorClass.wrong_distinct, Stage.sql_generate),
    (ErrorClass.wrong_order_limit, Stage.sql_generate),
    (ErrorClass.wrong_set_op, Stage.sql_generate),
    # ``value_level`` is charged to ``sql_generate``, and the reasoning matters
    # because an earlier version deliberately charged it to nothing.
    #
    # The old rule required an execution probe to "confirm" the failure before the
    # row could carry a stage. That gate was unreachable — nothing on the run path
    # ever produced the field it read — so the class was permanently unattributed and
    # ``by_error_stage`` did not sum to ``n_wrong``. But wiring the probe up would
    # have been the wrong fix too, because ``execute`` is the wrong stage for this
    # class either way: the statement executed *successfully* and returned exactly
    # the rows it asked for. The mistake is in a value the generator wrote, which is
    # ``sql_generate``'s territory. ``Stage.execute`` stays reserved for a statement
    # that genuinely failed to run, which the live path already stamps via
    # ``refused_by="execution"``.
    #
    # ``Attribution.result_shape`` carries the descriptive half (did the query come
    # back empty, or with gold's row count but different contents?) derived for free
    # from the grading fields, with no stage implication and no extra query.
    (ErrorClass.value_level, Stage.sql_generate),
    # An inconclusive diff is charged to nothing, and this one IS a real absence: we
    # do not know where it went wrong. Counted so the gap has a size.
    (ErrorClass.unresolved_diff, None),
)


@dataclass
class Attribution:
    """Why one row was wrong: a stage, a primary class, and every class present."""

    question_id: str
    outcome: Outcome
    correct: bool
    #: ``None`` for a correct answer, and for a row whose gold could not be used.
    stage: Stage | None = None
    primary: ErrorClass | None = None
    classes: tuple[ErrorClass, ...] = ()
    #: How many structural dimensions differ. Recorded per row because the
    #: distribution is the evidence that per-class headroom is not additive.
    n_classes: int = 0
    gradeable: bool = True
    diff: SqlDiff | None = None
    #: What the executed result *looked like* next to gold, from the row's own
    #: grading fields — no extra query. Descriptive only: it never decides a stage.
    #: See :func:`_result_shape`.
    result_shape: str | None = None
    #: True when the structural diff ran and both sides parsed, i.e. the per-dimension
    #: verdicts on this row are meaningful. The histogram that feeds
    #: ``multi_class_share`` is restricted to these rows: a routing failure or an
    #: unparseable statement never reached the differ, so counting it as a
    #: "single-dimension" error dilutes the share of genuine multi-dimension ones.
    diffed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "failed_stage": self.stage.value if self.stage else None,
            "error_primary": self.primary.value if self.primary else None,
            "error_classes": [c.value for c in self.classes],
            "n_error_classes": self.n_classes,
            "gradeable": self.gradeable,
            "result_shape": self.result_shape,
        }


def attribute_row(
    row: Mapping[str, Any],
    gold_sql: str | None,
    *,
    gold_in_shortlist: bool | None = None,
    dialect: str = "postgres",
) -> Attribution:
    """Attribute one scored row to a stage and a set of error classes.

    ``gold_in_shortlist`` separates the two routing failures: a picker that chose
    wrongly from candidates that included the right answer is a *prompt* problem,
    while a gold schema that never made the shortlist is an *embedding* problem, and
    they are fixed in different places. Pass ``None`` when the shortlist was not
    recorded; the row then attributes to ``schema_pick``, which is the conservative
    reading — it blames the component we can see.
    """
    qid = str(row.get("question_id") or row.get("request_id") or "")
    outcome, live_stage, _ = classify_row(row)
    correct = bool(row.get("correct"))

    if correct:
        return Attribution(qid, outcome, True)

    # A turn that never produced SQL was already attributed by the live path
    # (refused / capped / crashed). Re-deriving it here from a missing statement
    # would relabel a governed refusal as a SQL-generation failure.
    if outcome is not Outcome.answered:
        return Attribution(qid, outcome, False, stage=live_stage)

    # Routing, before anything about the SQL matters — unless routing never ran.
    # An oracle rung pins the corpus to one schema, so the router sees a single
    # candidate, never engages, and stamps no provenance. Read literally that is
    # ``routed_hit=False`` on every row, and the rung whose whole purpose is to
    # remove routing error would report routing as the entire problem. A bypassed
    # router cannot have missed.
    routing_bypassed = bool(row.get("routing_bypassed"))
    if not routing_bypassed and (
        row.get("routed_hit") is False or row.get("pick_hit") is False
    ):
        cls = (
            ErrorClass.embedding_wall
            if gold_in_shortlist is False
            else ErrorClass.wrong_schema
        )
        return Attribution(
            qid,
            outcome,
            False,
            stage=_STAGE_CASCADE[0][1] if cls is ErrorClass.embedding_wall else Stage.schema_pick,
            primary=cls,
            classes=(cls,),
            n_classes=1,
        )

    # The statement raised when the grader ran it. Decided BEFORE the structural
    # diff, because a statement that returned no rows has no result to have been
    # "structurally identical and still wrong" about — that reading charged a
    # non-executable query to a bad literal.
    if str(row.get("error") or "").startswith("exec_error:"):
        return Attribution(
            qid,
            outcome,
            False,
            stage=Stage.execute,
            primary=ErrorClass.execution_error,
            classes=(ErrorClass.execution_error,),
            n_classes=1,
        )

    # A row where NEITHER routing field was recorded — both ``None``, not ``False`` —
    # cannot be attributed to a stage. It used to fall straight through to the
    # structural differ below and be charged to ``sql_generate``/``table_select``: a
    # confident stage attribution for a turn whose routing status was never observed.
    # Everywhere else in this module absence is preserved (``_result_shape`` returns
    # ``None``, ``unresolved_diff`` exists precisely for "we could not tell").
    #
    # Placed AFTER the exec-error branch on purpose: a statement that raised at grading
    # is attributable whether or not routing was recorded, and we know which stage.
    if (
        not routing_bypassed
        and row.get("routed_hit") is None
        and row.get("pick_hit") is None
    ):
        return Attribution(
            qid,
            outcome,
            False,
            stage=None,
            primary=ErrorClass.unresolved_diff,
            classes=(ErrorClass.unresolved_diff,),
            n_classes=1,
        )

    diff = diff_sql(row.get("generated_sql"), gold_sql, dialect=dialect)

    if diff.gold_frozen or not diff.gold_parsed:
        # Not our failure to own. A gold answer that hardcodes its rows cannot be
        # reached from schema; charging it to SQL generation inflates every class.
        return Attribution(
            qid,
            outcome,
            False,
            stage=None,
            primary=ErrorClass.gold_unusable,
            classes=(ErrorClass.gold_unusable,),
            n_classes=0,
            gradeable=False,
            diff=diff,
        )

    if not diff.gen_parsed:
        return Attribution(
            qid,
            outcome,
            False,
            stage=Stage.sql_generate,
            primary=ErrorClass.unparseable_sql,
            classes=(ErrorClass.unparseable_sql,),
            n_classes=1,
            diff=diff,
        )

    classes: list[ErrorClass] = []
    n_unresolved = 0
    for dimension, cls in _DIMENSION_CLASS:
        d = diff.dimensions.get(dimension)
        if d is None:
            continue
        if d.verdict is Verdict.unknown:
            # NOT the same as a match. Alias/scope resolution failed, so this
            # dimension has no verdict at all. Treating it as "no mismatch" (which the
            # loop used to do) let a row whose table-sensitive dimensions were all
            # unresolved fall through to ``value_level`` — reported as "structurally
            # identical, bad literal" when the truth was "we could not tell".
            n_unresolved += 1
            continue
        if d.verdict is not Verdict.mismatch:
            continue
        if dimension is Dimension.projection and d.order_only:
            classes.append(ErrorClass.projection_order)
        else:
            classes.append(cls)

    if not classes:
        classes = [
            # Nothing mismatched, but something could not be compared: the honest
            # class is "unknown", not "bad value".
            ErrorClass.unresolved_diff
            if n_unresolved
            # Every dimension resolved and every one matched, and the answer is still
            # wrong: the difference is in a value.
            else ErrorClass.value_level
        ]

    primary = _primary_of(classes)
    return Attribution(
        qid,
        outcome,
        False,
        stage=_stage_of(primary),
        primary=primary,
        classes=tuple(classes),
        # Structural dimensions that differ. ``value_level`` and ``unresolved_diff``
        # are not dimensions, so they contribute 0 — see ``diffed`` for why that does
        # not silently drop them from the multi-class denominator.
        n_classes=len(
            [
                c
                for c in classes
                if c not in (ErrorClass.value_level, ErrorClass.unresolved_diff)
            ]
        ),
        diff=diff,
        result_shape=_result_shape(row),
        diffed=True,
    )


#: Descriptive shapes for a wrong answer, derived from the fields the grader already
#: wrote. Deliberately NOT a stage input: knowing the query came back empty tells you
#: what a bad literal did, not that a different component is at fault.
def _result_shape(row: Mapping[str, Any]) -> str | None:
    """How the executed result compared to gold, from grading fields alone.

    Free: ``score_sql_hashes`` already executes the generated statement and records
    ``pred_nrows`` / ``gold_nrows`` / ``nrows_match``, and the gold artifact ships
    ``nrows``. An earlier design ran a second pair of queries per wrong row to
    recover this; the two extra round-trips bought a distinction the grader had
    already paid for.

    ``None`` when the counts were not recorded — unmeasured, never "matched".
    """
    pred, gold_n = row.get("pred_nrows"), row.get("gold_nrows")
    if not isinstance(pred, int) or not isinstance(gold_n, int):
        return None
    if pred == 0 and gold_n == 0:
        return "both_empty"
    if pred == 0:
        # The statement ran and matched nothing. Almost always a filter literal.
        return "empty_result"
    if pred != gold_n:
        return "row_count_differs"
    # Gold's row count, different contents: a projection, ordering or value defect
    # rather than a different result set.
    return "same_row_count"


def _primary_of(classes: Iterable[ErrorClass]) -> ErrorClass:
    present = set(classes)
    for cls, _stage in _STAGE_CASCADE:
        if cls in present:
            return cls
    return ErrorClass.value_level


def _stage_of(cls: ErrorClass) -> Stage | None:
    for candidate, stage in _STAGE_CASCADE:
        if candidate is cls:
            return stage
    return None


def attribute_rows(
    rows: Iterable[Mapping[str, Any]],
    gold: Mapping[str, Any],
    *,
    shortlists: Mapping[str, Iterable[str]] | None = None,
    dialect: str = "postgres",
) -> list[Attribution]:
    """Attribute every row. ``gold`` maps question_id to the gold record.

    A gold record may be the raw dataset dict (``sql_rename`` is read from it) or a
    bare SQL string, so this works against both the dataset and a slimmed fixture.
    """
    out: list[Attribution] = []
    for row in rows:
        qid = str(row.get("question_id") or row.get("request_id") or "")
        record = gold.get(qid)
        gold_sql = _gold_sql(record)
        in_shortlist: bool | None = None
        if shortlists is not None:
            candidates = shortlists.get(qid)
            if candidates is not None:
                in_shortlist = str(row.get("db_id")) in set(candidates)
        out.append(
            attribute_row(
                row, gold_sql, gold_in_shortlist=in_shortlist, dialect=dialect
            )
        )
    return out


def _gold_sql(record: Any) -> str | None:
    if record is None:
        return None
    if isinstance(record, str):
        return record
    if isinstance(record, Mapping):
        for key in ("sql_rename", "sql", "gold_sql", "sql_base"):
            value = record.get(key)
            if value:
                return str(value)
    return None


def summarise_attributions(attributions: Iterable[Attribution]) -> dict[str, Any]:
    """Aggregate attributions into the shape a summary artifact carries.

    Every rate is ``None`` at an empty denominator rather than ``0.0``, matching the
    convention the rest of the harness uses: a rate of zero is a measurement, and an
    absent denominator is not.
    """
    items = list(attributions)
    n = len(items)
    wrong = [a for a in items if not a.correct]
    gradeable_wrong = [a for a in wrong if a.gradeable]

    by_stage: Counter = Counter()
    by_primary: Counter = Counter()
    class_incidence: Counter = Counter()
    n_class_hist: Counter = Counter()
    by_result_shape: Counter = Counter()
    n_unattributed = 0

    for a in wrong:
        if a.stage is not None:
            by_stage[a.stage.value] += 1
        else:
            n_unattributed += 1
        if a.primary is not None:
            by_primary[a.primary.value] += 1
        for cls in a.classes:
            class_incidence[cls.value] += 1
        if a.result_shape is not None:
            by_result_shape[a.result_shape] += 1
        # Restricted to rows the structural differ actually ran on. A routing failure
        # and an unparseable statement are pinned to one class because they never
        # reached the differ, not because exactly one dimension differed — counting
        # them here made the multi-dimension share look small by padding its
        # denominator with rows that could not have been multi-dimension.
        if a.gradeable and a.diffed:
            n_class_hist[a.n_classes] += 1

    multi = sum(v for k, v in n_class_hist.items() if k > 1)
    total_classed = sum(n_class_hist.values())

    return {
        "n": n,
        "n_wrong": len(wrong),
        "n_wrong_gradeable": len(gradeable_wrong),
        "n_gold_unusable": len(wrong) - len(gradeable_wrong),
        "by_error_stage": dict(by_stage.most_common()),
        # Wrong answers charged to no stage: unusable gold, and rows the differ could
        # not resolve. ``by_error_stage`` + this == ``n_wrong``, which is the
        # invariant that makes the stage table readable as a partition. Named distinctly
        # from the live serve summary's ``by_failed_stage`` (Outcome/Stage from
        # ``classify_row``) so the two artifacts cannot be mixed. Reported rather than
        # asserted: an absence with a size is auditable, a claim that the buckets sum
        # is not.
        "n_unattributed": n_unattributed,
        "by_error_primary": dict(by_primary.most_common()),
        "error_class_incidence": dict(class_incidence.most_common()),
        # Row-count shape of each wrong answer against gold, free from the grading
        # fields. Separates "matched nothing" from "gold's shape, wrong contents"
        # without a second execution pass.
        "by_result_shape": dict(by_result_shape.most_common()),
        "classes_per_query": {str(k): v for k, v in sorted(n_class_hist.items())},
        # Stated explicitly so nobody has to derive it before reading the table
        # above as if its rows were independent levers. Denominator: gradeable wrong
        # answers the differ resolved (see ``diffed``).
        "multi_class_share": (multi / total_classed) if total_classed else None,
        "n_multi_class_denominator": total_classed,
    }
