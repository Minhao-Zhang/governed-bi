"""Prompt registry: every engine prompt, variants, and ``prompt_set_hash``.

Prompts live here so the hash covers the whole set. Text and pure functions
only — no I/O, settings, or non-stdlib imports. Both serve and eval import it.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Mapping

__all__ = [
    "Prompt",
    "PROMPT_REGISTRY",
    "FACET_QUERY_PROMPTS",
    "DEFAULT_VARIANTS",
    "prompt_text",
    "prompt_set_hash",
    "select",
    "unknown_prompts",
]


@dataclass(frozen=True)
class Prompt:
    """One prompt, its variants, and which stage sends it.

    ``stage`` is a Stage value held as a string (this module must not import
    ``stages``); coherence is checked in tests. ``why`` is required.
    """

    name: str
    stage: str
    why: str
    variants: Mapping[str, str]
    default: str = "v1"

    def text(self, variant: str | None = None) -> str:
        key = variant or self.default
        if key not in self.variants:
            raise KeyError(
                f"prompt {self.name!r} has no variant {key!r}; declared: "
                f"{sorted(self.variants)}. A variant chosen by a knob and absent here would "
                "otherwise fall back to the default silently, and the run would report the "
                "hash of a prompt it did not send."
            )
        return self.variants[key]


#: SQL-writing agent system prompt.
ANALYST = Prompt(
    name="analyst",
    stage="agent_core",
    why=(
        "The SQL-writing agent's system prompt. Carries the two identifier rules whose absence "
        "made governance refuse for the wrong stated reason (ADR 0008 P8/D10)."
    ),
    variants={
        "v1": (
            "You are a governed BI analyst. Use only the context and tools provided. "
            "Prefer run_query for factual answers. Call ask_user only when a missing "
            "fact blocks a correct SQL answer.\n"
            "Write every table reference as schema.table — an unqualified name is refused. "
            "Spell identifiers exactly as the context gives them, and wrap any identifier "
            'containing a space, punctuation or a leading digit in double quotes, e.g. '
            'airline."Air Carriers".'
        ),
        "v2": (
            "You are a governed BI analyst. Use only the context and tools provided. "
            "Call ask_user only when a missing fact blocks a correct SQL answer.\n"
            "Write every table reference as schema.table — an unqualified name is refused. "
            "Spell identifiers exactly as the context gives them, and wrap any identifier "
            'containing a space, punctuation or a leading digit in double quotes, e.g. '
            'airline."Air Carriers".\n'
            "Tool arguments are asset ids, not SQL names. When a context line carries "
            "id=..., pass that value to read_body, inspect_schema and sample_rows; the "
            "spelling before it is for SQL only.\n"
            "Before writing SQL you may call inspect_schema for a table's columns, "
            "sample_rows to see a column's actual values, and read_body for an asset's "
            "notes. Use sample_rows whenever a filter compares against a literal you have "
            "not seen. Then answer with run_query."
        ),
    },
    default="v2",
)


#: Guard scope gate: is this a BI question? Reply YES/NO; fail closed on YES.
BI_SCOPE = Prompt(
    name="bi_scope",
    stage="guard",
    why=(
        "Refuses a turn that is not a business-intelligence question, before any retrieval or "
        "SQL is paid for. Not an injection defence — the deterministic guard rules own that, and "
        "the maintainer scoped this to an internal tool where injection is not the concern."
    ),
    variants={
        "v1": (
            "You decide whether a request belongs to a business-intelligence system that "
            "answers questions about a company's data by querying its database.\n\n"
            "In scope: questions about data, metrics, counts, trends, comparisons, rankings, "
            "records, or the meaning of a field or table.\n"
            "Out of scope: general knowledge, chat, opinions, code unrelated to querying the "
            "data, instructions about how you should behave, and anything not answerable from "
            "a database.\n\n"
            "Reply with exactly one word: YES if it is in scope, NO if it is not. "
            "No punctuation, no explanation."
        ),
    },
)


#: Shared suffix for facet rewriters: search text only, no reasoning.
_REWRITE_TAIL = (
    "\n\nReply with the search text only — no preamble, no explanation, no quotes. "
    "Keep it under 30 words. If the question gives you nothing to work with, reply with "
    "the question unchanged."
)

#: Schema-facet rewriter (declared for hashing; not sent — see FACET_QUERY_PROMPTS).
FACET_SCHEMA_QUERY = Prompt(
    name="facet_schema_query",
    stage="facet_schema",
    why=(
        "Turns the question into catalogue search terms for the tables and schemas that would "
        "answer it. This is the rewrite the schema router consumes, so it decides which corpus "
        "the analyst is shown at all."
    ),
    default="v2",
    variants={
        "v1": (
            "You rewrite a business question into search text for finding database TABLES and "
            "SCHEMAS.\n\nDescribe the kind of tables and schemas that would be needed to answer "
            "the question — the entities they store, not the calculation asked for. Use the "
            "vocabulary a data catalogue would use." + _REWRITE_TAIL
        ),
        "v2": (
            "You turn a business question into SEARCH TERMS for a data catalogue of tables and "
            "schemas.\n\n"
            "Emit terms, not a sentence.\n\n"
            "**Lead with the words that tell the right schema apart from every other one.** Many "
            "schemas store ratings, prices, dates and counts; far fewer store restaurants, "
            "airlines or hospitals. So put the specific domain nouns of the question first, then "
            "their synonyms, then the catalogue words for the same thing (table, records, "
            "directory, master, listing). Name the entities stored, never the calculation asked "
            "for.\n\n"
            "Do not join the terms into clauses — no 'and', no 'including', no 'such as'. A "
            "description reads well and retrieves badly: it spends its weight on the generic "
            "words every schema shares." + _REWRITE_TAIL
        ),
    },
)

FACET_TERM_QUERY = Prompt(
    name="facet_term_query",
    stage="facet_term",
    why="Turns the question into search text for business-term and definition assets.",
    variants={
        "v1": (
            "You rewrite a business question into search text for finding BUSINESS TERM "
            "definitions in a semantic layer.\n\nName the domain terms, jargon and entity names "
            "the question depends on — the words whose meaning has to be pinned down before the "
            "question can be answered." + _REWRITE_TAIL
        ),
    },
)

FACET_METRIC_QUERY = Prompt(
    name="facet_metric_query",
    stage="facet_metric",
    why="Turns the question into search text for metric definitions.",
    variants={
        "v1": (
            "You rewrite a business question into search text for finding METRIC definitions in "
            "a semantic layer.\n\nName the measures, aggregations and calculations the question "
            "asks for — averages, counts, totals, rates, ratios — and what they are computed "
            "over." + _REWRITE_TAIL
        ),
    },
)

FACET_ENTITY_QUERY = Prompt(
    name="facet_entity_query",
    stage="facet_entity",
    why="Turns the question into search text for the concrete entities and column values it names.",
    variants={
        "v1": (
            "You rewrite a business question into search text for finding the specific ENTITIES "
            "it refers to — named people, places, products, categories, statuses or codes that "
            "would appear as values in a column.\n\nList them plainly. If the question names no "
            "specific entity, describe the kind of entity it is about." + _REWRITE_TAIL
        ),
    },
)

FACET_EXAMPLE_QUERY = Prompt(
    name="facet_example_query",
    stage="facet_example",
    why=(
        "Turns the question into search text for past question/SQL example pairs. The facet the "
        "maintainer singled out: past SQL examples help a lot, and an embedder retrieves them "
        "better than BM25 — which is why this facet declares only the semantic channel."
    ),
    variants={
        "v1": (
            "You rewrite a business question into search text for finding PAST QUESTION AND SQL "
            "EXAMPLES that would be useful precedents.\n\nDescribe the shape of the query the "
            "question needs — what is being counted or aggregated, over what, grouped or "
            "filtered by what, and how many tables have to be joined. A useful precedent has the "
            "same shape, not the same subject." + _REWRITE_TAIL
        ),
    },
)

#: Closing sentence when the agent did not write one.
NARRATE = Prompt(
    name="narrate",
    stage="narrate",
    why=(
        "Guarantees the turn ends in a sentence. The answer card reads `answer_text`, and a "
        "turn whose agent finished on a tool call rendered SQL, a ledger and no answer."
    ),
    variants={
        "v1": (
            "State the answer to the question in one or two sentences, using the query result "
            "below.\n\n"
            "Rules:\n"
            "- Lead with the number or the name that answers the question.\n"
            "- Use the result exactly as given. Do not round, re-derive or estimate.\n"
            "- If the result is empty, say that no rows matched.\n"
            "- Do not add caveats about accuracy, data quality or your own confidence. "
            "Reliability is reported separately and is not yours to assert.\n"
            "- No preamble, no restating of the question, no description of the SQL."
        ),
    },
)


#: Stage → rewriter prompt. ``facet_schema`` absent: searches raw question;
#: ``FACET_SCHEMA_QUERY`` stays in the registry (hashed) as an unsent baseline.
FACET_QUERY_PROMPTS: Mapping[str, str] = {
    "facet_term": "facet_term_query",
    "facet_metric": "facet_metric_query",
    "facet_entity": "facet_entity_query",
    "facet_example": "facet_example_query",
}


#: Name → prompt. Enumerated so :func:`prompt_set_hash` covers every prompt.
PROMPT_REGISTRY: Mapping[str, Prompt] = {
    p.name: p
    for p in (
        ANALYST,
        BI_SCOPE,
        NARRATE,
        FACET_SCHEMA_QUERY,
        FACET_TERM_QUERY,
        FACET_METRIC_QUERY,
        FACET_ENTITY_QUERY,
        FACET_EXAMPLE_QUERY,
    )
}

#: Every prompt at its declared default.
DEFAULT_VARIANTS: Mapping[str, str] = {n: p.default for n, p in PROMPT_REGISTRY.items()}


def prompt_text(name: str, variants: Mapping[str, str] | None = None) -> str:
    """Active text of one prompt. Raises on an unknown name."""
    prompt = PROMPT_REGISTRY.get(name)
    if prompt is None:
        raise KeyError(
            f"no prompt named {name!r}; declared: {sorted(PROMPT_REGISTRY)}. "
            "Register it here — a prompt sent from outside this registry is a treatment the "
            "run's own prompt_set_hash does not cover."
        )
    return prompt.text((variants or {}).get(name))


def select(overrides: Mapping[str, str] | None = None) -> dict[str, str]:
    """Active variant of every prompt, defaults filled in. Total over the registry."""
    active = dict(DEFAULT_VARIANTS)
    for name, variant in (overrides or {}).items():
        if name not in PROMPT_REGISTRY:
            raise KeyError(
                f"cannot select a variant for unknown prompt {name!r}; declared: "
                f"{sorted(PROMPT_REGISTRY)}"
            )
        PROMPT_REGISTRY[name].text(variant)  # raises here, not three stages later
        active[name] = variant
    return active


def prompt_set_hash(overrides: Mapping[str, str] | None = None) -> str:
    """Digest of active variant names and their text, sorted by prompt name.

    Both halves: names alone miss in-place edits; text alone collapses identical
    variants that later diverge.
    """
    active = select(overrides)
    parts: list[str] = []
    for name in sorted(PROMPT_REGISTRY):
        variant = active[name]
        parts.append(f"{name}\x1e{variant}\x1e{PROMPT_REGISTRY[name].text(variant)}")
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:16]


def unknown_prompts(names: Mapping[str, str] | None) -> list[str]:
    """Override names not in the registry (report rather than raise)."""
    return sorted(n for n in (names or {}) if n not in PROMPT_REGISTRY)
