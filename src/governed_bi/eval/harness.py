"""Shared helpers for the eval drivers.

Extracted from ``run_experiment`` because the pooled driver imported ten of that
module's private symbols directly — a driver reaching into another driver's
implementation, which is how the manifest builders drifted apart in the first
place (see :mod:`governed_bi.eval.metrics`).

Nothing here is a metric definition; the register lives in
:mod:`governed_bi.eval.metrics`. This is the plumbing both drivers need: corpus
validation and curator-error collection, the atomic JSONL writer, the cost block,
the SME no-op signal, and the refuse-everything solver used by the offline path.

When the two drivers collapse into one, this module stays — it is the first slice
off a 4,700-line driver, not scaffolding for the merge.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..corpus import load_corpus
from ..corpus.schemas import ReliabilityStatus, TableAsset
from ..corpus.validate import validate_corpus

if TYPE_CHECKING:
    pass


def _dsn_host(dsn: str) -> str:
    """``host:port`` from a libpq DSN, with no credentials.

    The manifest used to hard-code ``127.0.0.1:5435`` regardless of ``--pg-dsn``, so
    two runs against different databases were indistinguishable in the record. Only
    the host and port are taken: a DSN carries a password, and a manifest is an
    artifact people paste into issues.
    """
    parts = dict(
        kv.split("=", 1) for kv in str(dsn).split() if "=" in kv
    )
    host = parts.get("host", "?")
    port = parts.get("port", "")
    return f"{host}:{port}" if port else host


def _utc_ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _sum_or_none(rows: list[dict[str, Any]], key: str) -> float | None:
    """Sum of a numeric row field, or ``None`` when no row recorded it.

    A run with no cost data and a run that genuinely cost nothing are different
    facts, so the two must not both collapse to one value: ``0.0`` is reported only
    when some row actually observed a zero.
    """
    vals = [float(r[key]) for r in rows if isinstance(r.get(key), (int, float))]
    return sum(vals) if vals else None


def _mean_or_none(rows: list[dict[str, Any]], key: str) -> float | None:
    """Mean over the rows that recorded ``key``, else ``None`` — never ``0.0``,
    which would read as a real observation of zero."""
    vals = [float(r[key]) for r in rows if isinstance(r.get(key), (int, float))]
    return sum(vals) / len(vals) if vals else None


def _total_tokens(rows: list[dict[str, Any]]) -> int | None:
    """Total tokens across rows, from whichever usage shape the provider returned."""
    total = 0
    seen = False
    for r in rows:
        usage = r.get("usage") or r.get("token_sum")
        if isinstance(usage, dict):
            value = usage.get("total_tokens")
            if isinstance(value, (int, float)):
                total += int(value)
                seen = True
    return total if seen else None


def _round_or_none(value: float | None, digits: int) -> float | None:
    """Rounding that preserves "not measured": ``round(value or 0.0)`` would turn an
    unrecorded field into a measured zero."""
    return None if value is None else round(value, digits)


def _cost_block(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Wall-clock + token/dollar cost for one arm (parity with the pooled driver).

    Kept out of :class:`ArmSummary` on purpose: latency is scheduler-dependent, and
    the workers-invariance test compares the whole summary — a serial and a pooled
    run must agree on every number that is a *result*
    (docs/measurement.md). Nesting it here instead is what lets
    that comparison stay total while the cost still lands in ``summary.json``.
    """
    return {
        "total_latency_sec": _round_or_none(_sum_or_none(rows, "latency_sec"), 2),
        "mean_latency_sec": _round_or_none(_mean_or_none(rows, "latency_sec"), 3),
        "total_cost_est_usd": _sum_or_none(rows, "cost_est_usd"),
        # How many rows the total actually covers. _sum_or_none sums only the rows
        # that carried the key, so a crashed turn — which burned model calls and recorded
        # no meta — contributes nothing and deflates the total. That was cosmetic while
        # cost was context; it is load-bearing now that ladder_deltas divides by it,
        # which is why that function refuses to price a step whose cost covers fewer rows
        # than the arm scored.
        "n_rows_priced": sum(1 for r in rows if r.get("cost_est_usd") is not None),
        "total_tokens": _total_tokens(rows),
    }


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    """Overwrite ``path`` atomically (temp + flush/fsync + replace).

    Crash-row resume rewrites generations files through this helper. An in-place
    ``open("w")`` truncate left a kill mid-write able to destroy already-scored
    rows; the temp-file swap keeps the previous file until the new one is durable.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp{os.getpid()}")
    try:
        with tmp.open("w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _validate_corpora(corpora: dict[str, Any], *, connector: Any = None) -> dict[str, dict]:
    """CI-green gate: run ``validate_corpus`` on each arm's corpus so a corpus
    with a reference-integrity defect can never be scored *silently*. Returns a
    per-arm ``{finding_count, findings[:20]}`` block for ``summary.json``.

    This closes the gap that let dangling term bindings ride into a scored arm
    unnoticed: the count is now a headline field, not something buried in a
    per-corpus manifest. ``connector`` (optional) additionally checks physical
    existence against the live catalog.
    """

    out: dict[str, dict] = {}
    for arm_name, loaded in corpora.items():
        findings = validate_corpus(loaded.assets, connector=connector)
        out[arm_name] = {
            "finding_count": len(findings),
            "findings": [f"{f.code} [{f.asset_id}]: {f.message}" for f in findings[:20]],
        }
    return out


def _collect_curator_errors(corpus_dirs: dict[str, Path]) -> dict[str, dict]:
    """Surface swallowed curator failures at the run level.

    ``_invoke_agent`` catches agent crashes and records them in the per-corpus
    ``run_manifest.json`` (``error`` / ``fix_pass_error``) without aborting — so a
    crashed fold or fix-pass is invisible in the headline ``summary.json``. Lift
    the short form of any recorded error up so it is not swallowed silently. The
    full traceback stays in the per-corpus manifest.
    """
    out: dict[str, dict] = {}
    for arm, d in corpus_dirs.items():
        # A diagnostic that could not be promoted leaves this marker. Checked FIRST,
        # and reported even though it is not itself a curator error: it means the
        # manifest below may be absent or stale, and "absent" is read two lines down
        # as "no error". Without this, a build whose crash record went missing is
        # indistinguishable from a build that did not crash — the exact swallowed
        # failure this function exists to surface.
        marker = d / "UNPROMOTED_SIDECARS.json"
        unpromoted: str | None = None
        if marker.exists():
            try:
                names = json.loads(marker.read_text(encoding="utf-8")).get("unpromoted")
            except ValueError:
                names = None
            unpromoted = (
                "curator diagnostics could not be promoted "
                f"({', '.join(names) if names else 'see marker'}) — this db's "
                "curator verdict is unknown"
            )
        mpath = d / "run_manifest.json"
        # Both are reported when both exist. Returning early on the marker would drop
        # a real, recorded curator crash in favour of the note saying a *different*
        # file went missing — losing the more specific finding of the two.
        if not mpath.exists():
            if unpromoted:
                out[arm] = {"error": unpromoted, "fix_pass_error": None}
            continue
        m = json.loads(mpath.read_text(encoding="utf-8"))
        err, fix_err = m.get("error"), m.get("fix_pass_error")
        if err or fix_err or unpromoted:
            first = (err or "").splitlines()[0] if err else None
            out[arm] = {
                "error": "; ".join(x for x in (unpromoted, first) if x) or None,
                "fix_pass_error": (fix_err or "").splitlines()[0] if fix_err else None,
            }
    return out


def _warn_if_curator_errors(curator_errors: dict[str, dict]) -> None:
    for arm, block in curator_errors.items():
        print(
            f"\n*** WARNING: curator error on arm {arm!r} was swallowed during "
            f"build (corpus still scored): error={block['error']!r} "
            f"fix_pass_error={block['fix_pass_error']!r} ***"
        )


def _sme_fold_signal(
    curated_root: Path, curated_sme_root: Path, schema: str
) -> dict[str, Any]:
    """Surface the SME fold outcome so a curated_sme arm that ended byte-identical
    to curated (empty/missing ledger → nothing folded, or a resume that reused a
    prior no-op corpus) is visible in summary.json instead of masquerading as
    "SME added nothing". Reads the Phase-B manifest (root, or the
    data-lake-relocated ``<schema>/_build/`` location)."""
    from ..curator.pipeline import _corpora_differ

    manifest = curated_sme_root / "run_manifest.json"
    if not manifest.exists():
        manifest = curated_sme_root / schema / "_build" / "run_manifest.json"
    data: dict[str, Any] = {}
    if manifest.exists():
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
    return {
        "fold_mode": data.get("fold_mode"),
        "ledger_source": data.get("ledger_source"),
        "clarification_count": data.get("clarification_count"),
        "clarifications_applied": data.get("clarifications_applied"),
        "identical_to_curated": not _corpora_differ(
            curated_root, curated_sme_root, schema
        ),
    }


def _warn_if_sme_noop(sme_fold: dict[str, Any], *, db_id: str) -> None:
    if sme_fold.get("identical_to_curated"):
        print(
            f"\n*** WARNING: curated_sme is byte-identical to curated for {db_id!r} "
            f"(fold_mode={sme_fold.get('fold_mode')!r}, "
            f"ledger_source={sme_fold.get('ledger_source')!r}) — the SME arm folded "
            "nothing; its EX equals curated by construction, not by measurement ***"
        )


def _warn_if_not_green(corpus_validation: dict[str, dict]) -> None:
    """Emit a loud, unmissable warning for any arm whose corpus is not CI-green.
    Non-fatal (a long live run should not be lost to a stray finding), but the
    signal is impossible to overlook — and the count is persisted in summary.json.
    """
    for arm_name, block in corpus_validation.items():
        if block["finding_count"]:
            print(
                f"\n*** WARNING: arm {arm_name!r} corpus is NOT CI-green — "
                f"{block['finding_count']} finding(s); scored numbers may be "
                f"corrupted. ***"
            )
            for line in block["findings"]:
                print(f"    - {line}")


def _suspect_from_corpus(corpus_root: Path, schema: str) -> frozenset[str]:
    corpus = load_corpus(corpus_root, schema=schema)
    refs: set[str] = set()
    for asset in corpus.assets:
        if not isinstance(asset, TableAsset):
            continue
        for col in asset.columns:
            if col.reliability.status is ReliabilityStatus.suspect:
                refs.add(f"{asset.physical_name}.{col.physical_name}")
                refs.add(col.physical_name)
    return frozenset(refs)


class _RefuseAllSolver:
    """Trivial solver for ``--skip-agent`` offline smoke runs (no live model):
    refuses every question so the layered arms still produce a well-formed run.

    Implements ``solve_with_meta`` so both drivers use one uniform call path
    (the return-meta contract, not a stashed attribute)."""

    def solve_with_meta(self, question: str) -> tuple[str | None, dict]:
        del question
        return None, {"refused_by": "no_model"}

    def solve(self, question: str) -> str | None:
        return self.solve_with_meta(question)[0]


def _delta(hi: float | None, lo: float | None) -> float | None:
    """``hi - lo``, or ``None`` when either side was never measured.

    Rates became ``None`` at an empty denominator so a measurement of zero could be
    told from no measurement, but the delta arithmetic here kept subtracting them
    and raised ``TypeError`` on the offline ``--skip-agent`` path, where no arm
    produces SQL and every ``decoy_touch_rate`` is ``None``. The crash landed after
    the whole run, before ``summary.json`` was written, so the run's own artifacts
    were lost — which is exactly the shape of failure the offline smoke exists to
    catch before a live run hits it.
    """
    if hi is None or lo is None:
        return None
    return hi - lo


