"""The analyst-visible corpus: a type, not a convention (ADR 0005 §1.5, B10).

v1's caller contract — "callers are documented as passing ``for_analyst()``" — was
unenforced and was breached by the pooled driver, shipping excluded PII column
names into the routing index. The fix is that everything that authorises or
indexes reads an :class:`AnalystCorpus`, which can only be built by
:func:`for_analyst`.

Column keys are folded here the same way ``govern.identifiers`` folds them
(``str.lower``, schema.table.column). The shapes must agree; a conformance test
locks that. This module cannot import ``govern`` — corpus sits below it in the
layer graph.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from .identity import slug
from .schema import Asset, ColumnAsset, Governance, ProvenanceStatus, Reliability, ReliabilityStatus
from .validate import _bare

__all__ = [
    "AnalystCorpus",
    "for_analyst",
    "analyst_corpus_from_keys",
    "column_key_for",
]


def column_key_for(asset: ColumnAsset) -> str:
    """``{schema}.{table}.{column}`` folded, or ``{table}.{column}`` when schema is empty.

    Must match :func:`governed_bi.govern.identifiers.column_key` /
    :func:`~governed_bi.govern.identifiers.normalise_column_key`, including the **slug**
    (ADR 0008 D1): the table half comes from ``parent_table``, which is already an asset id
    and therefore already slugged, while the column half is a raw ``physical_name`` and is
    slugged here. A conformance test locks the two shapes together, and this module cannot
    import ``govern`` -- ``corpus`` sits below it -- which is why ``slug`` lives in
    ``corpus.identity`` where both halves can reach it.
    """
    table = _bare(asset.parent_table).lower()
    column = slug(asset.physical_name).lower()
    schema = (asset.schema or "").strip().lower()
    if schema:
        return f"{schema}.{table}.{column}"
    return f"{table}.{column}"


@dataclass(frozen=True, slots=True)
class AnalystCorpus:
    """Filtered view over a loaded corpus. Construction is :func:`for_analyst` only."""

    _by_id: Mapping[str, Asset]
    _allowed_columns: frozenset[str]
    _excluded_columns: frozenset[str]
    _suspect_columns: frozenset[str]

    @property
    def by_id(self) -> Mapping[str, Asset]:
        return self._by_id

    @property
    def assets(self) -> tuple[Asset, ...]:
        return tuple(self._by_id.values())

    @property
    def allowed_columns(self) -> frozenset[str]:
        return self._allowed_columns

    @property
    def excluded_columns(self) -> frozenset[str]:
        return self._excluded_columns

    @property
    def suspect_columns(self) -> frozenset[str]:
        return self._suspect_columns

    def get(self, asset_id: str) -> Asset | None:
        return self._by_id.get(asset_id)


def for_analyst(assets: Sequence[Asset]) -> AnalystCorpus:
    """Drop ``governance.excluded`` assets and any not yet ``certified``; record excluded
    column keys for ``check()``.

    **Provenance-status filtering is not in ADR 0005 §1.5 — it is required by our own draft
    write path** (``corpus/drafts.py``), and it belongs here for the same reason exclusion
    does: this is the one function every retrieval/authorisation caller is required to route
    through. Without it, ``restamp_model_authored()`` stamping a fresh write ``proposed``
    would be a state nothing reads — the asset would index and serve exactly like a
    ``certified`` one, and the draft/approve split would be theatre.

    A seeded or hand-written asset with no ``audit``/``provenance`` at all is visible: absence
    of provenance is not evidence of an unreviewed draft, and treating it as one would hide
    every asset this project has ever shipped.
    """
    visible: dict[str, Asset] = {}
    excluded_cols: set[str] = set()
    allowed_cols: set[str] = set()
    suspect_cols: set[str] = set()

    for asset in assets:
        if asset.governance.excluded:
            if isinstance(asset, ColumnAsset):
                excluded_cols.add(column_key_for(asset))
            continue
        provenance = getattr(asset.audit, "provenance", None) if asset.audit is not None else None
        if provenance is not None and provenance.status is not ProvenanceStatus.certified:
            continue
        visible[asset.id] = asset
        if isinstance(asset, ColumnAsset):
            key = column_key_for(asset)
            allowed_cols.add(key)
            if asset.reliability.status is ReliabilityStatus.suspect:
                suspect_cols.add(key)

    return AnalystCorpus(
        _by_id=visible,
        _allowed_columns=frozenset(allowed_cols),
        _excluded_columns=frozenset(excluded_cols),
        _suspect_columns=frozenset(suspect_cols),
    )


def _parse_column_key(raw: str) -> tuple[str, str, str]:
    parts = [p for p in raw.split(".") if p]
    if len(parts) == 2:
        return "", parts[0], parts[1]
    if len(parts) == 3:
        return parts[0], parts[1], parts[2]
    raise ValueError(f"{raw!r} is not table.column or schema.table.column")


def analyst_corpus_from_keys(
    *,
    allowed: Iterable[str] = (),
    excluded: Iterable[str] = (),
    suspect: Iterable[str] = (),
) -> AnalystCorpus:
    """Build a minimal :class:`AnalystCorpus` for tests and key-holding call sites.

    Production paths use :func:`for_analyst` over real assets.
    """
    by_raw: dict[str, ColumnAsset] = {}
    for raw in allowed:
        schema, table, column = _parse_column_key(raw)
        table_id = f"{schema}.{table}" if schema else table
        by_raw[raw] = ColumnAsset(
            id=f"{table_id}.{column}",
            schema=schema,
            parent_table=table,
            physical_name=column,
            summary=f"{column} - test column",
        )
    for raw in suspect:
        schema, table, column = _parse_column_key(raw)
        table_id = f"{schema}.{table}" if schema else table
        existing = by_raw.get(raw)
        by_raw[raw] = ColumnAsset(
            id=f"{table_id}.{column}",
            schema=schema,
            parent_table=table,
            physical_name=column,
            summary=existing.summary if existing else f"{column} - suspect",
            reliability=Reliability(status=ReliabilityStatus.suspect),
            governance=existing.governance if existing else Governance(),
        )
    assets: list[Asset] = list(by_raw.values())
    for raw in excluded:
        schema, table, column = _parse_column_key(raw)
        table_id = f"{schema}.{table}" if schema else table
        assets.append(
            ColumnAsset(
                id=f"{table_id}.{column}",
                schema=schema,
                parent_table=table,
                physical_name=column,
                summary=f"{column} - excluded",
                governance=Governance(excluded=True, reason="test", by="human"),
            )
        )
    return for_analyst(assets)
