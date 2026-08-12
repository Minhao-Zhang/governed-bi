"""Positive function allowlist (ADR 0006 §2), keyed on sqlglot classes.

Committed class list against a pinned sqlglot major; the name set is derived
(canonical names are not SQL spellings). Everything absent is refused, including
``exp.Anonymous``. CI asserts both directions: not too narrow vs gold inventory;
``PERMITTED_FUNCTIONS ∩ ADVERSARIAL_SET == ∅``.
"""

from __future__ import annotations

import hashlib
from importlib.metadata import PackageNotFoundError, version
from typing import Mapping

from sqlglot import expressions as exp

__all__ = [
    "SQLGLOT_TESTED_MAJOR",
    "sqlglot_version",
    "PERMITTED_FUNCTION_CLASSES",
    "PERMITTED_ANONYMOUS_NAMES",
    "PERMITTED_FUNCTIONS",
    "ADVERSARIAL_SET",
    "INTENTIONALLY_ABSENT",
    "canonical_function_name",
    "permitted_functions_digest",
]

#: The sqlglot generation this list was enumerated against. A major bump renames and
#: re-parents node classes, silently changing what the allowlist matches, so a
#: mismatch raises at import. The minor version is recorded (not asserted) by the
#: ``sqlglot_version`` comparability knob.
SQLGLOT_TESTED_MAJOR = 30


def sqlglot_version() -> str:
    """The installed sqlglot version, for the ``sqlglot_version`` knob."""
    try:
        return version("sqlglot")
    except PackageNotFoundError:  # pragma: no cover - sqlglot is a hard dependency
        return "unknown"


#: The committed list. Grouped for review, flat for use.
#:
#: Everything **absent** is refused, including every ``exp.Anonymous`` — a function
#: sqlglot does not know is a function this list was never enumerated against, and
#: that is the whole B1 family (``pg_read_file``, the XML-export family,
#: ``setval``/``nextval``, ``dblink``).
PERMITTED_FUNCTION_CLASSES: tuple[type[exp.Func], ...] = (
    # ── aggregates ──
    exp.Avg, exp.Count, exp.CountIf, exp.Max, exp.Min, exp.Sum,
    exp.Stddev, exp.StddevPop, exp.StddevSamp, exp.Variance, exp.VariancePop,
    exp.Corr, exp.Median, exp.Mode, exp.PercentileCont, exp.PercentileDisc,
    exp.Quantile, exp.ApproxDistinct, exp.GroupConcat,
    # ── window ──
    exp.RowNumber, exp.Rank, exp.DenseRank, exp.Ntile, exp.NthValue,
    exp.Lag, exp.Lead, exp.FirstValue, exp.LastValue,
    # ── conditional and boolean ──
    # And/Or/Exists ARE exp.Func subclasses in this release: omit them and every WHERE
    # clause with a conjunction false-refuses. The release-dependence the pin exists for.
    exp.Case, exp.If, exp.Coalesce, exp.Nullif, exp.Nvl2,
    exp.Least, exp.Greatest, exp.Exists, exp.And, exp.Or, exp.Xor,
    # ── casts ──
    exp.Cast, exp.TryCast,
    # ── string ──
    exp.Lower, exp.Upper, exp.Initcap, exp.Length, exp.Substring, exp.Trim,
    exp.Replace, exp.Concat, exp.ConcatWs, exp.StrPosition, exp.Left, exp.Right,
    exp.Repeat, exp.StartsWith, exp.Ascii, exp.Chr,
    exp.RegexpLike, exp.RegexpExtract, exp.RegexpReplace,
    # ── numeric ──
    exp.Abs, exp.Ceil, exp.Floor, exp.Round, exp.Sign, exp.Sqrt, exp.Cbrt,
    exp.Exp, exp.Ln, exp.Log, exp.Pow,
    # ── date and time ──
    # The `current_*` family is legitimate analytics. `version()` is not, and is
    # absent: it canonicalises to CURRENT_VERSION, which is how a denylist entry
    # spelled "version" missed it.
    exp.CurrentDate, exp.CurrentTime, exp.CurrentTimestamp, exp.CurrentDatetime,
    exp.Date, exp.DateAdd, exp.DateSub, exp.DateDiff, exp.DateTrunc,
    exp.DateStrToDate, exp.DatetimeAdd, exp.DatetimeDiff, exp.DatetimeTrunc,
    exp.TimestampTrunc, exp.TimestampAdd, exp.TimestampDiff,
    exp.StrToDate, exp.StrToTime, exp.TimeToStr, exp.ToChar, exp.Extract,
    exp.Year, exp.Month, exp.Day, exp.Week, exp.Quarter,
    exp.DayOfWeek, exp.DayOfMonth, exp.DayOfYear,
)

#: Functions permitted by *name* because sqlglot parses them as ``exp.Anonymous``.
#:
#: **Empty, and measured rather than assumed:** zero of the 6,743 gold statements in
#: ``BIRD-Data-Obfuscation/eval_dataset/{train,test}_final.jsonl`` (``sql_base``,
#: 2026-08-03, inventory at ``tests/govern/gold_functions.json``) parse a function as
#: ``Anonymous``, so refusing the shape costs nothing. An entry here is a name the
#: pinned sqlglot does not model, so the allowlist cannot reason about its arguments
#: either — each one needs its own argument rule, not just a name.
PERMITTED_ANONYMOUS_NAMES: frozenset[str] = frozenset()

#: The allowlist as ``check()`` uses it: canonical names, folded.
PERMITTED_FUNCTIONS: frozenset[str] = frozenset(
    cls.sql_name().lower() for cls in PERMITTED_FUNCTION_CLASSES
) | PERMITTED_ANONYMOUS_NAMES

#: SQL spellings that must never be permitted. A committed fixture, from ADR 0006 §2.
#:
#: Compared by **parsing each spelling and canonicalising it through
#: :func:`canonical_function_name`**, never by string intersection: ``json_agg`` parses to
#: ``exp.JSONArrayAgg``, whose canonical name is ``J_S_O_N_ARRAY_AGG``, so
#: ``"json_agg" in PERMITTED_FUNCTIONS`` is ``False`` whether or not it is permitted. The
#: reasoning behind the sqlglot pin says the same thing beside the pin, in ``pyproject.toml``.
ADVERSARIAL_SET: tuple[str, ...] = (
    # B1: reads that reference no table and no column.
    "pg_read_file", "pg_read_binary_file", "pg_ls_dir", "pg_sleep",
    "lo_import", "lo_export", "dblink", "dblink_exec",
    # B1: the XML-export family. Takes its target as a string literal; one call
    # dumps a whole table.
    "query_to_xml", "table_to_xml", "schema_to_xml", "database_to_xml",
    "table_to_xmlschema", "schema_to_xmlschema", "database_to_xmlschema",
    "table_to_xml_and_xmlschema", "schema_to_xml_and_xmlschema",
    "database_to_xml_and_xmlschema", "query_to_xmlschema",
    "query_to_xml_and_xmlschema",
    # B1: SELECT-shaped write primitives.
    "setval", "nextval",
    # B2: whole-row emitters. Zero Column nodes, every column of the row.
    "json_agg", "jsonb_agg", "array_agg", "row_to_json", "to_json", "to_jsonb",
)

#: Functions that **do** appear in gold SQL and are still refused, with the reason.
#:
#: The narrowness assertion is satisfied by "permitted **or** recorded here", so a
#: chosen false refusal carries its measured cost instead of going unnoticed.
INTENTIONALLY_ABSENT: Mapping[str, str] = {
    "array_agg": (
        "B2. array_agg(t) emits every column of a row — including excluded and "
        "suspect ones — with zero Column nodes for them, so no column-level check "
        "can see it. Refusing the name costs 3 calls spread over 2 of the 6,743 gold "
        "statements (0.03%); the inventory's count of 3 is calls, not statements. "
        "Permitting it and relying on the whole-row argument rule alone "
        "would make one AST walk the only thing between a decoy column and the "
        "analyst. In ADVERSARIAL_SET, so this is enforced from both sides."
    ),
}


def canonical_function_name(node: exp.Expr) -> str:
    """The name the allowlist is keyed on: folded, schema-qualification stripped.

    ``pg_catalog.setval`` and ``setval`` are the same function; only one of them
    would be on a hand-written name list.
    """
    if isinstance(node, exp.Anonymous):
        raw = node.this
        name = raw.name if isinstance(raw, exp.Expr) else str(raw)
        return name.rsplit(".", 1)[-1].lower()
    if isinstance(node, exp.Func):
        return node.sql_name().lower()
    # Not a function node. "" rather than raising keeps the FUNCTIONS layer total: ""
    # is on no allowlist, so an unrecognised shape refuses instead of escaping.
    return ""


def permitted_functions_digest() -> str:
    """Content digest of the allowlist, for the ``permitted_functions`` knob.

    Hashed by content, so widening the list moves the serve config hash and breaks
    comparability with earlier runs. Intended, and not to be worked around: without
    it two runs with different security configuration hash identically (ADR 0006 §13).
    """
    payload = "\n".join(sorted(PERMITTED_FUNCTIONS))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _assert_allowlist_is_coherent() -> None:
    """Import-time guards. Three, none of them definitional."""
    installed = sqlglot_version()
    major = installed.split(".")[0]
    if major.isdigit() and int(major) != SQLGLOT_TESTED_MAJOR:  # pragma: no cover
        raise AssertionError(
            f"sqlglot {installed} is installed; this allowlist was enumerated against "
            f"{SQLGLOT_TESTED_MAJOR}.x. Canonical names and class parentage change "
            "across majors, so the allowlist's correctness is unknown — re-derive it "
            "and re-run tests/govern/ rather than loosening this."
        )

    if len(PERMITTED_FUNCTIONS) != len(PERMITTED_FUNCTION_CLASSES) + len(PERMITTED_ANONYMOUS_NAMES):
        collisions = sorted(
            name for name in PERMITTED_FUNCTIONS
            if sum(1 for cls in PERMITTED_FUNCTION_CLASSES if cls.sql_name().lower() == name) > 1
        )
        raise AssertionError(  # pragma: no cover - import-time guard
            f"two permitted classes share one canonical name: {collisions}. The name set "
            "is the thing check() matches on, so a collision means one of the two is "
            "permitted by accident."
        )

    if "anonymous" in PERMITTED_FUNCTIONS:  # pragma: no cover - import-time guard
        raise AssertionError(
            "exp.Anonymous is on the allowlist, which permits every function sqlglot "
            "does not model — the entire B1 family."
        )


_assert_allowlist_is_coherent()
