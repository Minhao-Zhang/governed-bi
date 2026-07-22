"""In-memory Inference-tier asset bag the deep-agent curator mutates via tools."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

from pydantic import ValidationError

from ..corpus.schemas import (
    Audit,
    Cardinality,
    ColumnRole,
    Complexity,
    FewShotAsset,
    JoinAsset,
    MetricAsset,
    NoteAsset,
    NoteKind,
    Provenance,
    ProvenanceSource,
    ProvenanceStatus,
    Reliability,
    ReliabilityStatus,
    TableAsset,
    TermAsset,
)
from ..corpus.serialize import write_corpus
from .clarifications import ClarificationRecord, ClarificationRecordStatus, parse_scope

_Asset = TableAsset | JoinAsset | MetricAsset | TermAsset | FewShotAsset | NoteAsset


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "x"


def _inference_audit(
    *,
    model: str | None = None,
    source: ProvenanceSource = ProvenanceSource.curator,
    status: ProvenanceStatus = ProvenanceStatus.proposed,
    by: str | None = None,
) -> Audit:
    prov = Provenance(source=source, status=status, model=model)
    if by is not None:
        data = prov.model_dump(mode="python")
        data["by"] = by
        prov = Provenance.model_validate(data)
    return Audit(provenance=prov)


@dataclass
class AssetBag:
    """Mutable working set for one schema's curated corpus."""

    schema: str
    tables: dict[str, TableAsset] = field(default_factory=dict)  # physical_name -> asset
    joins: dict[str, JoinAsset] = field(default_factory=dict)
    metrics: dict[str, MetricAsset] = field(default_factory=dict)
    terms: dict[str, TermAsset] = field(default_factory=dict)
    few_shots: dict[str, FewShotAsset] = field(default_factory=dict)
    notes: dict[str, NoteAsset] = field(default_factory=dict)
    model_name: str | None = None

    @classmethod
    def from_tables(
        cls, schema: str, tables: Iterable[TableAsset], *, model_name: str | None = None
    ) -> "AssetBag":
        bag = cls(schema=schema, model_name=model_name)
        for t in tables:
            bag.tables[t.physical_name] = t.model_copy(deep=True)
        return bag

    def all_assets(self) -> list[_Asset]:
        return [
            *self.tables.values(),
            *self.joins.values(),
            *self.metrics.values(),
            *self.terms.values(),
            *self.few_shots.values(),
            *self.notes.values(),
        ]

    def table_id(self, physical_name: str) -> str | None:
        t = self.tables.get(physical_name)
        return t.id if t else None

    def write(self, root) -> list:
        return write_corpus(root, self.schema, self.all_assets())

    # -- reads ------------------------------------------------------------- #

    def read_corpus(self, table: str | None = None, kind: str | None = None) -> str:
        """Render the live corpus (Facts + Inference written so far).

        ``table`` filters to one physical table (plus joins/metrics that mention
        it). ``kind`` is one of ``table``/``join``/``metric``/``term``/``few_shot``.
        """
        kinds = {kind.lower()} if kind else None
        lines: list[str] = []

        def want(k: str) -> bool:
            return kinds is None or k in kinds

        if want("table"):
            if table is not None and table not in self.tables:
                return f"error: unknown table={table!r}; known={sorted(self.tables)}"
            tables = (
                [self.tables[table]]
                if table is not None
                else list(self.tables.values())
            )
            for t in tables:
                header = t.physical_name
                if t.row_count is not None:
                    header += f" ({t.row_count} rows)"
                if t.description:
                    header += f" — {t.description}"
                lines.append(f"[table] {header}")
                for c in t.columns:
                    samples = ", ".join(str(v) for v in c.sample_values[:3])
                    line = (
                        f"  - {c.physical_name}: {c.logical_type.value}, "
                        f"unique={c.is_unique}"
                    )
                    if c.role is not None:
                        line += f", role={c.role.value}"
                    if c.reliability.status is ReliabilityStatus.suspect:
                        line += f", SUSPECT ({c.reliability.note or ''})"
                    if c.description:
                        line += f" — {c.description}"
                    if samples:
                        line += f" e.g. [{samples}]"
                    lines.append(line)

        if want("join"):
            for j in self.joins.values():
                if table is not None:
                    left = next(
                        (t.physical_name for t in self.tables.values() if t.id == j.left_table),
                        None,
                    )
                    right = next(
                        (
                            t.physical_name
                            for t in self.tables.values()
                            if t.id == j.right_table
                        ),
                        None,
                    )
                    if table not in (left, right):
                        continue
                lines.append(
                    f"[join] {j.id}: {j.on} "
                    f"card={j.cardinality.value if j.cardinality else '?'} "
                    f"conf={j.confidence}"
                )

        if want("metric"):
            for m in self.metrics.values():
                if table is not None:
                    base = next(
                        (
                            t.physical_name
                            for t in self.tables.values()
                            if t.id == m.base_table
                        ),
                        None,
                    )
                    if base != table:
                        continue
                lines.append(
                    f"[metric] {m.id}: {m.name} = {m.expression} "
                    f"(base={m.base_table}) conf={m.confidence}"
                )

        if want("term") and table is None:
            for term in self.terms.values():
                binding = (
                    f"{term.binding.asset_type}:{term.binding.asset_id}"
                    if term.binding
                    else "unbound"
                )
                lines.append(f"[term] {term.id}: {term.name} -> {binding}")

        if want("few_shot") and table is None:
            for fs in self.few_shots.values():
                lines.append(
                    f"[few_shot] {fs.id}: Q={fs.question!r} sql={fs.sql[:80]!r}..."
                )

        return "\n".join(lines) if lines else "(corpus empty for this filter)"

    # -- mutations --------------------------------------------------------- #

    def upsert_join(
        self,
        left_table: str,
        right_table: str,
        on: str,
        *,
        cardinality: str = "many_to_one",
        confidence: float = 0.7,
        certified: bool = False,
        answered_by: str | None = None,
    ) -> str:
        left_id = self.table_id(left_table)
        right_id = self.table_id(right_table)
        if left_id is None or right_id is None:
            return (
                f"error: unknown table(s) left={left_table!r} right={right_table!r}; "
                f"known={sorted(self.tables)}"
            )
        jid = f"join_{_slug(self.schema)}_{_slug(left_table)}_{_slug(right_table)}"
        try:
            card = Cardinality(cardinality)
        except ValueError:
            return f"error: invalid cardinality={cardinality!r}"
        try:
            asset = JoinAsset.model_validate(
                {
                    "id": jid,
                    "left_table": left_id,
                    "right_table": right_id,
                    "on": on,
                    "cardinality": card,
                    "cost": 1.0,
                    "confidence": confidence,
                    "audit": self._audit(certified=certified, answered_by=answered_by),
                }
            )
        except ValidationError as err:
            return f"error: invalid JoinAsset: {err}"
        self.joins[jid] = asset
        return f"ok: wrote {jid}"

    def upsert_metric(
        self,
        name: str,
        base_table: str,
        expression: str,
        *,
        confidence: float = 0.6,
        certified: bool = False,
        answered_by: str | None = None,
    ) -> str:
        base_id = self.table_id(base_table)
        if base_id is None:
            return f"error: unknown base_table={base_table!r}; known={sorted(self.tables)}"
        # Address an existing metric by id (see upsert_term) to update in place.
        if name in self.metrics:
            mid = name
            name = self.metrics[mid].name
        else:
            mid = f"metric_{_slug(self.schema)}_{_slug(name)}"
        try:
            asset = MetricAsset.model_validate(
                {
                    "id": mid,
                    "name": name,
                    "base_table": base_id,
                    "expression": expression,
                    "confidence": confidence,
                    "audit": self._audit(certified=certified, answered_by=answered_by),
                }
            )
        except ValidationError as err:
            return f"error: invalid MetricAsset: {err}"
        self.metrics[mid] = asset
        return f"ok: wrote {mid}"

    def _column_id_index(self) -> dict[str, str]:
        """Map every spelling the model plausibly emits for a column to its
        canonical loader-derived id.

        The model does not know the ``col_<table>_<column>`` derivation
        (``ids.derive_column_id``); left to free text it guesses
        ``<table_id>.<column>`` or ``<physical_table>.<column>``. Coercing those
        here is what keeps ``term.binding.asset_id`` from dangling.
        """
        from ..corpus.ids import derive_column_id

        index: dict[str, str] = {}
        for t in self.tables.values():
            for c in t.columns:
                cid = derive_column_id(t.id, c.physical_name)
                index[cid] = cid  # canonical id
                index[f"{t.id}.{c.physical_name}"] = cid  # tbl_x.col
                index[f"{t.physical_name}.{c.physical_name}"] = cid  # physical.col
        return index

    def _resolve_binding(
        self, asset_type: str, asset_id: str
    ) -> tuple[str | None, str | None]:
        """Resolve/coerce a term binding to a real asset id. Returns
        ``(resolved_id, error)``; ``error`` is a ready-to-return string when the
        binding cannot be made to resolve. Never persists a dangling reference."""
        if asset_type == "column":
            index = self._column_id_index()
            if asset_id in index:
                return index[asset_id], None
            sample = sorted(set(index.values()))[:8]
            return None, (
                f"error: binding column {asset_id!r} does not resolve. Pass a "
                f"physical 'table.column' or a col_* id, e.g. {sample}"
            )
        if asset_type == "table":
            by_id = {t.id for t in self.tables.values()}
            by_physical = {t.physical_name: t.id for t in self.tables.values()}
            if asset_id in by_id:
                return asset_id, None
            if asset_id in by_physical:
                return by_physical[asset_id], None
            return None, (
                f"error: binding table {asset_id!r} does not resolve; "
                f"known={sorted(by_physical)}"
            )
        if asset_type == "metric":
            if asset_id in self.metrics:
                return asset_id, None
            return None, (
                f"error: binding metric {asset_id!r} does not resolve; "
                f"known={sorted(self.metrics)}"
            )
        return None, (
            f"error: invalid binding_asset_type={asset_type!r} "
            "(expected column/table/metric)"
        )

    def upsert_term(
        self,
        name: str,
        *,
        binding_asset_type: str = "table",
        binding_asset_id: str | None = None,
        confidence: float = 0.6,
        certified: bool = False,
        answered_by: str | None = None,
    ) -> str:
        # Address an existing asset by id: when the caller passes a term id as
        # ``name`` (e.g. a fix-pass echoing a finding's asset_id), update that
        # asset in place instead of minting a slugged duplicate — the mechanism
        # behind the 6->12 dangling-ref doubling.
        if name in self.terms:
            tid = name
            name = self.terms[tid].name
        else:
            tid = f"term_{_slug(self.schema)}_{_slug(name)}"
        binding = None
        if binding_asset_id:
            resolved, err = self._resolve_binding(binding_asset_type, binding_asset_id)
            if err is not None:
                return err
            binding = {"asset_type": binding_asset_type, "asset_id": resolved}
        try:
            asset = TermAsset.model_validate(
                {
                    "id": tid,
                    "name": name,
                    "binding": binding,
                    "confidence": confidence,
                    "audit": self._audit(certified=certified, answered_by=answered_by),
                }
            )
        except ValidationError as err:
            return f"error: invalid TermAsset: {err}"
        self.terms[tid] = asset
        return f"ok: wrote {tid}"

    def upsert_few_shot(
        self,
        question: str,
        sql: str,
        *,
        complexity: str = "simple",
        confidence: float = 0.7,
        certified: bool = False,
        answered_by: str | None = None,
    ) -> str:
        n = len(self.few_shots) + 1
        fid = f"fs_{_slug(self.schema)}_{n}"
        try:
            cx = Complexity(complexity)
        except ValueError:
            return f"error: invalid complexity={complexity!r}"
        try:
            asset = FewShotAsset.model_validate(
                {
                    "id": fid,
                    "schema": self.schema,
                    "question": question,
                    "sql": sql,
                    "complexity": cx,
                    "confidence": confidence,
                    "audit": self._audit(certified=certified, answered_by=answered_by),
                }
            )
        except ValidationError as err:
            return f"error: invalid FewShotAsset: {err}"
        self.few_shots[fid] = asset
        return f"ok: wrote {fid}"

    def annotate_table(
        self,
        table: str,
        *,
        description: str | None = None,
        confidence: float | None = None,
        certified: bool = False,
        answered_by: str | None = None,
    ) -> str:
        t = self.tables.get(table)
        if t is None:
            return f"error: unknown table={table!r}"
        if description is None and confidence is None and not certified:
            return "error: annotate_table requires at least one of description/confidence"
        updates: dict = {}
        if description is not None:
            updates["description"] = description
        if confidence is not None:
            updates["confidence"] = confidence
        elif description is not None:
            updates["confidence"] = 0.7
        updates["audit"] = self._audit(
            certified=certified,
            answered_by=answered_by,
            existing=t.audit,
        )
        try:
            self.tables[table] = TableAsset.model_validate(
                {**t.model_dump(mode="python"), **updates}
            )
        except ValidationError as err:
            return f"error: invalid TableAsset: {err}"
        return f"ok: annotated table {table}"

    def annotate_column(
        self,
        table: str,
        column: str,
        *,
        description: str | None = None,
        role: str | None = None,
        reliability: str | None = None,
        suspect: bool | None = None,
        note: str | None = None,
        confidence: float | None = None,
        certified: bool = False,
        answered_by: str | None = None,
    ) -> str:
        t = self.tables.get(table)
        if t is None:
            return f"error: unknown table={table!r}"
        if all(
            v is None
            for v in (description, role, reliability, suspect, confidence)
        ) and not certified:
            return (
                "error: annotate_column requires at least one of "
                "description/role/reliability/suspect/confidence"
            )
        cols = []
        found = False
        for c in t.columns:
            if c.physical_name != column:
                cols.append(c)
                continue
            found = True
            updates: dict = {}
            if description is not None:
                updates["description"] = description
            if role is not None:
                try:
                    updates["role"] = ColumnRole(role)
                except ValueError:
                    return f"error: invalid role={role!r}"
            if suspect is True or (reliability is not None and reliability == "suspect"):
                suspect_note = note or "DO NOT USE — unreliable for analysis"
                if not suspect_note.startswith("DO NOT USE"):
                    suspect_note = f"DO NOT USE — {suspect_note}"
                updates["reliability"] = Reliability(
                    status=ReliabilityStatus.suspect, note=suspect_note
                )
                updates.setdefault("confidence", 0.4)
            elif reliability is not None:
                try:
                    updates["reliability"] = Reliability(
                        status=ReliabilityStatus(reliability), note=note
                    )
                except ValueError:
                    return f"error: invalid reliability={reliability!r}"
            if confidence is not None:
                updates["confidence"] = confidence
            elif description is not None and "confidence" not in updates:
                updates["confidence"] = 0.7
            updates["audit"] = self._audit(
                certified=certified,
                answered_by=answered_by,
                existing=c.audit,
            )
            try:
                cols.append(
                    type(c).model_validate({**c.model_dump(mode="python"), **updates})
                )
            except ValidationError as err:
                return f"error: invalid Column: {err}"
        if not found:
            return f"error: unknown column={table}.{column}"
        self.tables[table] = t.model_copy(update={"columns": cols})
        return f"ok: annotated {table}.{column}"

    # Back-compat aliases used by seed / older tests ------------------------- #

    def propose_join(self, *args, **kwargs) -> str:
        return self.upsert_join(*args, **kwargs)

    def propose_metric(self, *args, **kwargs) -> str:
        return self.upsert_metric(*args, **kwargs)

    def propose_term(self, *args, **kwargs) -> str:
        return self.upsert_term(*args, **kwargs)

    def propose_few_shot(self, *args, **kwargs) -> str:
        return self.upsert_few_shot(*args, **kwargs)

    def set_column_description(
        self, table: str, column: str, description: str, *, confidence: float = 0.7
    ) -> str:
        return self.annotate_column(
            table, column, description=description, confidence=confidence
        )

    def mark_column_suspect(
        self, table: str, column: str, *, note: str = "DO NOT USE — unreliable for analysis"
    ) -> str:
        return self.annotate_column(table, column, suspect=True, note=note)

    def set_table_description(
        self, table: str, description: str, *, confidence: float = 0.7
    ) -> str:
        return self.annotate_table(table, description=description, confidence=confidence)

    def apply_answered_clarifications(
        self, records: Iterable[ClarificationRecord]
    ) -> int:
        """Deterministic Phase B fold for offline/tests (no agent).

        Applies answered records whose ``scope`` is ``table:Name`` or
        ``table:Name.col`` as description + human/certified provenance.
        Returns the number of successful folds.
        """
        applied = 0
        for rec in records:
            if rec.status is not ClarificationRecordStatus.answered:
                continue
            if not rec.answer:
                continue
            try:
                table, column = parse_scope(rec.scope)
            except ValueError:
                continue
            by = rec.answered_by or "sme"
            if column is None:
                msg = self.annotate_table(
                    table,
                    description=rec.answer,
                    confidence=0.9,
                    certified=True,
                    answered_by=by,
                )
            else:
                msg = self.annotate_column(
                    table,
                    column,
                    description=rec.answer,
                    confidence=0.9,
                    certified=True,
                    answered_by=by,
                )
            if msg.startswith("ok:"):
                applied += 1
        return applied

    def propose_note(
        self,
        summary: str,
        *,
        kind: NoteKind = NoteKind.context,
        scope: Iterable[str] = (),
        confidence: float = 0.7,
        certified: bool = False,
        answered_by: str | None = None,
    ) -> str:
        """Record a governed note/caveat that serve should heed."""
        summary = (summary or "").strip()
        if not summary:
            return "error: empty note summary"
        note_id = f"note_{_slug(self.schema)}_{len(self.notes) + 1}"
        try:
            asset = NoteAsset.model_validate(
                {
                    "id": note_id,
                    "kind": kind,
                    "scope": list(scope),
                    "summary": summary,
                    "confidence": confidence,
                    "publication_status": (
                        ProvenanceStatus.certified if certified else ProvenanceStatus.proposed
                    ),
                    "audit": self._audit(certified=certified, answered_by=answered_by),
                }
            )
        except ValidationError as err:
            return f"error: invalid NoteAsset: {err}"
        self.notes[note_id] = asset
        return f"ok: wrote {note_id}"

    def record_caveats(self, records: Iterable[ClarificationRecord]) -> int:
        """Fold answered clarifications that don't map to an asset (``pair:`` /
        ``query:`` scopes — trap/annotation-error findings) into governance
        ``NoteAsset``s, so the caveat reaches the served corpus instead of dying
        in the ledger. Runs after both fold modes (deterministic + agent).
        Returns the number of notes recorded.
        """
        n = 0
        for rec in records:
            if rec.status is not ClarificationRecordStatus.answered or not rec.answer:
                continue
            try:
                parse_scope(rec.scope)  # table:/column: scopes are handled by the fold
                continue
            except ValueError:
                pass  # non-asset scope (pair:/query:/…) → record as a caveat
            msg = self.propose_note(
                rec.answer,
                kind=NoteKind.context,
                certified=True,
                answered_by=rec.answered_by or "sme",
            )
            if msg.startswith("ok:"):
                n += 1
        return n

    def _table_id_index(self) -> dict[str, str]:
        """Map a table id or physical name to its canonical table id."""
        index: dict[str, str] = {}
        for t in self.tables.values():
            index[t.id] = t.id
            index[t.physical_name] = t.id
        return index

    def _all_asset_ids(self) -> set[str]:
        """Every canonical id a reference may legitimately point at."""
        ids = {a.id for a in self.all_assets()}
        ids |= set(self._column_id_index().values())
        return ids

    def repair_references(self) -> int:
        """Deterministically re-resolve every coercible dangling reference to its
        canonical id, in place.

        Reference integrity is machine-checkable, so it is machine-fixable. A
        reference written in a spelling the model guesses (``tbl_x.col`` /
        ``physical.col`` for columns, a physical name for a table) is rewritten to
        the loader-derived id; a reference that already resolves, or that cannot
        be resolved at all, is left untouched (a genuine gap for the agent /
        human, not a formatting slip). Covers ``column.references``,
        ``metric.base_table``, ``join.left/right_table``, ``term.binding`` and
        ``note.scope``. Returns the number of fields rewritten. Runs before the
        agent fix-pass so a stochastic LLM is never handed a deterministic
        reference problem.
        """
        col_idx = self._column_id_index()
        col_ids = set(col_idx.values())
        tbl_idx = self._table_id_index()
        tbl_ids = set(tbl_idx.values())
        n = 0

        # column.references -> a column id
        for name, t in list(self.tables.items()):
            new_cols = []
            changed = False
            for c in t.columns:
                ref = c.references
                if ref and ref not in col_ids and col_idx.get(ref):
                    c = c.model_copy(update={"references": col_idx[ref]})
                    changed = True
                    n += 1
                new_cols.append(c)
            if changed:
                self.tables[name] = t.model_copy(update={"columns": new_cols})

        # metric.base_table -> a table id
        for mid, m in list(self.metrics.items()):
            if m.base_table not in tbl_ids and tbl_idx.get(m.base_table):
                self.metrics[mid] = m.model_copy(update={"base_table": tbl_idx[m.base_table]})
                n += 1

        # join.left_table / right_table -> table ids
        for jid, j in list(self.joins.items()):
            updates = {}
            for endpoint in ("left_table", "right_table"):
                cur = getattr(j, endpoint)
                if cur not in tbl_ids and tbl_idx.get(cur):
                    updates[endpoint] = tbl_idx[cur]
            if updates:
                self.joins[jid] = j.model_copy(update=updates)
                n += len(updates)

        # term.binding.asset_id -> canonical id (column/table/metric)
        for tid, term in list(self.terms.items()):
            if term.binding is None:
                continue
            resolved, err = self._resolve_binding(
                term.binding.asset_type, term.binding.asset_id
            )
            if err is not None or resolved is None or resolved == term.binding.asset_id:
                continue
            self.terms[tid] = term.model_copy(
                update={"binding": term.binding.model_copy(update={"asset_id": resolved})}
            )
            n += 1

        # note.scope[] -> any canonical asset id
        valid_any = self._all_asset_ids()
        for note_id, note in list(self.notes.items()):
            new_scope = []
            changed = False
            for s in note.scope:
                if s in valid_any:
                    new_scope.append(s)
                    continue
                fixed = col_idx.get(s) or tbl_idx.get(s)
                if fixed:
                    new_scope.append(fixed)
                    changed = True
                    n += 1
                else:
                    new_scope.append(s)  # unresolvable -> leave for agent / human
            if changed:
                self.notes[note_id] = note.model_copy(update={"scope": new_scope})

        return n

    # Back-compat: term-only entry point (superseded by repair_references).
    def repair_term_bindings(self) -> int:
        return self.repair_references()

    def suspect_count(self) -> int:
        n = 0
        for t in self.tables.values():
            for c in t.columns:
                if c.reliability.status is ReliabilityStatus.suspect:
                    n += 1
        return n

    def _audit(
        self,
        *,
        certified: bool = False,
        answered_by: str | None = None,
        existing: Audit | None = None,
    ) -> Audit:
        if certified:
            return _inference_audit(
                model=self.model_name,
                source=ProvenanceSource.human,
                status=ProvenanceStatus.certified,
                by=answered_by,
            )
        if existing is not None:
            return existing
        return _inference_audit(model=self.model_name)
