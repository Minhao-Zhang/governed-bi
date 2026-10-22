"""A port with one adapter is not a boundary, so each one here has at least two.
"""


from __future__ import annotations

from abc import abstractmethod
from typing import Any, Protocol, Sequence, runtime_checkable

__all__ = [
    "Vector",
    "Row",
    "ColumnInfo",
    "TableInfo",
    "Embedder",
    "Connector",
]

#: A plain list, so nothing crossing the port is pyarrow or numpy.
Vector = Sequence[float]

#: Normalisation is the grader's job, not the connector's.
Row = tuple[Any, ...]


class ColumnInfo(Protocol):
    name: str
    data_type: str
    nullable: bool
    primary_key: bool


class TableInfo(Protocol):
    name: str
    columns: Sequence[ColumnInfo]


@runtime_checkable
class Embedder(Protocol):
    """A ``...`` body is inherited as a concrete method returning ``None``.

    These three are abstract, or a forgotten ``model`` reports ``None`` as its cache key.
    """

    @property
    @abstractmethod
    def model(self) -> str:
        ...

    @property
    @abstractmethod
    def dimensions(self) -> int:
        ...

    @abstractmethod
    def embed(self, texts: Sequence[str]) -> list[Vector]:
        ...


@runtime_checkable
class Connector(Protocol):
    """``execute`` does no governance check. The harness calls it with ungoverned SQL on purpose.
    """

    def __enter__(self) -> Connector: ...

    def __exit__(self, *exc: object) -> None: ...

    @property
    def dialect(self) -> str:
        ...

    def execute(self, sql: str) -> tuple[Sequence[str], Sequence[Row], bool]:
        ...

    def list_tables(self) -> Sequence[str]: ...

    def describe_table(self, name: str) -> TableInfo: ...

    # No ``sample_values``. Building SQL from identifiers would skip the ledger.
