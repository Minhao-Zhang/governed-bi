"""Curator orchestration for the three-arm experiment (A2 / A3).

Chains Facts profile → deterministic seed → deep-agent curation → write_corpus
into a fresh output directory (never overwrites an existing BIRD-corpus tree).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Sequence

from .asset_bag import AssetBag
from .clarify_loop import emit_clarifications, resolve_clarifications
from .profile import profile_database
from .seed import SeedBundle, seed_from_train_sql

if TYPE_CHECKING:
    from ..eval.dataset import EvalItem
    from ..gateway import Gateway
    from ..gateway.connectors.base import Connector
    from .clarify_loop import Responder


def _render_train_batch(items: Sequence["EvalItem"], *, max_pairs: int = 40) -> str:
    lines = ["## Train (question, gold SQL, evidence) pairs — curate from these"]
    for i, item in enumerate(items[:max_pairs], 1):
        evidence = (item.evidence or "").strip()
        lines.append(f"{i}. Q: {item.question}")
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


def _run_adversary_signal(
    bag: AssetBag, *, connector: "Connector", out_root: Path
) -> list[dict]:
    """Structural adversary as a *signal* (design §1): record findings, never gate.

    Writes ``adversary_findings.jsonl`` under ``out_root`` and stamps each
    finding onto the matching asset's Audit as free-form evidence. Assets are
    never dropped; confidence is gently reduced when a finding names them.
    """
    import json

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
        # Tables
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
            # Audit.extra="allow" — attach adversary signal without schema change.
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


def _corpora_differ(a2_root: Path, a3_root: Path, schema: str) -> bool:
    """True when A3 is not a byte-identical copy of A2 (A3 acceptance)."""
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

    return _fingerprint(a2_root) != _fingerprint(a3_root)


def _mark_columns_absent_from_gold(
    bag: AssetBag, sqls: Sequence[str], *, dialect: str = "postgres"
) -> int:
    """Heuristic decoy defense: columns never referenced by train gold SQL.

    Train gold never touches decoys/traps, so catalog columns absent from all
    train SQL are strong suspect candidates. Returns how many were newly marked.
    """
    import sqlglot
    from sqlglot import exp

    referenced: set[str] = set()
    for sql in sqls:
        try:
            tree = sqlglot.parse_one(sql, read=dialect)
        except Exception:
            continue
        for col in tree.find_all(exp.Column):
            referenced.add(col.name.lower())

    marked = 0
    for table in list(bag.tables.values()):
        for col in table.columns:
            if col.physical_name.lower() in referenced:
                continue
            # Keep primary-key-ish columns; do not mark unique keys as decoys.
            if col.is_unique:
                continue
            before = bag.suspect_count()
            bag.mark_column_suspect(
                table.physical_name,
                col.physical_name,
                note="DO NOT USE — never referenced by working train SQL (likely decoy)",
            )
            if bag.suspect_count() > before:
                marked += 1
    return marked


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
    """Profile → seed → (optional) deep-agent → write A2 corpus under ``out_root``.

    Returns the corpus root path written (``out_root`` itself; assets land in
    ``out_root/<schema>/``). When ``run_agent`` is False or ``model`` is None,
    only the deterministic seed + absent-from-gold suspect pass run (useful for
    offline tests and resume).
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

    if run_agent and model is not None:
        from .deep_agent import build_curator_agent

        agent = build_curator_agent(
            model, connector=connector, schema=schema, gateway=gateway, bag=bag
        )
        user = "\n\n".join(
            [
                f"Curate schema `{schema}`. Persist surviving Inference assets via tools.",
                seed.render(),
                _render_train_batch(train_items),
                "Mark decoy/trap columns suspect. Propose at least the verified seed joins.",
                "Stop once you have verified the seed joins and marked obvious decoys.",
            ]
        )
        try:
            agent.invoke(
                {"messages": [{"role": "user", "content": user}]},
                config={"recursion_limit": max(max_agent_steps * 4, 100)},
            )
        except Exception as err:
            # Persist whatever the agent wrote before hitting a step/recursion
            # ceiling; seed + suspect pass already ground the corpus.
            print(f"deep-agent stopped early ({type(err).__name__}: {err})")

    # Adversary as signal (not a gate): findings recorded, assets kept.
    _run_adversary_signal(bag, connector=connector, out_root=out_root)

    bag.write(out_root)
    return out_root


def build_curated_corpus_with_sme(
    connector: "Connector",
    gateway: "Gateway",
    schema: str,
    train_items: Sequence["EvalItem"],
    out_root: Path | str,
    *,
    responder: "Responder",
    a2_root: Path | str | None = None,
    model: Any | None = None,
    dialect: str = "postgres",
    max_agent_steps: int = 15,
    run_agent_repass: bool = False,
) -> Path:
    """A2 assets → clarifications → Simulated SME → write A3 corpus.

    When ``a2_root`` is provided, loads table assets from that corpus (plus any
    joins/metrics already written there) rather than rebuilding A2. Otherwise
    builds A2 first into a sibling ``corpus_a2`` under the parent of ``out_root``.
    """
    from ..corpus.loader import load_corpus
    from ..corpus.schemas import ClarificationStatus, TableAsset

    out_root = Path(out_root)
    out_root.mkdir(parents=True, exist_ok=True)

    if a2_root is None:
        a2_root = out_root.parent / "corpus_a2"
        build_curated_corpus(
            connector,
            gateway,
            schema,
            train_items,
            a2_root,
            model=model,
            dialect=dialect,
            max_agent_steps=max_agent_steps,
            run_agent=model is not None,
        )

    corpus = load_corpus(Path(a2_root), schema=schema)
    tables = [a for a in corpus.assets if isinstance(a, TableAsset)]
    other = [a for a in corpus.assets if not isinstance(a, TableAsset)]

    clarified = emit_clarifications(tables)

    def _has_open(tables_in) -> bool:
        for t in tables_in:
            if (
                t.audit
                and t.audit.clarification
                and t.audit.clarification.status is ClarificationStatus.open
            ):
                return True
            for c in t.columns:
                if (
                    c.audit
                    and c.audit.clarification
                    and c.audit.clarification.status is ClarificationStatus.open
                ):
                    return True
        return False

    # Guarantee at least one open clarification so A3 cannot be a no-op copy of A2
    # when the agent left every description high-confidence.
    if not _has_open(clarified):
        clarified = emit_clarifications(tables, confidence_threshold=1.01)

    resolved = resolve_clarifications(clarified, responder)

    bag = AssetBag.from_tables(schema, resolved)
    for asset in other:
        if asset.asset_type == "join":
            bag.joins[asset.id] = asset  # type: ignore[assignment]
        elif asset.asset_type == "metric":
            bag.metrics[asset.id] = asset  # type: ignore[assignment]
        elif asset.asset_type == "term":
            bag.terms[asset.id] = asset  # type: ignore[assignment]
        elif asset.asset_type == "few_shot":
            bag.few_shots[asset.id] = asset  # type: ignore[assignment]

    if run_agent_repass and model is not None:
        from .deep_agent import build_curator_agent

        agent = build_curator_agent(
            model, connector=connector, schema=schema, gateway=gateway, bag=bag
        )
        agent.invoke(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": (
                            f"SME answers have been folded into `{schema}`. "
                            "Refine joins/suspect flags if needed; keep human-certified "
                            "descriptions."
                        ),
                    }
                ]
            },
            config={"recursion_limit": max(max_agent_steps * 2, 40)},
        )

    bag.write(out_root)
    if not _corpora_differ(Path(a2_root), out_root, schema):
        raise RuntimeError(
            f"A3 corpus is identical to A2 at {out_root}; SME round-trip produced no edits"
        )
    return out_root
