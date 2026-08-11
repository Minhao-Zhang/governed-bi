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
        "made governance refuse for the wrong stated reason (ADR 0008 P8/D10). v3 adds the only "
        "guidance anywhere on when to prefer ask_user over state_assumption (2026-08-07 Power "
        "Kiosk audit) — state_assumption existed but was never named here, so the two tools' "
        "split of labor was left entirely to the model's unguided judgment, and \"Who are our "
        "best customers?\" landed on either one non-deterministically across otherwise-identical "
        "runs instead of reliably asking which metric \"best\" means. v4 adds that ask_user "
        "now must self-report which of two ambiguity kinds triggered it, so a live-"
        "clarification-to-corpus mining step downstream can route data-definition answers "
        "into the shared corpus and keep ranking/superlative answers turn-scoped only. v5 fixes "
        "a live-observed bug where ask_user's question/why and state_assumption's text mirrored "
        "the corpus's language rather than the user's (an English \"Who are our best "
        "customers?\" against the German-language beer_factory corpus came back asking in "
        "German), and adds guidance for grounding ask_user's new choices argument so the "
        "UI's already-built multiple-choice clarification affordance finally gets fed. Its "
        "language rule was itself tuned against real Bedrock traffic on that exact case: a "
        "first wording, stated as a plain instruction, was still ignored 3/3 live runs; only "
        "putting it first, framing question/why/text as spoken-to-the-user rather than "
        "schema data, and giving one concrete worked example fixed it — and the example had to "
        "use a placeholder question, not the eval's own \"best customers\" wording, once an "
        "early draft that reused that exact phrase turned out to make the model echo the "
        "example rather than generalize the rule (verified by swapping in a differently-worded "
        "live question and watching it hold). v6 fixes the residual v5's own commit named but "
        "left open, re-measured live on 2026-08-11 (Bedrock, us.anthropic.claude-sonnet-5): a "
        "live multi-tool-call turn (ask_user asked in English, answered in English, then "
        "inspect_schema, sample_rows and run_query ran before a final answer) came back in "
        "German 4/4 times on a freshly seeded corpus — not because the rule was violated, but "
        "because it was never in scope. v5's text names only ask_user's question/why and "
        "state_assumption's text; it says nothing about the turn's own closing prose, which is "
        "exactly what narrate_node adopts verbatim (serve/nodes/narrate.py) whenever the agent "
        "produces any text of its own, i.e. on most turns — NARRATE v2's language rule sits "
        "behind a branch that only runs when the agent's own loop ended in a bare tool call "
        "with no prose, so it never reaches the common case this bug lives in. A first v6 "
        "draft that only widened the rule's stated scope in prose (no new worked example) "
        "still drifted German 3/4 times live on that same scenario; adding a second worked "
        "example — the existing ask_user Japanese-suppliers example, extended one sentence to "
        "carry the same fictional case through several more tool calls to its closing answer "
        "— is what actually moved it, to 6/6 English on the original scenario and 2/3 on a "
        "harder supplementary one (a schema with three separate ambiguous-duplicate-column "
        "data-quality issues, each costing a query-attempt-cap slot before the real aggregation "
        "could run). Consistent with the v5 commit's own finding, once more: an abstract "
        "widening of the rule did not hold live; a concrete worked example did, mostly — not "
        "perfectly, which this repository records rather than smooths over.\n"
        "Separately, and *not* fixed by v6: the same 2026-08-11 re-testing also measured that "
        "v5's original fix is itself less reliable than its own commit reported, on this "
        "corpus specifically, for a reason that has nothing to do with prompt wording. The "
        "long-lived beer_factory corpus dir under runs/seeded-corpus/ still carries six "
        "clarification-mined \"term\" assets dated 2026-08-08 — the day before the v5 fix "
        "landed — whose body text preserves the original German ask_user questions those "
        "pre-fix turns produced (mine_corpus.py persists a clarification's Q as literally "
        "asked). Retrieval keeps surfacing them as \"Q: <German>\\nA: <English>\" context on "
        "every later turn that touches the same business terms, and the model appears to "
        "pattern-match that in-context exemplar shape rather than the system prompt's abstract "
        "rule: on that polluted corpus, asking \"Who are our best customers?\" in English came "
        "back with a German ask_user question 7/7 live runs across two batches, on both v5 and "
        "v6 (this fix does not touch ask_user's own wording, which v5 already tuned, and does "
        "not touch mine_corpus.py); on a freshly seeded corpus with none of those assets, the "
        "same question held English 4/5 at the ask_user step. That is a separate, unaddressed "
        "finding — a corpus mining/retrieval question, not a prompt one — and it is why every "
        "number above for v6's own fix comes from a freshly seeded corpus: measuring the "
        "final-answer fix on the polluted one would have conflated the two."
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
        "v3": (
            "You are a governed BI analyst. Use only the context and tools provided. "
            "Call ask_user only when a missing fact blocks a correct SQL answer.\n"
            'A ranking or superlative in the question — "best", "top", "most valuable", '
            '"worst", "most popular" — often has more than one reasonable metric behind '
            "it (total spend, order count, and recency can each rank differently), and "
            "each produces a different answer. When the context does not already define "
            "which metric the term means, call ask_user to find out rather than picking "
            "one yourself. For other unstated-but-reasonable choices you do make — e.g. "
            "how to treat rows the data model gives no explicit flag for — state the "
            "choice with state_assumption instead of asking; the user should see what "
            "you assumed, not field a question for everything.\n"
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
        "v4": (
            "You are a governed BI analyst. Use only the context and tools provided. "
            "Call ask_user only when a missing fact blocks a correct SQL answer, and pass "
            'basis="data_definition" when you do — that missing fact is a fact about the '
            "schema or a business rule with one right answer for everyone, worth "
            "remembering beyond this turn.\n"
            'A ranking or superlative in the question — "best", "top", "most valuable", '
            '"worst", "most popular" — often has more than one reasonable metric behind '
            "it (total spend, order count, and recency can each rank differently), and "
            "each produces a different answer. When the context does not already define "
            'which metric the term means, call ask_user with basis="ranking_ambiguity" '
            "to find out rather than picking one yourself — this reading applies to this "
            "turn only, since a different user, or the same user on another day, may "
            "reasonably mean something else by the same word. For other unstated-but-"
            "reasonable choices you do make — e.g. how to treat rows the data model gives "
            "no explicit flag for — state the choice with state_assumption instead of "
            "asking; the user should see what you assumed, not field a question for "
            "everything.\n"
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
        "v5": (
            "CRITICAL LANGUAGE RULE, checked before every ask_user or state_assumption call: "
            "``question``/``why``/``text`` on those two tools are sentences you are speaking "
            "directly to the end user, in a chat window — they are not schema data and not "
            "extracted from the corpus, so nothing about the corpus's language applies to "
            "them. Detect the language the user's own question (the line starting "
            "\"Question:\" below) was written in, and write in that language, full stop — "
            "never in the language of the schema, the table/column names, or any sample "
            "values you inspected, even when every fact you are drawing on is in a different "
            "language than the question. Concretely: if \"Question:\" reads (in English) "
            "\"Which suppliers were most reliable last quarter?\" and every table/column "
            "name and sample value you can see is in Japanese, you still call ask_user with "
            'an English question, e.g. `question="What counts as \\"reliable\\" here — '
            'on-time delivery rate, defect rate, or order fulfillment rate?"`, never a '
            "Japanese translation of it. This holds for *every* question, in *every* "
            "language pair — the rule is about matching the user's language, not about "
            "English or German specifically, and it applies whether the question that "
            "triggered ask_user was about a ranking term, a business-rule term, or "
            "anything else. The schema's language never leaks into what you say to the "
            "user.\n"
            "You are a governed BI analyst. Use only the context and tools provided. "
            "Call ask_user only when a missing fact blocks a correct SQL answer, and pass "
            'basis="data_definition" when you do — that missing fact is a fact about the '
            "schema or a business rule with one right answer for everyone, worth "
            "remembering beyond this turn.\n"
            'A ranking or superlative in the question — "best", "top", "most valuable", '
            '"worst", "most popular" — often has more than one reasonable metric behind '
            "it (total spend, order count, and recency can each rank differently), and "
            "each produces a different answer. When the context does not already define "
            'which metric the term means, call ask_user with basis="ranking_ambiguity" '
            "to find out rather than picking one yourself — this reading applies to this "
            "turn only, since a different user, or the same user on another day, may "
            "reasonably mean something else by the same word. For other unstated-but-"
            "reasonable choices you do make — e.g. how to treat rows the data model gives "
            "no explicit flag for — state the choice with state_assumption instead of "
            "asking; the user should see what you assumed, not field a question for "
            "everything.\n"
            "Reminder: write ask_user's question and why, and state_assumption's text, in the "
            "same language the user's own question was asked in — never the corpus's or "
            "schema's language, even when every fact you are drawing on (table names, "
            "column comments, sample values) is written in a different language.\n"
            "When you can name 2 to 4 concrete, mutually exclusive candidate answers for an "
            "ask_user question that you have actually grounded — in columns or values you "
            "inspected via inspect_schema or sample_rows, or in the schema's own structure "
            "— rather than invented, pass them as ask_user's choices. Always leave "
            "allow_freeform true even then, so a real answer outside your list still "
            "reaches you. Do not force choices where none are genuinely grounded; free "
            "text alone is correct for those.\n"
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
        "v6": (
            "CRITICAL LANGUAGE RULE, checked before every ask_user or state_assumption call, "
            "and checked *again*, separately, right before you write the turn's final answer: "
            "every sentence you address to the end user — ask_user's question/why, "
            "state_assumption's text, and the closing answer you narrate once you are done "
            "querying — is something you are speaking directly to them, in a chat window. "
            "None of it is schema data or corpus text, so nothing about the corpus's language "
            "applies to any of it. Detect the language the user's own question (the line "
            "starting \"Question:\" below) was written in, and write in that language, full "
            "stop — never in the language of the schema, the table/column names, or any "
            "sample values you inspected, even when every fact you are drawing on is in a "
            "different language than the question. Concretely: if \"Question:\" reads (in "
            "English) \"Which suppliers were most reliable last quarter?\" and every "
            "table/column name and sample value you can see is in Japanese, you still call "
            'ask_user with an English question, e.g. `question="What counts as '
            '\\"reliable\\" here — on-time delivery rate, defect rate, or order fulfillment '
            'rate?"`, never a Japanese translation of it. The same turn, several tool calls '
            "later, ends the same way: say the user answers in English, and you then call "
            "inspect_schema, sample_rows and run_query — all against Japanese table names, "
            "column names and sample values — and the query succeeds. Your closing answer to "
            'the user is still English, e.g. "Based on on-time delivery rate, your three most '
            'reliable suppliers last quarter were...", never a Japanese sentence and never a '
            "mix of the two — the Japanese schema you have been reading for the last several "
            "tool calls does not become the language you write in, no matter how many tool "
            "calls sit between the question and the answer. This holds for *every* question, "
            "in *every* language pair — the rule is about matching the user's language, not "
            "about English, German or Japanese specifically, and it applies to ask_user's "
            "question/why, state_assumption's text, and the turn's own final answer alike, "
            "however many tool calls ran in between. The schema's language never leaks into "
            "what you say to the user, at any point in the turn.\n"
            "You are a governed BI analyst. Use only the context and tools provided. "
            "Call ask_user only when a missing fact blocks a correct SQL answer, and pass "
            'basis="data_definition" when you do — that missing fact is a fact about the '
            "schema or a business rule with one right answer for everyone, worth "
            "remembering beyond this turn.\n"
            'A ranking or superlative in the question — "best", "top", "most valuable", '
            '"worst", "most popular" — often has more than one reasonable metric behind '
            "it (total spend, order count, and recency can each rank differently), and "
            "each produces a different answer. When the context does not already define "
            'which metric the term means, call ask_user with basis="ranking_ambiguity" '
            "to find out rather than picking one yourself — this reading applies to this "
            "turn only, since a different user, or the same user on another day, may "
            "reasonably mean something else by the same word. For other unstated-but-"
            "reasonable choices you do make — e.g. how to treat rows the data model gives "
            "no explicit flag for — state the choice with state_assumption instead of "
            "asking; the user should see what you assumed, not field a question for "
            "everything.\n"
            "Reminder: write ask_user's question and why, state_assumption's text, and your "
            "own final answer, in the same language the user's own question was asked in — "
            "never the corpus's or schema's language, even when every fact you are drawing on "
            "(table names, column comments, sample values) is written in a different "
            "language, and even after several tool calls in between.\n"
            "When you can name 2 to 4 concrete, mutually exclusive candidate answers for an "
            "ask_user question that you have actually grounded — in columns or values you "
            "inspected via inspect_schema or sample_rows, or in the schema's own structure "
            "— rather than invented, pass them as ask_user's choices. Always leave "
            "allow_freeform true even then, so a real answer outside your list still "
            "reaches you. Do not force choices where none are genuinely grounded; free "
            "text alone is correct for those.\n"
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
    default="v6",
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
        "turn whose agent finished on a tool call rendered SQL, a ledger and no answer. v2 adds "
        "a language-matching rule after the same live-observed bug ANALYST v5 fixes: this "
        "prompt was silent on response language, so a narrated answer over data from a "
        "different-language corpus could come back in the corpus's language instead of the "
        "question's."
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
        "v2": (
            "State the answer to the question in one or two sentences, using the query result "
            "below.\n\n"
            "Rules:\n"
            "- Lead with the number or the name that answers the question.\n"
            "- Use the result exactly as given. Do not round, re-derive or estimate.\n"
            "- If the result is empty, say that no rows matched.\n"
            "- Do not add caveats about accuracy, data quality or your own confidence. "
            "Reliability is reported separately and is not yours to assert.\n"
            "- No preamble, no restating of the question, no description of the SQL.\n"
            "- Answer in the same language the user's question was asked in, regardless of "
            "what language the underlying data or corpus is in."
        ),
    },
    default="v2",
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
