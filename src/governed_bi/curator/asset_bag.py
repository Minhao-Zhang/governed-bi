"""In-memory Inference-tier asset bag the deep-agent curator mutates via tools."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

from ..corpus.schemas import (
    Audit,
    Cardinality,
    Complexity,
    FewShotAsset,
    JoinAsset,
    MetricAsset,
    Provenance,
    ProvenanceSource,
    ProvenanceStatus,
    Reliability,
    ReliabilityStatus,
    TableAsset,
    TermAsset,
    TermBinding,
)
from ..corpus.serialize import write_corpus

_Asset = TableAsset | JoinAsset | MetricAsset | TermAsset | FewShotAsset


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "x"


def _inference_audit(*, model: str | None = None) -> Audit:
    return Audit(
        provenance=Provenance(
            source=ProvenanceSource.curator,
            status=ProvenanceStatus.proposed,
            model=model,
        )
    )


@dataclass
class AssetBag:
    """Mutable working set for one schema's curated corpus."""

    schema: str
    tables: dict[str, TableAsset] = field(default_factory=dict)  # physical_name -> asset
    joins: dict[str, JoinAsset] = field(default_factory=dict)
    metrics: dict[str, MetricAsset] = field(default_factory=dict)
    terms: dict[str, TermAsset] = field(default_factory=dict)
    few_shots: dict[str, FewShotAsset] = field(default_factory=dict)
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
        ]

    def table_id(self, physical_name: str) -> str | None:
        t = self.tables.get(physical_name)
        return t.id if t else None

    def write(self, root) -> list:
        return write_corpus(root, self.schema, self.all_assets())

    # -- mutations --------------------------------------------------------- #

    def propose_join(
        self,
        left_table: str,
        right_table: str,
        on: str,
        *,
        cardinality: str = "many_to_one",
        confidence: float = 0.7,
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
            card = Cardinality.many_to_one
        self.joins[jid] = JoinAsset(
            id=jid,
            left_table=left_id,
            right_table=right_id,
            on=on,
            cardinality=card,
            cost=1.0,
            confidence=confidence,  # type: ignore[arg-type]
            audit=_inference_audit(model=self.model_name),
        )
        return f"ok: wrote {jid}"

    def propose_metric(
        self,
        name: str,
        base_table: str,
        expression: str,
        *,
        confidence: float = 0.6,
    ) -> str:
        base_id = self.table_id(base_table)
        if base_id is None:
            return f"error: unknown base_table={base_table!r}; known={sorted(self.tables)}"
        mid = f"metric_{_slug(self.schema)}_{_slug(name)}"
        self.metrics[mid] = MetricAsset(
            id=mid,
            name=name,
            base_table=base_id,
            expression=expression,
            confidence=confidence,  # type: ignore[arg-type]
            audit=_inference_audit(model=self.model_name),
        )
        return f"ok: wrote {mid}"

    def propose_term(
        self,
        name: str,
        *,
        binding_asset_type: str = "table",
        binding_asset_id: str | None = None,
        confidence: float = 0.6,
    ) -> str:
        tid = f"term_{_slug(self.schema)}_{_slug(name)}"
        binding = None
        if binding_asset_id:
            binding = TermBinding(asset_type=binding_asset_type, asset_id=binding_asset_id)  # type: ignore[arg-type]
        self.terms[tid] = TermAsset(
            id=tid,
            name=name,
            binding=binding,
            confidence=confidence,  # type: ignore[arg-type]
            audit=_inference_audit(model=self.model_name),
        )
        return f"ok: wrote {tid}"

    def propose_few_shot(
        self,
        question: str,
        sql: str,
        *,
        complexity: str = "simple",
        confidence: float = 0.7,
    ) -> str:
        n = len(self.few_shots) + 1
        fid = f"fs_{_slug(self.schema)}_{n}"
        try:
            cx = Complexity(complexity)
        except ValueError:
            cx = Complexity.simple
        self.few_shots[fid] = FewShotAsset(
            id=fid,
            schema=self.schema,
            question=question,
            sql=sql,
            complexity=cx,
            confidence=confidence,  # type: ignore[arg-type]
            audit=_inference_audit(model=self.model_name),
        )
        return f"ok: wrote {fid}"

    def set_column_description(
        self, table: str, column: str, description: str, *, confidence: float = 0.7
    ) -> str:
        t = self.tables.get(table)
        if t is None:
            return f"error: unknown table={table!r}"
        cols = []
        found = False
        for c in t.columns:
            if c.physical_name == column:
                found = True
                cols.append(
                    c.model_copy(
                        update={
                            "description": description,
                            "confidence": confidence,
                            "audit": c.audit or _inference_audit(model=self.model_name),
                        }
                    )
                )
            else:
                cols.append(c)
        if not found:
            return f"error: unknown column={table}.{column}"
        self.tables[table] = t.model_copy(update={"columns": cols})
        return f"ok: described {table}.{column}"

    def mark_column_suspect(
        self, table: str, column: str, *, note: str = "DO NOT USE — likely decoy/trap"
    ) -> str:
        t = self.tables.get(table)
        if t is None:
            return f"error: unknown table={table!r}"
        cols = []
        found = False
        for c in t.columns:
            if c.physical_name == column:
                found = True
                cols.append(
                    c.model_copy(
                        update={
                            "reliability": Reliability(
                                status=ReliabilityStatus.suspect,
                                note=note if note.startswith("DO NOT USE") else f"DO NOT USE — {note}",
                            ),
                            "confidence": 0.4,
                            "audit": c.audit or _inference_audit(model=self.model_name),
                        }
                    )
                )
            else:
                cols.append(c)
        if not found:
            return f"error: unknown column={table}.{column}"
        self.tables[table] = t.model_copy(update={"columns": cols})
        return f"ok: marked suspect {table}.{column}"

    def set_table_description(
        self, table: str, description: str, *, confidence: float = 0.7
    ) -> str:
        t = self.tables.get(table)
        if t is None:
            return f"error: unknown table={table!r}"
        self.tables[table] = t.model_copy(
            update={
                "description": description,
                "confidence": confidence,
                "audit": t.audit or _inference_audit(model=self.model_name),
            }
        )
        return f"ok: described table {table}"

    def suspect_count(self) -> int:
        n = 0
        for t in self.tables.values():
            for c in t.columns:
                if c.reliability.status is ReliabilityStatus.suspect:
                    n += 1
        return n
