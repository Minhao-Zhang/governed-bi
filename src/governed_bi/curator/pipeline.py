"""Curator orchestration for the eval ladder (``baseline`` / ``curated`` / ``curated_sme``).

``build_baseline_corpus``: deterministic, DB-derivable corpus only (names,
types, sample values, naming-convention FK candidates) — no curator LLM, no
train-SQL seeding. The eval floor (arm naming: ``docs/glossary.md``).

``build_curated_corpus`` (Phase A / ``curated``): Facts profile → deterministic
train-SQL seed → deep-agent explore (all pairs + ``clarifications.jsonl`` via
``FilesystemBackend``) → validate fix pass → write.

``build_curated_corpus_with_sme`` (Phase B / ``curated_sme``): SME-answered
ledger → deep-agent ingest (same tools, ingest prompt) → validate → write.
Offline/tests may use a deterministic fold only when ``model`` is None;
mechanical ledger seeding requires explicit opt-in.
"""

from __future__ import annotations

import json
import logging
import re
import traceback
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Sequence

from ..corpus.validate import validate_corpus
from ..obs import tracing_callbacks
from .asset_bag import AssetBag
from .clarifications import (
    ClarificationRecord,
    ClarificationRecordStatus,
    clarifications_path,
    fill_clarifications_with_responder,
    load_clarifications,
    load_clarifications_with_repairs,
    quarantine_agent_answers,
    resolve_clarifications_path,
    seed_gap_clarifications,
    write_clarifications,
)
from .profile import profile_database
from .prompts import _PHASE_A_PROMPT, _PHASE_B_PROMPT
from .seed import SeedBundle, qualified_ref, seed_from_train_sql

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from ..config import Settings
    from ..corpus.schemas import TableAsset
    from ..eval.dataset import EvalItem
    from ..gateway import Gateway
    from ..gateway.connectors.base import Connector
    from .clarifications import Responder

_READ_TOOLS = frozenset({"read_corpus", "run_probe_query"})
_WRITE_TOOLS = frozenset(
    {
        "upsert_join",
        "upsert_metric",
        "upsert_term",
        "upsert_few_shot",
        "annotate_table",
        "annotate_column",
    }
)


def _render_train_batch(items: Sequence["EvalItem"], *, max_pairs: int = 40) -> str:
    lines = ["## Train (question, gold SQL, evidence) pairs — curate from these"]
    for i, item in enumerate(items[:max_pairs], 1):
        evidence = (item.evidence or "").strip()
        qid = item.question_id or f"t{i}"
        lines.append(f"{i}. id={qid} Q: {item.question}")
        if evidence:
            lines.append(f"   evidence: {evidence}")
        lines.append(f"   sql: {item.sql}")
    if len(items) > max_pairs:
        lines.append(f"... ({len(items) - max_pairs} more pairs omitted from prompt)")
    return "\n".join(lines)


def _apply_seed(bag: AssetBag, seed: SeedBundle) -> dict[str, int]:
    """Materialise seed candidates. Returns ``{joins_ok, joins_fail, metrics_ok}``."""
    joins_ok = joins_fail = metrics_ok = 0
    for j in seed.joins:
        msg = bag.propose_join(j.left_table, j.right_table, j.on, confidence=0.55)
        if msg.startswith("ok:"):
            joins_ok += 1
        else:
            joins_fail += 1
    for m in seed.metrics[:20]:
        msg = bag.propose_metric(m.name, m.base_table, m.expression, confidence=0.5)
        if msg.startswith("ok:"):
            metrics_ok += 1
    return {"joins_ok": joins_ok, "joins_fail": joins_fail, "metrics_ok": metrics_ok}


def _norm_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _fk_candidates_from_names(
    tables: Sequence["TableAsset"],
    *,
    dialect: str = "postgres",
) -> list[tuple[str, str, str]]:
    """Naming-convention FK guesses over Facts alone: no train SQL, no LLM.

    A column named ``<other>_id`` (or ``<other>Id``) that is not its own
    table's primary key is proposed as a foreign key to another table's
    primary-key column, when a table whose (normalized, singular-or-plural)
    name matches ``<other>`` exists. This is the same cheap prior a human
    skimming the catalog would form from names alone — it is the
    ``baseline`` arm's only source of relationship candidates (D5: baseline
    is deterministic-max, DB-derivable only; the train-SQL-derived
    :func:`seed_from_train_sql` joins belong to ``curated``, not here).

    Returns ``(left_table, right_table, on)`` triples of physical names.
    """
    pk_by_table: dict[str, str] = {}
    for t in tables:
        for c in t.columns:
            if c.is_unique:
                pk_by_table.setdefault(t.physical_name, c.physical_name)

    norm_table_names = {_norm_name(name): name for name in pk_by_table}

    candidates: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for t in tables:
        own_pk = pk_by_table.get(t.physical_name)
        for c in t.columns:
            if c.physical_name == own_pk:
                continue  # not a candidate for its own primary key
            m = re.match(r"^(.+?)[_]?id$", c.physical_name, re.IGNORECASE)
            if not m:
                continue
            stem = _norm_name(m.group(1))
            if not stem:
                continue
            target = (
                norm_table_names.get(stem)
                or norm_table_names.get(stem + "s")
                or (norm_table_names.get(stem[:-1]) if stem.endswith("s") else None)
            )
            if not target or target == t.physical_name:
                continue
            target_pk = pk_by_table.get(target)
            if not target_pk:
                continue
            # Through the same helper the train-SQL seeder uses, not an f-string. Raw
            # interpolation is how `Air Carriers` produced the unparseable
            # `Air Carriers.carrier_id = Carriers.CarrierID`, and this producer feeds
            # the BASELINE arm — the one rung `build_baseline_corpus` deliberately
            # never runs `validate_corpus` over, so nothing downstream would have
            # reported the malformed edge.
            on = (
                f"{qualified_ref(t.physical_name, c.physical_name, dialect=dialect)} = "
                f"{qualified_ref(target, target_pk, dialect=dialect)}"
            )
            key = (t.physical_name, target, on)
            if key in seen:
                continue
            seen.add(key)
            candidates.append(key)
    return candidates


def _apply_fk_candidates(
    bag: AssetBag, tables: Sequence["TableAsset"], *, dialect: str = "postgres"
) -> dict[str, int]:
    """Materialise naming-convention FK candidates. Low, honest confidence: an
    unverified prior, not a measured or SME-confirmed relationship."""
    ok = fail = 0
    for left, right, on in _fk_candidates_from_names(tables, dialect=dialect):
        msg = bag.propose_join(left, right, on, confidence=0.3)
        if msg.startswith("ok:"):
            ok += 1
        else:
            fail += 1
    return {"fk_candidates_ok": ok, "fk_candidates_fail": fail}


def build_baseline_corpus(
    connector: "Connector",
    schema: str,
    out_root: Path | str,
    *,
    sample_limit: int = 5,
) -> Path:
    """The ``baseline`` arm (plan D5): deterministic-max, DB-derivable only.

    Everything a script can pull from the database with **no curator LLM**:
    names, types, sample values (:func:`profile_database`'s default
    ``sample_limit``) and naming-convention FK candidates
    (:func:`_fk_candidates_from_names`). Deliberately does **not** call
    :func:`seed_from_train_sql` and proposes no few-shots — anything learned
    from the train ``(question, SQL)`` pairs belongs to ``curated``, not
    ``baseline``. Served through the same :func:`~governed_bi.eval.arms.agent_solver`
    path as every other rung.
    """
    out_root = Path(out_root)
    out_root.mkdir(parents=True, exist_ok=True)

    tables = profile_database(connector, schema=schema, sample_limit=sample_limit)
    bag = AssetBag.from_tables(schema, tables)
    fk_stats = _apply_fk_candidates(bag, tables)
    bag.write(out_root)

    _write_run_manifest(
        out_root,
        {
            "phase": "baseline",
            "schema": schema,
            "sample_limit": sample_limit,
            "fk_candidates": fk_stats,
        },
    )
    return out_root


def _empty_tool_counts() -> dict[str, Any]:
    return {
        "read": {name: 0 for name in sorted(_READ_TOOLS)},
        "write": {name: 0 for name in sorted(_WRITE_TOOLS)},
        "other": 0,
        "read_total": 0,
        "write_total": 0,
    }


def _unmeasured_tool_counts() -> dict[str, Any]:
    """Counts for an invocation that CRASHED — every total ``None``, not ``0``.

    ``_count_tool_calls`` reconstructs the tally from the returned message list, and
    :func:`_invoke_agent` nulls that list on any exception, so a crash produced a
    complete-looking dict of zeros. The agent's writes are unaffected — the write tools
    mutate the shared :class:`AssetBag` as they are called and ``bag.write`` runs after
    the ``except`` — so ``write_total: 0`` described a half-authored corpus as an
    untouched one. On the 2026-07-29 run that read as "the agent wrote nothing" for 13
    of 55 schemas, and the SME phase republished the same zero as
    ``clarifications_applied``, a reported metric.

    Zero is a measurement. This is the absence of one.
    """
    return {
        "read": {name: None for name in sorted(_READ_TOOLS)},
        "write": {name: None for name in sorted(_WRITE_TOOLS)},
        "other": None,
        "read_total": None,
        "write_total": None,
        # Named in the artifact so a reader does not have to infer why the totals are
        # null, and so `None` cannot be mistaken for an old manifest that lacked them.
        "unmeasured_reason": "agent invocation raised; counts cannot be reconstructed",
    }


def _count_tool_calls(result: Any) -> dict[str, Any]:
    """Tally domain tool calls, split into read vs write."""
    counts = _empty_tool_counts()
    messages = []
    if isinstance(result, dict):
        messages = result.get("messages") or []
    for msg in messages:
        tool_calls = getattr(msg, "tool_calls", None) or []
        if not tool_calls and isinstance(msg, dict):
            tool_calls = msg.get("tool_calls") or []
        for tc in tool_calls:
            name = tc.get("name") if isinstance(tc, dict) else getattr(tc, "name", None)
            if name in _READ_TOOLS:
                counts["read"][name] = counts["read"].get(name, 0) + 1
                counts["read_total"] += 1
            elif name in _WRITE_TOOLS:
                counts["write"][name] = counts["write"].get(name, 0) + 1
                counts["write_total"] += 1
            elif name:
                counts["other"] += 1
    return counts


def _write_run_manifest(out_root: Path, payload: dict) -> None:
    path = out_root / "run_manifest.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _phase_a_run_manifest(curated_root: Path, schema: str) -> dict[str, Any] | None:
    """Phase-A ``run_manifest.json`` from the live root or relocated ``<schema>/_build``.

    After sidecar relocation the manifest sits under ``_build/``; before relocate
    (and during the same-process SME build) it is still at the arm root.
    """
    candidates = (
        curated_root / "run_manifest.json",
        curated_root / schema / "_build" / "run_manifest.json",
    )
    for path in candidates:
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            return data
    return None


def _write_validate_findings(out_root: Path, findings) -> None:
    path = out_root / "validate_findings.jsonl"
    with path.open("w", encoding="utf-8") as fh:
        for f in findings:
            fh.write(
                json.dumps(
                    {"code": f.code, "asset_id": f.asset_id, "message": f.message},
                    ensure_ascii=False,
                )
                + "\n"
            )


def _settings_or_load(settings: "Settings | None") -> "Settings | None":
    """The caller's Settings, or a freshly loaded one as a last resort.

    Every producer in this module stamps its run record from whatever this
    returns, so a caller that resolved a prompt set (or any other knob) must hand
    it in. Loading here is the fallback for standalone CLI use, not the norm: a
    record stamped from a re-read TOML describes a configuration the agent may
    never have run under.
    """
    if settings is not None:
        return settings
    try:
        from ..config import load_settings

        return load_settings(apply_local=False)
    except Exception:
        return None


def _invoke_agent(
    agent: Any,
    *,
    user: str,
    max_agent_steps: int,
    settings: "Settings | None" = None,
    run_id: str | None = None,
    thread_id: str | None = None,
) -> tuple[Any | None, dict[str, Any], str | None]:
    """Invoke agent; return (result, tool_counts, error_string)."""
    import time

    from ..analyst.run_log import emit_run_record, new_run_id
    from ..provenance import Producer

    result = None
    error = None
    t0 = time.perf_counter()
    rid = run_id or new_run_id()
    tid = thread_id or rid
    usage_cb = None
    cbs = tracing_callbacks(with_usage=True)
    for cb in cbs:
        if type(cb).__name__ == "UsageMetadataCallbackHandler":
            usage_cb = cb
            break
    try:
        result = agent.invoke(
            {"messages": [{"role": "user", "content": user}]},
            config={
                "recursion_limit": max(max_agent_steps * 4, 100),
                "callbacks": cbs,
                "configurable": {"thread_id": tid},
            },
        )
    except Exception as err:
        # Keep the FULL traceback, not just class + message. The bare
        # "KeyError: 'restaurant'" that lands in run_manifest.json is
        # un-diagnosable on its own (it hides which frame keyed on the schema);
        # the manifest is the only durable artifact once runs/ is swept, so the
        # frame has to be captured here or it is lost. The short form still goes
        # to stdout for a readable progress line.
        short = f"{type(err).__name__}: {err}"
        error = f"{short}\n{traceback.format_exc()}"
        print(f"deep-agent stopped early ({short})")
    settings = _settings_or_load(settings)
    if settings is not None:
        usage_list: list = []
        if usage_cb is not None:
            from ..analyst.run_log import usage_callback_entries

            usage_list = usage_callback_entries(usage_cb, source="curator")
        emit_run_record(
            settings=settings,
            producer=Producer.curator,
            run_id=rid,
            thread_id=tid,
            outcome="error" if error else "ok",
            error=error,
            token_usage=usage_list,
            t0=t0,
        )
    counts = _unmeasured_tool_counts() if error is not None else _count_tool_calls(result)
    return result, counts, error


def _validate_fix_pass(
    make_agent: "Callable[[], Any] | None",
    bag: AssetBag,
    *,
    connector: "Connector",
    out_root: Path,
    max_agent_steps: int,
) -> tuple[list, dict[str, Any], str | None]:
    """Run validate_corpus; deterministically repair what we can; then optionally
    one agent fix pass for whatever survives. Returns findings + counts.

    ``make_agent`` is a factory (not a prebuilt agent): the fix-pass gets a
    *fresh* agent so it never shares mutable state — notably the filesystem
    backend — with the fold invoke that ran before it. The shared corpus lives
    in ``bag``, which is passed explicitly; nothing else should carry across.
    """
    # Reference integrity is machine-fixable — repair coercible references
    # (term bindings, column.references, metric.base_table, join endpoints,
    # rule.scope) in code before spending (and risking a crash on) a stochastic
    # agent pass.
    repaired = bag.repair_references()
    if repaired:
        print(f"fix-pass: repaired {repaired} dangling reference(s) deterministically")
    findings = validate_corpus(bag.all_assets(), connector=connector)
    _write_validate_findings(out_root, findings)
    fix_counts = _empty_tool_counts()
    fix_error = None
    if findings and make_agent is not None:
        summary = "\n".join(f"- {f.code} [{f.asset_id}]: {f.message}" for f in findings[:40])
        user = (
            "validate_corpus reported the following findings. Fix them with the "
            f"write tools (do not edit clarifications.jsonl unless needed):\n{summary}"
        )
        _result, fix_counts, fix_error = _invoke_agent(
            make_agent(), user=user, max_agent_steps=max(max_agent_steps // 2, 8)
        )
        findings = validate_corpus(bag.all_assets(), connector=connector)
        _write_validate_findings(out_root, findings)
    return findings, fix_counts, fix_error


def _run_adversary_signal(
    bag: AssetBag, *, connector: "Connector", out_root: Path
) -> list[dict]:
    """Structural adversary: record findings, soft confidence penalties, then gate.

    Soft heuristic codes (missing provenance, FK-without-ref) only discount
    confidence. Hard findings (dangling refs, bad ids, missing physical tables,
    and every other ``validate_corpus`` code) raise
    :class:`~governed_bi.curator.adversary.StructuralGateError` so the caller
    must not ``bag.write`` — fail closed.
    """
    from .adversary import SOFT_ADVERSARY_CODES, gate_hard_findings, review

    findings = review(bag.all_assets(), connector=connector)
    records = [
        {"code": f.code, "asset_id": f.asset_id, "message": f.message} for f in findings
    ]
    path = out_root / "adversary_findings.jsonl"
    with path.open("w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    by_id: dict[str, list[str]] = {}
    for f in findings:
        if f.asset_id:
            by_id.setdefault(f.asset_id, []).append(f"{f.code}: {f.message}")

    for asset_id, notes in by_id.items():
        for name, table in list(bag.tables.items()):
            if table.id != asset_id:
                continue
            audit = table.audit
            from ..corpus.schemas import Audit, Provenance, ProvenanceSource, ProvenanceStatus

            if audit is None:
                audit = Audit(
                    provenance=Provenance(
                        source=ProvenanceSource.curator,
                        status=ProvenanceStatus.proposed,
                    )
                )
            data = audit.model_dump(mode="python")
            data["adversary_findings"] = notes
            new_audit = Audit.model_validate(data)
            conf = table.confidence
            # Soft notes only: hard findings block write, so a penalty is moot,
            # but we still record them on the audit trail above.
            soft_n = sum(1 for n in notes if n.split(":", 1)[0] in SOFT_ADVERSARY_CODES)
            if conf is not None and soft_n:
                conf = max(0.0, float(conf) - 0.1 * soft_n)
            bag.tables[name] = table.model_copy(
                update={"audit": new_audit, "confidence": conf}
            )
        for store in (bag.joins, bag.metrics, bag.terms, bag.few_shots):
            if asset_id not in store:
                continue
            asset = store[asset_id]
            audit = asset.audit
            from ..corpus.schemas import Audit, Provenance, ProvenanceSource, ProvenanceStatus

            if audit is None:
                audit = Audit(
                    provenance=Provenance(
                        source=ProvenanceSource.curator,
                        status=ProvenanceStatus.proposed,
                    )
                )
            data = audit.model_dump(mode="python")
            data["adversary_findings"] = notes
            new_audit = Audit.model_validate(data)
            conf = getattr(asset, "confidence", None)
            updates: dict = {"audit": new_audit}
            soft_n = sum(1 for n in notes if n.split(":", 1)[0] in SOFT_ADVERSARY_CODES)
            if conf is not None and soft_n:
                updates["confidence"] = max(0.0, float(conf) - 0.1 * soft_n)
            store[asset_id] = asset.model_copy(update=updates)

    gate_hard_findings(findings)
    return records


def _corpora_differ(curated_root: Path, curated_sme_root: Path, schema: str) -> bool:
    """True when curated_sme is not a byte-identical copy of curated (curated_sme acceptance)."""
    import hashlib

    def _fingerprint(root: Path) -> str:
        h = hashlib.sha256()
        base = root / schema
        if not base.is_dir():
            return ""
        for path in sorted(base.rglob("*.yaml")):
            h.update(path.relative_to(base).as_posix().encode())
            h.update(path.read_bytes())
        return h.hexdigest()

    return _fingerprint(curated_root) != _fingerprint(curated_sme_root)


# `_mark_columns_absent_from_gold` used to live here: a mask that stamped every
# column no train gold SQL referenced as suspect. It is gone, and it should not come
# back. "BIRD never queried this column" is not evidence the column is unreliable,
# and where the gold SQL was defective the mask was actively wrong — it banned
# columns the generator needed. Reliability is now authored: the curator agent marks
# suspect columns with `annotate_column(suspect=True, ...)` after sweeping the
# schema, and an SME answer that disowns a column folds into the same mark
# (`AssetBag.mark_unrecognised_columns`). `governance.excluded` stays human-only.


def _write_sme_clarifications_log(
    records: Sequence[ClarificationRecord],
    out_root: Path,
    *,
    schema: str,
    tables: Sequence | None = None,
) -> int:
    """Durable audit log of the SME clarification round-trip (ledger shape)."""
    by_name = {t.physical_name: t for t in (tables or [])}
    path = out_root / "sme_clarifications.jsonl"
    rows = []
    for rec in records:
        table = None
        column = None
        table_id = None
        if rec.scope.startswith("table:"):
            rest = rec.scope[len("table:") :]
            if "." in rest:
                table, column = rest.split(".", 1)
            else:
                table = rest
            if table in by_name:
                table_id = by_name[table].id
        rows.append(
            {
                "schema": schema,
                "table_id": table_id,
                "table": table,
                "column": column,
                "question": rec.question,
                "answer": rec.answer,
                "answered_by": rec.answered_by,
                "asked_by": ",".join(rec.raised_by) if rec.raised_by else None,
                "status": rec.status.value,
                "at": None,
                "id": rec.id,
                "scope": rec.scope,
            }
        )
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return len(rows)


def build_curated_corpus(
    connector: "Connector",
    gateway: "Gateway",
    schema: str,
    train_items: Sequence["EvalItem"],
    out_root: Path | str,
    *,
    model: Any | None = None,
    dialect: str = "postgres",
    max_agent_steps: int = 25,
    run_agent: bool = True,
    system_prompt: str | None = None,
    settings: "Settings | None" = None,
) -> Path:
    """Phase A: profile → seed → explore agent → validate → write curated corpus.

    Does **not** pre-create ``clarifications.jsonl`` — the agent must
    ``write_file`` it (FilesystemBackend rejects write-to-existing). An empty
    missing ledger after Phase A is visible in the manifest
    (``clarification_count: 0``, ``ledger_source: missing``).

    ``system_prompt`` injects a registered ``curator_phase_a`` variant; ``None``
    keeps ``v1``. A caller that stamps a prompt set on the run **must** pass the
    resolved text — a corpus built under one prompt and recorded under another is
    the attribution failure this whole mechanism exists to prevent.

    Pass ``settings`` alongside it. The run record this build emits is stamped from
    ``settings``, so re-deriving config here would record the corpus under the
    TOML's prompt set while the agent ran on the caller's — the same mismatch, one
    layer down, and invisible because both halves look internally consistent.
    """
    out_root = Path(out_root)
    out_root.mkdir(parents=True, exist_ok=True)

    tables = profile_database(connector, schema=schema)
    bag = AssetBag.from_tables(schema, tables)
    seed = seed_from_train_sql([it.sql for it in train_items], dialect=dialect)
    seed_stats = _apply_seed(bag, seed)
    if seed_stats["joins_fail"]:
        print(
            f"seed: {seed_stats['joins_ok']} joins applied, "
            f"{seed_stats['joins_fail']} failed lookup (check alias resolution)"
        )
    # No deterministic suspect marking happens here any more (see the note where
    # `_mark_columns_absent_from_gold` used to be, above). Between this point and the
    # agent pass the corpus carries zero suspect columns, so the curated arm's decoy
    # defence is entirely whatever the agent authors.
    tool_counts = _empty_tool_counts()
    fix_counts = _empty_tool_counts()
    agent_error: str | None = None
    fix_error: str | None = None
    make_agent: "Callable[[], Any] | None" = None
    agent_ran = False

    if run_agent and model is not None:
        from ..analyst.run_log import new_run_id
        from .deep_agent import build_curator_agent

        _settings = _settings_or_load(settings)
        _run_id = new_run_id()
        _thread_id = f"curator:{schema}:{out_root.name}"
        # No checkpointer. deepagents requires one only for `interrupt_on`, and the
        # curator sets none — it invokes once and never resumes. The sqlite saver it
        # used to create was written, closed, relocated and never read back by
        # anything: the harness's own `--resume` decides from existing YAML, and the
        # fix pass mints a fresh thread rather than continuing this one.
        #
        # It was not free. An open sqlite handle is unmovable on Windows, which
        # aborted every curated build and would have ended a paid run with
        # "every db failed to build"; the fixes for that (release-in-finally,
        # copy fallback, promotion exemption) all exist to serve a file nothing reads.
        # Removing it deletes that whole class of failure rather than guarding it.

        def make_agent() -> Any:  # fresh agent per invoke — no shared fs/state
            return build_curator_agent(
                model,
                connector=connector,
                schema=schema,
                gateway=gateway,
                bag=bag,
                run_dir=out_root,
                system_prompt=system_prompt or _PHASE_A_PROMPT,
            )

        agent_ran = True
        user = "\n\n".join(
            [
                f"Curate schema `{schema}`. Work pair-by-pair; persist via tools.",
                seed.render(),
                _render_train_batch(train_items),
                "Create /clarifications.jsonl for genuine unknowns "
                "(write_file on first create; grep before add; edit_file to broaden/merge).",
                "Mark unreliable or misleading columns suspect. Propose at least the verified seed joins.",
                "Stop once pairs are covered, seed joins verified, and obviously unreliable columns marked.",
            ]
        )
        _result, tool_counts, agent_error = _invoke_agent(
            make_agent(),
            user=user,
            max_agent_steps=max_agent_steps,
            settings=_settings,
            run_id=_run_id,
            thread_id=_thread_id,
        )

    findings, fix_counts, fix_error = _validate_fix_pass(
        make_agent if agent_ran else None,
        bag,
        connector=connector,
        out_root=out_root,
        max_agent_steps=max_agent_steps,
    )
    _run_adversary_signal(bag, connector=connector, out_root=out_root)
    bag.write(out_root)

    ledger, ledger_repairs = load_clarifications_with_repairs(clarifications_path(out_root))
    if ledger_repairs:
        logger.warning(
            "curator ledger for %s needed %d repair(s): %s",
            schema,
            len(ledger_repairs),
            "; ".join(ledger_repairs),
        )
    if clarifications_path(out_root).exists():
        ledger_source = "agent" if agent_ran else "preexisting"
    else:
        ledger_source = "missing"

    # The agent owns this file, so it can pre-answer its own questions and mint a
    # certified "human" fact (AUDIT C6). Strip that here, at the Phase A boundary,
    # and rewrite the artifact so nothing downstream can read the forged form.
    forged_answers: list[str] = []
    if ledger and ledger_source == "agent":
        ledger, forged_answers = quarantine_agent_answers(ledger)
        # Rewritten when the loader repaired anything too, not only on a forged
        # answer. A repair that stays in memory leaves the malformed line on disk for
        # the SME arm and every later resume to re-repair — and for any future strict
        # reader to die on. Write the normalised form once, here, where the artifact
        # is still owned.
        if forged_answers or ledger_repairs:
            write_clarifications(clarifications_path(out_root), ledger)
        if forged_answers:
            logger.warning(
                "curator agent pre-answered %d clarification(s) in %s; reset to open: %s",
                len(forged_answers),
                schema,
                ", ".join(forged_answers),
            )

    _write_run_manifest(
        out_root,
        {
            "phase": "A",
            "schema": schema,
            "agent_ran": agent_ran,
            "ledger_source": ledger_source,
            "clarification_count": len(ledger),
            # Non-empty means the agent tried to answer its own questions (AUDIT C6).
            "agent_forged_answers": forged_answers,
            # Non-empty means the agent wrote a record this loader had to normalise
            # (an out-of-enum status, an undeclared key). Recorded because the
            # alternative reading — a clean ledger — is indistinguishable otherwise,
            # and because the rate is what says whether the prompt needs the schema
            # spelled out more plainly.
            "ledger_repairs": ledger_repairs,
            "seed": seed_stats,
            # Successor to the deleted `decoy_defense` block: every suspect column in
            # this corpus is now agent-authored, so one count is the whole story. Zero
            # means the curated arm went out with no decoy defence at all.
            "suspect_columns": bag.suspect_count(),
            "tool_calls": tool_counts,
            "fix_pass_tool_calls": fix_counts,
            "error": agent_error,
            "fix_pass_error": fix_error,
            "validate_finding_count": len(findings),
            # Relative to the corpus root, not absolute. An absolute path embeds the
            # run's own output directory, so it was the one field that differed
            # between two otherwise byte-identical builds — enough to make
            # "did these two runs produce the same corpus?" un-answerable with a
            # plain diff, and it leaks a machine-local path into a durable artifact.
            "clarifications_path": clarifications_path(out_root).name,
        },
    )
    return out_root


def build_curated_corpus_with_sme(
    connector: "Connector",
    gateway: "Gateway",
    schema: str,
    train_items: Sequence["EvalItem"],
    out_root: Path | str,
    *,
    responder: "Responder",
    curated_root: Path | str | None = None,
    model: Any | None = None,
    dialect: str = "postgres",
    max_agent_steps: int = 15,
    run_agent_repass: bool | None = None,
    seed_ledger_if_empty: bool = False,
    system_prompt: str | None = None,
    phase_a_system_prompt: str | None = None,
    settings: "Settings | None" = None,
) -> Path:
    """Phase B: answered clarifications ledger → ingest → write curated_sme corpus.

    Requires an agent-authored (or explicitly planted) open ledger. Mechanical
    ``seed_gap_clarifications`` runs **only** when ``seed_ledger_if_empty=True``
    (opt-in for ``--skip-agent``); the default path raises if the ledger is empty.

    When ``model`` is set, ``run_agent_repass`` defaults to True and the ingest
    agent folds answers (no silent deterministic fold). When ``model`` is None,
    a deterministic scope-based fold is used for offline tests.

    ``system_prompt`` injects a registered ``curator_phase_b`` variant;
    ``phase_a_system_prompt`` is forwarded to the Phase-A build this function may
    run for itself when no ``curated_root`` is supplied. Both ``None`` keep ``v1``.
    ``settings`` is what this build's run records are stamped from, and is
    forwarded to that Phase-A build for the same reason (see
    :func:`build_curated_corpus`).
    """
    from ..corpus.loader import load_corpus
    from ..corpus.schemas import TableAsset

    out_root = Path(out_root)
    out_root.mkdir(parents=True, exist_ok=True)

    if run_agent_repass is None:
        run_agent_repass = model is not None

    if curated_root is None:
        curated_root = out_root.parent / "corpus_curated"
        build_curated_corpus(
            connector,
            gateway,
            schema,
            train_items,
            curated_root,
            model=model,
            dialect=dialect,
            max_agent_steps=max_agent_steps,
            run_agent=model is not None,
            system_prompt=phase_a_system_prompt,
            settings=settings,
        )
    curated_root = Path(curated_root)

    corpus = load_corpus(curated_root, schema=schema)
    tables = [a for a in corpus.assets if isinstance(a, TableAsset)]
    other = [a for a in corpus.assets if not isinstance(a, TableAsset)]

    ledger_path = resolve_clarifications_path(curated_root, schema)
    phase_a = _phase_a_run_manifest(curated_root, schema)
    ledger_was_written = (
        phase_a is not None
        and phase_a.get("ledger_source") not in (None, "missing")
    )
    if ledger_was_written and ledger_path is None:
        raise RuntimeError(
            f"curated clarifications ledger for {schema!r} was recorded "
            f"(ledger_source={phase_a.get('ledger_source')!r}, "
            f"clarification_count={phase_a.get('clarification_count')!r}) but is "
            f"absent from both {clarifications_path(curated_root)} and "
            f"{curated_root / schema / '_build' / 'clarifications.jsonl'}; "
            "refusing to continue with a missing relocated ledger"
        )

    records = load_clarifications(ledger_path) if ledger_path is not None else []
    open_records = [r for r in records if r.status is ClarificationRecordStatus.open]
    ledger_source = "agent" if open_records else "missing"

    if not open_records and seed_ledger_if_empty and ledger_path is None:
        # Offline/--skip-agent scaffolding only: synthesize gap questions so the
        # deterministic fold has something to do.
        #
        # Never invent a ledger when a real one was resolved from the live root or
        # relocated ``<schema>/_build/`` — including an all-answered file. That is
        # the cross-resume failure: looking only at the live root made the relocated
        # ledger look absent and this scaffolding synthesised a misleading fold.
        #
        # Written to THIS arm's root, not to ``curated_root``. Seeding the shared input
        # made the *next* SME arm read those synthetic records as if the curator agent
        # had raised them: with the answer write-back removed they stay open, so
        # ``curated_sme`` found open records and stamped ``ledger_source="agent"`` — the
        # one field whose job is telling agent-authored clarifications from mechanically
        # seeded ones, lying on exactly the rung the write-back fix was repairing. Both
        # arms now report ``seed_gap`` and both still fold.
        records = seed_gap_clarifications(tables)
        # Written now, before the responder runs, and truncated by the answered write
        # further down — so in a successful build this line leaves no trace. It exists for
        # the failing build: if the responder raises (a rate limit, a dead gateway), the
        # arm root is left holding the questions that were pending, which is the only
        # record of what this build was trying to do. Without it a crashed SME build
        # leaves an arm root with no ledger at all, indistinguishable from one that never
        # got that far.
        write_clarifications(clarifications_path(out_root), records)
        open_records = [
            r for r in records if r.status is ClarificationRecordStatus.open
        ]
        ledger_source = "seed_gap"
        if not open_records:
            raise RuntimeError("seed_ledger_if_empty produced no open clarifications")
    # An empty ledger from a real agent run is NOT a failure: the agent resolved
    # everything itself, so the SME round-trip has nothing to fold and curated_sme == curated.
    # A true agent no-op is distinguishable via the Phase-A manifest's write_total.
    # Paid path with a *required* (recorded) ledger absent already raised above.

    answered = fill_clarifications_with_responder(records, responder)
    # Written to THIS arm's root only. It used to also write back into
    # ``curated_root``'s ledger, which is this build's *input* — and that voided the
    # arm of any *second* SME build over the same curated root.
    #
    # The sequence: SME build A reads ``curated``'s ledger, finds its open records,
    # answers them, and marks them answered *in curated's ledger*. Build B then reads
    # the same ledger, finds nothing open, records ``ledger_source="missing"``, folds
    # nothing, and produces a corpus identical to ``curated``. It is caught downstream
    # — ``_sme_fold_signal`` -> ``sme_noop_dbs`` -> unquotable — but only after paying
    # for the whole build. Reachable by a resume, a ``--replicate curated_sme``, or a
    # future second SME arm.
    #
    # Harmless to what either arm *serves*: the corpus loader never reads
    # ``clarifications.jsonl`` and ``_corpora_differ`` fingerprints ``*.yaml`` only. The
    # damage was confined to the ledger, which is precisely where the next arm looks.
    write_clarifications(clarifications_path(out_root), answered)
    _write_sme_clarifications_log(answered, out_root, schema=schema, tables=tables)

    bag = AssetBag.from_tables(schema, tables)
    for asset in other:
        if asset.asset_type == "join":
            bag.joins[asset.id] = asset  # type: ignore[assignment]
        elif asset.asset_type == "metric":
            bag.metrics[asset.id] = asset  # type: ignore[assignment]
        elif asset.asset_type == "term":
            bag.terms[asset.id] = asset  # type: ignore[assignment]
        elif asset.asset_type == "few_shot":
            bag.few_shots[asset.id] = asset  # type: ignore[assignment]

    tool_counts = _empty_tool_counts()
    fix_counts = _empty_tool_counts()
    agent_error: str | None = None
    fix_error: str | None = None
    make_agent: "Callable[[], Any] | None" = None
    agent_ran = False
    applied = 0
    fold_mode = "none"

    if not open_records:
        fold_mode = "none"  # no clarifications → nothing to fold; curated_sme == curated
    elif run_agent_repass and model is not None:
        from ..analyst.run_log import new_run_id
        from .deep_agent import build_curator_agent

        _settings = _settings_or_load(settings)
        _run_id = new_run_id()
        _thread_id = f"curator-sme:{schema}:{out_root.name}"

        def make_agent() -> Any:  # fresh agent per invoke — no shared fs/state
            return build_curator_agent(
                model,
                connector=connector,
                schema=schema,
                gateway=gateway,
                bag=bag,
                run_dir=out_root,
                system_prompt=system_prompt or _PHASE_B_PROMPT,
            )

        agent_ran = True
        fold_mode = "agent"
        user = (
            f"Ingest answered clarifications for schema `{schema}`. "
            "Read /clarifications.jsonl and fold each answered record into the "
            "corpus via annotate/upsert tools (curator/proposed provenance only)."
        )
        _result, tool_counts, agent_error = _invoke_agent(
            make_agent(),
            user=user,
            max_agent_steps=max_agent_steps,
            settings=_settings,
            run_id=_run_id,
            thread_id=_thread_id,
        )
        # Count successful writes via tool totals; unanswered leftovers are NOT
        # folded — agent owns the fold.
        applied = tool_counts["write_total"]
    else:
        fold_mode = "deterministic"
        applied = bag.apply_answered_clarifications(answered)

    # An SME who says they do not recognise a column has delivered a reliability
    # verdict, and neither fold mode reliably records it as one: the deterministic
    # fold writes prose into the description, and the agent fold is asked to mark the
    # column but may not. Runs for both modes, after them, so the mark lands on top
    # of whatever description they wrote.
    unrecognised = bag.mark_unrecognised_columns(answered)
    if unrecognised["no_column_in_scope"]:
        print(
            f"sme fold: {unrecognised['no_column_in_scope']} unrecognised-column "
            "answer(s) had no column in scope — recorded as notes only"
        )

    # pair:/query:-scoped answers (trap / annotation-error findings) don't map to a
    # table/column asset, so the fold above skips them. Land them as governance
    # rules so the caveat reaches the served corpus instead of dying in the ledger.
    caveats_recorded = bag.record_caveats(answered)

    findings, fix_counts, fix_error = _validate_fix_pass(
        make_agent if agent_ran else None,
        bag,
        connector=connector,
        out_root=out_root,
        max_agent_steps=max_agent_steps,
    )
    # Phase B has no separate soft-adversary pass; validate findings are all hard.
    from .adversary import gate_hard_findings

    gate_hard_findings(findings)
    bag.write(out_root)

    _write_run_manifest(
        out_root,
        {
            "phase": "B",
            "schema": schema,
            "agent_ran": agent_ran,
            "ledger_source": ledger_source,
            "fold_mode": fold_mode,
            "clarifications_applied": applied,
            "caveats_recorded": caveats_recorded,
            "unrecognised_column_marks": unrecognised,
            "suspect_columns": bag.suspect_count(),
            "clarification_count": len(answered),
            "tool_calls": tool_counts,
            "fix_pass_tool_calls": fix_counts,
            "error": agent_error,
            "fix_pass_error": fix_error,
            "validate_finding_count": len(findings),
        },
    )

    if open_records and not _corpora_differ(curated_root, out_root, schema):
        raise RuntimeError(
            f"curated_sme corpus is identical to curated at {out_root}; SME round-trip produced no edits"
        )
    return out_root
