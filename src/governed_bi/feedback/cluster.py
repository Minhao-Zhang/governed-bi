"""Grouping observations that look like the same problem. Structural, never semantic.

**The key is ``(category, schema)``, and the missing-table set is deliberately not in it.** Two
earlier answers were both wrong and the measurement is why. The design keyed on the tables a turn
*was allowed to read*, which for a coverage miss is exactly backwards — the defect is the table
**absent** from ``licensed``, so two turns failing on the same missing table got disjoint keys. The
correction over-shot: keying on the absence too is *more* specific than the data supports.

Measured on the 73 coverage-miss failures of the v4 arm, 2026-08-23:

===========================================  ========  ==========  ===  ===============
key                                          clusters  singletons  max  in a cluster ≥2
===========================================  ========  ==========  ===  ===============
``(category, schema, missing[:3])``                70          67    2              8 %
``(category, schema)``  **shipped**                54          37    3             49 %
``(schema, missing[:1])``                          63          55    4             25 %
``(schema,)``                                      36          17    6             77 %
``(category,)``                                     4           0   33            100 %
===========================================  ========  ==========  ===  ===============

**56 of the 73 have exactly one missing table, and those tables are mostly different ones**, so the
absence identifies a turn rather than a problem — keying on it makes 92 % of the queue singletons.
``schema`` alone clusters 77 % and is too coarse in the other direction: six unrelated failures in
one schema is not one problem, and a reviewer told it was would stop believing the grouping.
``(category, schema)`` is the one line in that table that groups roughly half the population into
something a steward can act on ("three ``wrong_scope`` failures in ``airline``") while still
splitting a refusal from a wrong number.

``missing_tables`` stays **on the row** and out of the key. It is the evidence a reviewer reads and
the cluster reports the members' *intersection* of it, which is now informative rather than
definitional.

**Nothing here reads a question.** No embedding, no model, no cost. That is a limit and the surface
that renders a cluster has to say so, because a reviewer who believes the machine decided two
questions mean the same thing will treat one cluster as one problem without checking.

**And the honest headline: on this population, clustering buys about half a queue and no more.**
ADR 0015 §Open questions asked whether complaints cluster at all, and the answer is "weakly": the
largest cluster is three. The batching argument the design built on top of it -- that the marginal
cost of one more observation in a cluster is zero -- does not survive that, and anything sized on it
has to be re-sized.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable, Sequence

from governed_bi.feedback.events import Category, Observation

__all__ = ["Cluster", "cluster_key", "clusters"]

#: Stands in for a schema an observation does not name. Its own bucket rather than a blank,
#: because "retrieval never reached a schema" is a defect class and not a missing value.
UNROUTED = "unrouted"


@dataclass(frozen=True, slots=True)
class Cluster:
    """Observations that share a key, newest-last, with the key's parts kept readable.

    ``n_distinct_questions`` is the number a reviewer actually needs: it says whether this is one
    person hitting a wall repeatedly or several questions blocked by one gap, and the two want
    different amounts of attention.
    """

    key: str
    category: Category | None
    db_id: str
    missing_tables: tuple[str, ...]
    observations: tuple[Observation, ...]

    @property
    def n(self) -> int:
        return len(self.observations)

    @property
    def n_distinct_questions(self) -> int:
        return len({o.question_id or o.question for o in self.observations})

    @property
    def oldest_filed_at(self) -> str:
        return min(o.filed_at for o in self.observations)


def cluster_key(obs: Observation) -> str:
    """``{category}|{schema}``. Deterministic, cheap, and no more specific than the data supports.

    Falls back to the observation's own id when there is nothing to group on — a row with neither
    a category nor a schema is a cluster of one, and saying so beats dropping it into a bucket
    with every other under-specified row.
    """
    category = obs.category.value if obs.category else ""
    schema = obs.db_id or (obs.schemas[0] if obs.schemas else UNROUTED)
    if not (category or obs.db_id or obs.schemas):
        return f"solo|{obs.observation_id}"
    return f"{category}|{schema}"


def clusters(observations: Iterable[Observation]) -> list[Cluster]:
    """Group, then order **oldest cluster first**.

    Oldest-first on the cluster's oldest member, and deliberately not by size: a five-observation
    cluster from this morning is not more urgent than one that has waited a month, and sorting by
    size makes the long tail permanently invisible.
    """
    grouped: dict[str, list[Observation]] = defaultdict(list)
    for obs in observations:
        grouped[cluster_key(obs)].append(obs)

    out: list[Cluster] = []
    for key, members in grouped.items():
        members.sort(key=lambda o: (o.filed_at, o.observation_id))
        first = members[0]
        out.append(
            Cluster(
                key=key,
                category=first.category,
                db_id=first.db_id or (first.schemas[0] if first.schemas else UNROUTED),
                missing_tables=_shared_missing(members),
                observations=tuple(members),
            )
        )
    out.sort(key=lambda c: (c.oldest_filed_at, c.key))
    return out


def _shared_missing(members: Sequence[Observation]) -> tuple[str, ...]:
    """The missing tables every member of the cluster has in common.

    The intersection and not the union: the union grows with the cluster and stops describing
    what these observations share, which is the one thing a cluster is for. An empty
    intersection is a real answer and means the key grouped them on category and schema alone.
    """
    sets = [set(o.missing_tables) for o in members if o.missing_tables]
    if not sets:
        return ()
    shared = set.intersection(*sets)
    return tuple(sorted(shared))
