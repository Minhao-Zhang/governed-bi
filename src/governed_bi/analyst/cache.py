"""Analyst step 4: the SQL semantic-cache fast path (D7 / Architecture section 6).

Embed the question, match it against previously-answered questions by cosine, and
on a hit reuse the stored SQL. Two invariants from the design:

- **Cache SQL text only, never results.** Freshness over latency: a hit still
  re-executes the SQL against the live database, so no stored rows are ever served.
  Row-level scoping is a property of the *execution*, not of this cache: `identity`
  is carried into `Gateway.execute` and used only for the audit row, so the cache
  must not be described as identity-scoped (AUDIT R7). Entries are nonetheless
  partitioned by `scope` — see :class:`CacheScope` — and the identity key is part of
  it, so if gateway-level scoping ever lands the cache is already partitioned for it.
- **Fail-closed even on a hit.** This implementation additionally re-runs the
  guardrails on the cached SQL before executing (with the licensed-table set that
  was in force when it was stored), so a corpus change that would now block the
  query is caught rather than silently served. The design's "skip retrieval /
  planning / generation" still holds - only the cheap, corpus-global re-check runs.
- **Cosine similarity is not equivalence.** Two questions differing only in a
  threshold ("more than 100 dollars" / "more than 200 dollars") embed at ~0.94 and
  cleared the 0.92 gate, so the cache re-executed the *wrong predicate* with full
  freshness (AUDIT R7). Literals are therefore compared exactly, outside the
  embedding, and a mismatch is a miss.

Only clean (``governed``) answers are cached, so a hit is always high-confidence.
The clock is injected so TTL expiry is deterministic in tests. The store is a
simple in-memory list scanned linearly, which suits the small, single-process
scope here; a production deployment swaps in a shared vector store behind the same
interface.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable

from ..llm import cosine

if TYPE_CHECKING:
    from ..llm import Embedder

# Architecture section 7 reusable numbers: cosine gate 0.92, TTL 15 min.
DEFAULT_CACHE_GATE = 0.92
DEFAULT_TTL_SECONDS = 15 * 60


@dataclass(frozen=True)
class CacheScope:
    """Everything besides the question that changes what the right SQL is.

    The key used to be the question embedding and nothing else, so one process
    serving two corpora, two dialects, two schemas or two prompt variants could
    answer from the wrong one (AUDIT R7). All fields are plain strings so a scope is
    hashable and cheap to compare.

    ``identity_key`` is included for partitioning even though execution is not
    identity-scoped today: it costs nothing and means the cache does not have to be
    revisited when it becomes so.
    """

    corpus_hash: str = ""
    dialect: str = ""
    default_schema: str = ""
    model: str = ""
    prompt_set_hash: str = ""
    identity_key: str = ""


#: Numbers, quoted strings and dates in a question. Cosine similarity treats these
#: as noise; they are usually the whole predicate.
_LITERAL_RE = re.compile(r"\d+(?:\.\d+)?" r"|'[^']*'" r'|"[^"]*"')


def question_literals(question: str) -> tuple[str, ...]:
    """Literals in a question, in order — the part cosine cannot be trusted with."""
    return tuple(_LITERAL_RE.findall(question or ""))


@dataclass(frozen=True)
class CacheEntry:
    """One cached question -> SQL association (SQL text only, no results).

    The reliability stamp is **not** cached: the agent core re-derives it fresh
    from the current corpus on a hit (over ``tables_used``), so the stamp cannot
    go stale.
    """

    question: str
    vector: list[float]
    sql: str
    licensed_tables: frozenset[str]  # physical names in force when stored (L4 re-check)
    tables_used: frozenset[str]  # asset ids, for re-deriving the stamp on a hit
    metric_id: str | None
    stored_at: float  # epoch seconds (from the injected clock)
    scope: CacheScope = CacheScope()
    literals: tuple[str, ...] = ()


@dataclass
class SqlCache:
    """A semantic SQL cache keyed by question embedding.

    ``lookup`` returns the freshest non-expired entry whose question is within the
    cosine ``gate`` of the query (``None`` on a miss). ``put`` records a new entry.
    The embedder is the same seam retrieval uses; the clock is injectable.
    """

    embedder: "Embedder"
    gate: float = DEFAULT_CACHE_GATE
    ttl_seconds: int = DEFAULT_TTL_SECONDS
    clock: Callable[[], float] = time.time
    #: The deployment this cache belongs to. Entries under a different scope are
    #: invisible to ``lookup`` rather than merely unlikely to match.
    scope: CacheScope = CacheScope()
    _entries: list[CacheEntry] = field(default_factory=list)

    def _live_entries(self, now: float) -> list[CacheEntry]:
        """Drop expired entries in place and return the survivors."""
        self._entries = [e for e in self._entries if e.stored_at + self.ttl_seconds > now]
        return self._entries

    def lookup(self, question: str) -> CacheEntry | None:
        """The freshest live entry that matches on scope, literals AND cosine.

        All three, not cosine alone: scope keeps two deployments apart, and the
        literal check is what stops "over 100 dollars" answering "over 200 dollars"
        with a confident, freshly-executed number.
        """
        now = self.clock()
        live = self._live_entries(now)
        if not live:
            return None
        literals = question_literals(question)
        eligible = [e for e in live if e.scope == self.scope and e.literals == literals]
        if not eligible:
            return None
        vec = self.embedder.embed_one(question)
        best: CacheEntry | None = None
        best_sim = self.gate
        for entry in eligible:
            sim = cosine(vec, entry.vector)
            if sim >= best_sim:
                best_sim = sim
                best = entry
        return best

    def put(
        self,
        question: str,
        sql: str,
        *,
        licensed_tables: frozenset[str],
        tables_used: frozenset[str],
        metric_id: str | None,
    ) -> None:
        self._entries.append(
            CacheEntry(
                question=question,
                vector=self.embedder.embed_one(question),
                sql=sql,
                licensed_tables=licensed_tables,
                tables_used=tables_used,
                metric_id=metric_id,
                stored_at=self.clock(),
                scope=self.scope,
                literals=question_literals(question),
            )
        )

    def __len__(self) -> int:
        return len(self._entries)
