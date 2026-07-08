"""RVGD retrieval over the server-visible corpus view.

Input is always ``Corpus.for_server()`` so the tier contract is structurally
guaranteed (no Audit, no excluded assets).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..corpus import Asset, Corpus


@dataclass(frozen=True)
class RetrievalResult:
    assets: list["Asset"]
    skills: list[str]  # skill bodies to inject
    corrective_rag_triggered: bool = False  # -> reliability stamp


def retrieve(corpus: "Corpus", question: str, *, route: str, token_budget: int) -> RetrievalResult:
    """R/V/G/D retrieve + rerank within ``token_budget`` for the given route."""
    raise NotImplementedError("RVGD retrieval pending; needs vector + BM25 indexes")
