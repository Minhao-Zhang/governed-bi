"""Every declared mutation: one defect, re-introduced on purpose, with the test that must fail.

Split out of ``tools/mutate.py`` on 2026-08-11 for the reason ADR 0005 §6 gives — the runner
plus the catalogue crossed the 1 000-line hard cap, and it will keep growing, because a
catalogue is the one thing in this repository that is *supposed* to be append-only. The runner
is a hundred lines and stable; this file is a list.

**The list itself moved again, 2026-08-18, for the same reason.** This module hit the same cap
the catalogue was split out of ``mutate.py`` to relieve, and a data table has no logic to trim —
so the entries moved to ``mutation_catalogue_data_1.py`` and ``mutation_catalogue_data_2.py``,
and the shared ``Mutation`` record they are built from moved to ``mutation_catalogue_types.py``
so neither data half has to import the other. This module now only re-exports ``Mutation`` and
concatenates the two halves into ``MUTATIONS``, in their original order — nothing a caller of
this module observes has changed. Read ``tools/mutate.py`` for what a run proves and what it
does not, and either data module's own docstring for where a new entry belongs.
"""

from __future__ import annotations

from mutation_catalogue_data_1 import MUTATIONS_DATA_1
from mutation_catalogue_data_2 import MUTATIONS_DATA_2
from mutation_catalogue_types import Mutation

__all__ = ["Mutation", "MUTATIONS"]

MUTATIONS: tuple[Mutation, ...] = MUTATIONS_DATA_1 + MUTATIONS_DATA_2
