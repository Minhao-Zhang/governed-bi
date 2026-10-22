"""Positive allowlist, keyed on sqlglot classes. See ADR 0006. Names are derived from a pinned major. Everything absent
is refused, including ``exp.Anonymous``."""

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

#: Enumerated against this sqlglot major. A major bump re-parents node classes and
#: changes what the allowlist matches, so a mismatch raises at import. The minor is
#: recorded by the ``sqlglot_version`` setting, not asserted here.
SQLGLOT_TESTED_MAJOR = 30


def sqlglot_version() -> str:
    """Installed sqlglot version, for the ``sqlglot_version`` setting. Not the major pin."""
    try:
        return version("sqlglot")
    except PackageNotFoundError:  # pragma: no cover - sqlglot is a hard dependency
        return "unknown"


#: Everything absent is refused, including ``exp.Anonymous``: a function sqlglot does
#: not model was never enumerated, and that is the B1 family.
PERMITTED_FUNCTION_CLASSES: tuple[type[exp.Func], ...] = (
    exp.Avg, exp.Count, exp.CountIf, exp.Max, exp.Min, exp.Sum,
    exp.Stddev, exp.StddevPop, exp.StddevSamp, exp.Variance, exp.VariancePop,
    exp.Corr, exp.Median, exp.Mode, exp.PercentileCont, exp.PercentileDisc,
    exp.Quantile, exp.ApproxDistinct, exp.GroupConcat,
    exp.RowNumber, exp.Rank, exp.DenseRank, exp.Ntile, exp.NthValue,
    exp.Lag, exp.Lead, exp.FirstValue, exp.LastValue,
    # And/Or/Exists are exp.Func in this release: omit them and every conjunction false-refuses.
    # That parentage is why the major is pinned.
    exp.Case, exp.If, exp.Coalesce, exp.Nullif, exp.Nvl2,
    exp.Least, exp.Greatest, exp.Exists, exp.And, exp.Or, exp.Xor,
    exp.Cast, exp.TryCast,
    exp.Lower, exp.Upper, exp.Initcap, exp.Length, exp.Substring, exp.Trim,
    exp.Replace, exp.Concat, exp.ConcatWs, exp.StrPosition, exp.Left, exp.Right,
    exp.Repeat, exp.StartsWith, exp.Ascii, exp.Chr,
    exp.RegexpLike, exp.RegexpExtract, exp.RegexpReplace,
    exp.Abs, exp.Ceil, exp.Floor, exp.Round, exp.Sign, exp.Sqrt, exp.Cbrt,
    exp.Exp, exp.Ln, exp.Log, exp.Pow,
    # ``current_*`` is analytics. ``version()`` is absent: it canonicalises to
    # CURRENT_VERSION, which is how a denylist entry spelled "version" missed it.
    exp.CurrentDate, exp.CurrentTime, exp.CurrentTimestamp, exp.CurrentDatetime,
    exp.Date, exp.DateAdd, exp.DateSub, exp.DateDiff, exp.DateTrunc,
    exp.DateStrToDate, exp.DatetimeAdd, exp.DatetimeDiff, exp.DatetimeTrunc,
    exp.TimestampTrunc, exp.TimestampAdd, exp.TimestampDiff,
    exp.StrToDate, exp.StrToTime, exp.TimeToStr, exp.ToChar, exp.Extract,
    exp.Year, exp.Month, exp.Day, exp.Week, exp.Quarter,
    exp.DayOfWeek, exp.DayOfMonth, exp.DayOfYear,
)

#: Empty on purpose. Gold SQL parses no function as ``Anonymous``, so refusing the shape
#: costs nothing. A name here is one the pinned sqlglot does not model, and its arguments
#: would need their own rule.
PERMITTED_ANONYMOUS_NAMES: frozenset[str] = frozenset()

#: The allowlist as ``check()`` uses it: canonical names, folded.
PERMITTED_FUNCTIONS: frozenset[str] = frozenset(
    cls.sql_name().lower() for cls in PERMITTED_FUNCTION_CLASSES
) | PERMITTED_ANONYMOUS_NAMES

#: Spellings that must never be permitted. See ADR 0006.
#: Compared by parsing and :func:`canonical_function_name`, not string intersection:
#: ``json_agg`` canonicalises to ``J_S_O_N_ARRAY_AGG``.
ADVERSARIAL_SET: tuple[str, ...] = (
    # B1: reads that reference no table and no column.
    "pg_read_file", "pg_read_binary_file", "pg_ls_dir", "pg_sleep",
    "lo_import", "lo_export", "dblink", "dblink_exec",
    # B1: XML export. The target is a string literal; one call dumps a table.
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

#: Gold functions still refused. Narrowness is "permitted or recorded here", so a chosen
#: false refusal keeps its measured cost.
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
    """Schema qualification stripped. ``pg_catalog.setval`` and ``setval`` are one function."""
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
    """Widening the list moves the serve config hash. See ADR 0006."""
    payload = "\n".join(sorted(PERMITTED_FUNCTIONS))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _assert_allowlist_is_coherent() -> None:
    """Major pin, unique canonical names, and ``Anonymous`` stays off the allowlist."""
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
