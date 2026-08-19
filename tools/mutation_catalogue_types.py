"""The one record type the catalogue's declared mutations are made of.

**Split out of ``tools/mutation_catalogue.py``** once that file reached 984 lines against ADR
0005 §6's hard 1000-line cap (``tools/check_file_length.py``) -- the cap forced the timing, not a
belief that ``Mutation`` was in the wrong place before. It lives on its own, rather than at the
top of either data half, so that ``mutation_catalogue_data_1.py`` and
``mutation_catalogue_data_2.py`` can both import it without importing each other or the
reassembling module: the dependency runs one way, from each data half to this file, and
``mutation_catalogue.py`` imports all three and owes nothing back to either data half.
"""

from __future__ import annotations

import dataclasses

__all__ = ["Mutation"]


@dataclasses.dataclass(frozen=True)
class Mutation:
    """One defect, re-introduced on purpose.

    ``anchor`` must appear **exactly once** in ``path``; a count of 0 or 2 fails the run rather
    than silently mutating the wrong line or nothing at all. ``tests`` is a pytest selection kept
    as narrow as the property allows, because the whole file runs once per mutation.
    """

    id: str
    what: str
    path: str
    anchor: str
    replacement: str
    tests: tuple[str, ...]
    #: The audit finding, so a failure here points at the reasoning rather than only the line.
    finding: str
