"""Curator orchestration for the eval ladder (``baseline`` / ``curated`` / ``curated_sme``).

``build_baseline_corpus``: deterministic, DB-derivable corpus only (names,
types, sample values, naming-convention FK candidates) — no curator LLM, no
train-SQL seeding. The eval floor (plan: ``docs/plans/terminology-refactor.md``).

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
    seed_gap_clarifications,
    write_clarifications,
)
from .profile import profile_database
from .prompts import _PHASE_A_PROMPT, _PHASE_B_PROMPT
from .seed import SeedBundle, seed_from_train_sql

if TYPE_CHECKING:
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
            on = f"{t.physical_name}.{c.physical_name} = {target}.{target_pk}"
            key = (t.physical_name, target, on)
            if key in seen:
                continue
            seen.add(key)
            candidates.append(key)
    return candidates


def _apply_fk_candidates(bag: AssetBag, tables: Sequence["TableAsset"]) -> dict[str, int]:
    """Materialise naming-convention FK candidates. Low, honest confidence: an
    unverified prior, not a measured or SME-confirmed relationship."""
    ok = fail = 0
    for left, right, on in _fk_candidates_from_names(tables):
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
    if settings is None:
        try:
            from ..config import load_settings

            settings = load_settings(apply_local=False)
        except Exception:
            settings = None
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
    return result, _count_tool_calls(result), error


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
    """Structural adversary as a *signal* (design §1): record findings, never gate."""
    from .adversary import review

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
            if conf is not None:
                conf = max(0.0, float(conf) - 0.1 * len(notes))
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
            if conf is not None:
                updates["confidence"] = max(0.0, float(conf) - 0.1 * len(notes))
            store[asset_id] = asset.model_copy(update=updates)
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


def _mark_columns_absent_from_gold(
    bag: AssetBag, sqls: Sequence[str], *, dialect: str = "postgres"
) -> int:
    """Heuristic decoy defense: columns never referenced by train gold SQL."""
    import sqlglot
    from sqlglot import exp

    referenced: set[str] = set()
    for sql in sqls:
        try:
            tree = sqlglot.parse_one(sql, read=dialect)
        except sqlglot.errors.SqlglotError:
            continue  # unparseable gold SQL is tolerated; a non-parse bug is not
        for col in tree.find_all(exp.Column):
            referenced.add(col.name.lower())

    marked = 0
    for table in list(bag.tables.values()):
        for col in table.columns:
            if col.physical_name.lower() in referenced:
                continue
            if col.is_unique:
                continue
            before = bag.suspect_count()
            bag.mark_column_suspect(
                table.physical_name,
                col.physical_name,
                note="DO NOT USE — never referenced by working train SQL (likely unreliable)",
            )
            if bag.suspect_count() > before:
                marked += 1
    return marked


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
) -> Path:
    """Phase A: profile → seed → explore agent → validate → write curated corpus.

    Does **not** pre-create ``clarifications.jsonl`` — the agent must
    ``write_file`` it (FilesystemBackend rejects write-to-existing). An empty
    missing ledger after Phase A is visible in the manifest
    (``clarification_count: 0``, ``ledger_source: missing``).
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
    _mark_columns_absent_from_gold(bag, [it.sql for it in train_items], dialect=dialect)

    tool_counts = _empty_tool_counts()
    fix_counts = _empty_tool_counts()
    agent_error: str | None = None
    fix_error: str | None = None
    make_agent: "Callable[[], Any] | None" = None
    agent_ran = False

    if run_agent and model is not None:
        from ..analyst.run_log import make_durable_checkpointer, new_run_id
        from ..config import load_settings
        from .deep_agent import build_curator_agent

        try:
            _settings = load_settings(apply_local=False)
        except Exception:
            _settings = None
        _run_id = new_run_id()
        _thread_id = f"curator:{schema}:{out_root.name}"
        _ckpt = None
        if _settings is not None:
            try:
                _ckpt = make_durable_checkpointer(
                    _settings,
                    path=str(Path(out_root) / "agent_checkpoints.sqlite"),
                )
            except Exception:  # degrade; a checkpointer fault must not crash curation
                _ckpt = None

        def make_agent() -> Any:  # fresh agent per invoke — no shared fs/state
            return build_curator_agent(
                model,
                connector=connector,
                schema=schema,
                gateway=gateway,
                bag=bag,
                run_dir=out_root,
                system_prompt=_PHASE_A_PROMPT,
                checkpointer=_ckpt,
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

    ledger = load_clarifications(clarifications_path(out_root))
    if clarifications_path(out_root).exists():
        ledger_source = "agent" if agent_ran else "preexisting"
    else:
        ledger_source = "missing"

    _write_run_manifest(
        out_root,
        {
            "phase": "A",
            "schema": schema,
            "agent_ran": agent_ran,
            "ledger_source": ledger_source,
            "clarification_count": len(ledger),
            "seed": seed_stats,
            "tool_calls": tool_counts,
            "fix_pass_tool_calls": fix_counts,
            "error": agent_error,
            "fix_pass_error": fix_error,
            "validate_finding_count": len(findings),
            "clarifications_path": str(clarifications_path(out_root)),
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
) -> Path:
    """Phase B: answered clarifications ledger → ingest → write curated_sme corpus.

    Requires an agent-authored (or explicitly planted) open ledger. Mechanical
    ``seed_gap_clarifications`` runs **only** when ``seed_ledger_if_empty=True``
    (opt-in for ``--skip-agent``); the default path raises if the ledger is empty.

    When ``model`` is set, ``run_agent_repass`` defaults to True and the ingest
    agent folds answers (no silent deterministic fold). When ``model`` is None,
    a deterministic scope-based fold is used for offline tests.
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
        )
    curated_root = Path(curated_root)

    corpus = load_corpus(curated_root, schema=schema)
    tables = [a for a in corpus.assets if isinstance(a, TableAsset)]
    other = [a for a in corpus.assets if not isinstance(a, TableAsset)]

    ledger_path = clarifications_path(curated_root)
    records = load_clarifications(ledger_path)
    open_records = [r for r in records if r.status is ClarificationRecordStatus.open]
    ledger_source = "agent" if open_records else "missing"

    if not open_records and seed_ledger_if_empty:
        # Offline/--skip-agent scaffolding only: synthesize gap questions so the
        # deterministic fold has something to do.
        records = seed_gap_clarifications(tables)
        write_clarifications(ledger_path, records)
        open_records = [
            r for r in records if r.status is ClarificationRecordStatus.open
        ]
        ledger_source = "seed_gap"
        if not open_records:
            raise RuntimeError("seed_ledger_if_empty produced no open clarifications")
    # An empty ledger from a real agent run is NOT a failure: the agent resolved
    # everything itself, so the SME round-trip has nothing to fold and curated_sme == curated.
    # A true agent no-op is distinguishable via the Phase-A manifest's write_total.

    answered = fill_clarifications_with_responder(records, responder)
    write_clarifications(ledger_path, answered)
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
        from ..analyst.run_log import make_durable_checkpointer, new_run_id
        from ..config import load_settings
        from .deep_agent import build_curator_agent

        try:
            _settings = load_settings(apply_local=False)
        except Exception:
            _settings = None
        _run_id = new_run_id()
        _thread_id = f"curator-sme:{schema}:{out_root.name}"
        _ckpt = None
        if _settings is not None:
            try:
                _ckpt = make_durable_checkpointer(
                    _settings,
                    path=str(Path(out_root) / "agent_checkpoints.sqlite"),
                )
            except Exception:  # degrade; a checkpointer fault must not crash curation
                _ckpt = None

        def make_agent() -> Any:  # fresh agent per invoke — no shared fs/state
            return build_curator_agent(
                model,
                connector=connector,
                schema=schema,
                gateway=gateway,
                bag=bag,
                run_dir=out_root,
                system_prompt=_PHASE_B_PROMPT,
                certified_writes=True,
                checkpointer=_ckpt,
            )

        agent_ran = True
        fold_mode = "agent"
        user = (
            f"Ingest answered clarifications for schema `{schema}`. "
            "Read /clarifications.jsonl and fold each answered record into the "
            "corpus via annotate/upsert tools with certified=true."
        )
        _result, tool_counts, agent_error = _invoke_agent(
            make_agent(),
            user=user,
            max_agent_steps=max_agent_steps,
            settings=_settings,
            run_id=_run_id,
            thread_id=_thread_id,
        )
        # Count successful certified writes via tool totals; also apply any
        # unanswered leftovers is NOT done — agent owns the fold.
        applied = tool_counts["write_total"]
    else:
        fold_mode = "deterministic"
        applied = bag.apply_answered_clarifications(answered)

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
