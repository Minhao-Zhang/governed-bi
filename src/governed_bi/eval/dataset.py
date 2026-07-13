"""A tiny hand-authored beer_factory eval set (a stand-in for BIRD's jsonl).

The real eval reads ``train_final.jsonl`` / ``test_final.jsonl`` from the
BIRD-Obfuscation dataset; those obfuscated databases are prepared separately.
Until they land, this small gold set over the vendored (un-obfuscated)
beer_factory DB exercises the EX scorer and the arm harness end-to-end.

``answerable_by_template`` records whether the deterministic
``TemplateSqlGenerator`` (metric aggregates only) can solve an item, so a test
can predict the curator-arm EX without an LLM. A model-backed solver would raise
that arm's EX toward the count-style items it leaves unsolved today.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EvalItem:
    question: str
    sql: str  # gold reference SQL, executed to produce the ground-truth result set
    answerable_by_template: bool = False
    question_id: str | None = None
    difficulty: str | None = None
    evidence: str | None = None


# Gold SQL is written in the live (un-obfuscated) beer_factory identifiers and
# verified to execute against data/bird/beer_factory.sqlite.
BEER_FACTORY_EVAL: list[EvalItem] = [
    EvalItem(
        "What is the total revenue?",
        'SELECT SUM(PurchasePrice) FROM "transaction"',
        answerable_by_template=True,
    ),
    EvalItem(
        "What is the average star rating?",
        "SELECT AVG(StarRating) FROM rootbeerreview",
        answerable_by_template=True,
    ),
    EvalItem(
        "How many customers are there?",
        "SELECT COUNT(*) FROM customers",
    ),
    EvalItem(
        "How many transactions were recorded?",
        'SELECT COUNT(*) FROM "transaction"',
    ),
]

# Out-of-scope questions the system should refuse (D5). The first two match the
# curated ``negative_example`` (staffing / payroll); the third has no coverage.
BEER_FACTORY_UNANSWERABLE: list[str] = [
    "How many employees work at the factory?",
    "What is the average salary of factory staff?",
    "What was the weather like on the delivery days?",
]
