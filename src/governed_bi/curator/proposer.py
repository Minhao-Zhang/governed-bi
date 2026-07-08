"""Curator loop step 2 - Propose (Inference tier).

The proposer hypothesizes the **Inference tier** on top of the programmatic
**Facts tier** that ``profile.profile_database`` emits. Per D10
(``docs/curator.md``) Facts (dtypes, uniqueness, samples, row counts) are never
proposed and never checked; everything the proposer *asserts* is what the
adversary later tries to refute (``adversary.review``). The adversary boundary
*is* the Facts/Inference boundary.

A full LLM proposer (running on the ``deepagents`` harness, seeded with the DB's
train queries + BIRD ``evidence``) would additionally write column/table prose
descriptions, joins, reliability caveats, terms, and metrics/rules. This module
ships a deterministic :class:`HeuristicProposer` so the whole
proposer -> adversary -> promote loop runs with no network and no LLM. The
:class:`Proposer` protocol is the seam an LLM proposer plugs into.

The heuristic never fabricates prose it cannot know from Facts: it fills
``role``, ``confidence`` and provenance, and deliberately leaves ``description``
as ``None`` (that is the LLM proposer's job).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..corpus.schemas import (
    Audit,
    Column,
    ColumnRole,
    LogicalType,
    Provenance,
    ProvenanceSource,
    ProvenanceStatus,
    TableAsset,
)

# Fixed confidence for every heuristic guess: honest about being a cheap prior,
# not a measured score. An LLM proposer would set this per assertion.
_HEURISTIC_CONFIDENCE = 0.5

# Numeric logical types that make an unkeyed column a measure candidate.
_NUMERIC = (LogicalType.integer, LogicalType.decimal)


@runtime_checkable
class Proposer(Protocol):
    """Fills the Inference tier of a batch of Facts-only table assets.

    Implementations return proposed assets (``provenance.status == proposed``);
    the adversary decides what survives to ``draft``. The LLM proposer and the
    deterministic :class:`HeuristicProposer` share this seam.
    """

    def propose(self, tables: list[TableAsset]) -> list[TableAsset]:
        """Return new table assets with the Inference tier populated."""
        ...


def _looks_like_key(physical_name: str) -> bool:
    """A name that reads like an identifier column (``CustomerID``, ``id``)."""
    return physical_name.endswith(("ID", "Id", "id"))


def _infer_role(column: Column, *, sole_unique: bool) -> ColumnRole:
    """Classify a column from Facts alone.

    Precedence, strongest signal first:

    - ``foreign_key`` when ``references`` is already set (an explicit prior).
    - ``primary_key`` when the column is unique and either reads like a key or
      is the table's only unique column.
    - ``measure`` when the column is numeric and not a key.
    - ``dimension`` otherwise.
    """
    if column.references is not None:
        return ColumnRole.foreign_key
    if column.is_unique and (_looks_like_key(column.physical_name) or sole_unique):
        return ColumnRole.primary_key
    if column.logical_type in _NUMERIC:
        return ColumnRole.measure
    return ColumnRole.dimension


def _proposed_provenance() -> Audit:
    """A fresh proposer-authored provenance stamp (``curator`` / ``proposed``)."""
    return Audit(
        provenance=Provenance(
            source=ProvenanceSource.curator,
            status=ProvenanceStatus.proposed,
        )
    )


class HeuristicProposer:
    """Deterministic proposer: roles + confidence + provenance, no invented prose.

    Fills ``role``, ``confidence`` and an ``audit`` stamp on every column, and a
    matching stamp on the table, without inventing a ``description`` (the LLM
    proposer's job, left ``None``). Idempotent and side-effect free: inputs are
    never mutated; every asset is returned as a fresh ``model_copy``.

    Implements the :class:`Proposer` protocol.
    """

    def propose(self, tables: list[TableAsset]) -> list[TableAsset]:
        return [self._propose_table(t) for t in tables]

    def _propose_table(self, table: TableAsset) -> TableAsset:
        unique_count = sum(1 for c in table.columns if c.is_unique)
        new_columns = [
            self._propose_column(c, sole_unique=(unique_count == 1 and c.is_unique))
            for c in table.columns
        ]
        # description/grain are left to the LLM proposer; the provenance stamp
        # makes the table a promotable proposed unit.
        return table.model_copy(
            update={
                "columns": new_columns,
                "confidence": _HEURISTIC_CONFIDENCE,
                "audit": _proposed_provenance(),
            }
        )

    def _propose_column(self, column: Column, *, sole_unique: bool) -> Column:
        role = _infer_role(column, sole_unique=sole_unique)
        # description stays None: prose is the LLM proposer's seam.
        return column.model_copy(
            update={
                "role": role,
                "confidence": _HEURISTIC_CONFIDENCE,
                "audit": _proposed_provenance(),
            }
        )
