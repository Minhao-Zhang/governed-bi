"""Every prompt the engine sends, declared in one place, with one hash over the set.

**Why this exists, in one sentence:** ``prompt_set_hash`` was
``_digest(SYSTEM_PROMPT)`` — a digest of *one* prompt — and the engine is about to have
five, so two runs whose guard prompt differed would have reported the same
``prompt_set_hash`` and comparability would have cleared the pair the second run existed to
isolate.

That is not a hypothetical. ``register/knobs.py`` already carries the same lesson about
``llm_reasoning_effort``: two v1 ladders differed **only** in that field, it was recorded
nowhere, comparability cleared them, and effort moved the baseline arm +2.5pp against a 2.3pp
detection threshold. And the field this module feeds is declared in ``record.py`` with the
sentence *"a fixed field list is exactly how prompt identity went unhashed in the first
place"*. A prompt is a treatment. An unhashed treatment is an unquotable run.

**The registry is the authority, and the hash is over the whole set.** Adding a prompt
without registering it is the failure this prevents, so the prompts live *here* rather than
beside their callers, and a caller asks for one by name. That is the same shape as
``register/stages.py`` for stage names and ``register/knobs.py`` for knobs: one vocabulary,
declared where it can be enumerated, imported by both the serve path and the eval harness.

**Variants are first-class, because comparing prompts is the point.** The user's requirement
is *"at every single stage, this prompt needs to be version controlled, and we can perform
different analysis and comparison experiment on the different prompts"*. So a
:class:`Prompt` holds a mapping of named variants and a declared default, ``select`` resolves
a variant set, and :func:`prompt_set_hash` digests **which variant of every prompt was
active** — not the default set. An experiment that swaps one variant therefore gets a
different hash automatically, with nobody remembering to update anything.

**Text and pure functions only: no I/O, no settings, no model, no imports outside stdlib.**
Both the serve path and the eval harness import it, same constraint ``stages.py`` states, for
the same reason.
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

    ``stage`` is a :class:`~governed_bi.register.stages.Stage` **value**, held as a plain string
    rather than the enum member. This module must not import ``stages`` — nothing forces that,
    but a registry importing a registry is how the dependency-free constraint erodes — and the
    coupling is checked instead by ``tests/serve/test_prompt_registry.py``, which asserts every
    ``stage`` here is a declared ``Stage``. A wrong string fails there rather than at import in
    a bare interpreter.

    ``why`` is required and is not documentation: a prompt whose purpose nobody wrote down is a
    prompt nobody can write a second variant *of*, and the whole point of the variants mapping
    is that somebody will.
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


#: The analyst that writes the SQL. The only prompt v2 had, moved here unchanged.
#:
#: **The text is byte-identical to what ``serve/tools.py`` held**, deliberately: this commit
#: moves the prompt and changes the hash's *derivation*, and a simultaneous wording change
#: would make the resulting hash difference impossible to attribute. The two naming rules in it
#: are load-bearing — ADR 0008 P8/D10 records that omitting them produced refusals naming the
#: wrong cause, because ``check()`` gets no ``default_schema``, so an unqualified
#: ``FROM customers`` keys as ``customers`` while ``licensed`` holds ``beer_factory.customers``
#: and the table layer refuses "the model asked for a table it may not see" for what is really
#: "the model omitted a schema nobody told it to write". Quoting is asked for even though
#: ``canonicalise`` adds quotes itself, because it cannot fix a statement that does not parse:
#: ``FROM airline.Air Carriers`` is a syntax error before any of this runs, and that table is
#: real.
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
    },
)


#: The guard's scope gate: is this question a BI task at all?
#:
#: **Deliberately not an injection defence, on the maintainer's explicit instruction** — *"There's
#: not much worry about prompt injection, as this would be designed to be an internal tool for the
#: company."* The five deterministic rules in ``govern/guard.py`` are the injection surface and
#: they are unchanged; this one answers a different question, which is whether the turn is the
#: kind of thing this system is for. So the prompt asks for one word and nothing else: a gate that
#: reasons is a gate whose output has to be parsed, and a parse that can fail is a gate that can
#: fail open on a formatting change.
#:
#: ``v1`` requires the affirmative token. That direction matters: keying on the *negative* would
#: make any unexpected reply — an apology, a clarifying question, an empty completion — read as
#: "in scope", so the gate would fail **open** exactly when the model was confused. Requiring
#: ``YES`` fails closed instead, and a refusal a user can see and rephrase is recoverable where a
#: silently skipped gate is not.
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


#: The five facet query rewriters — one prompt each, on purpose.
#:
#: **What they are for.** A user asks *"what is the average star rating for restaurants in this
#: area"*; a schema summary reads *"stores basic information about restaurants"*. Those two
#: strings are not close, lexically or semantically, and the facet is searching with the raw
#: question. The rewrite turns the question into something shaped like the thing being searched
#: for — *"which tables and schemas hold restaurant records and their ratings"* — which is the
#: maintainer's own framing: *"a deterministic way of aggregating different strings together to
#: make them more semantically close to the thing we are trying to search."*
#:
#: **Five prompts and not one parameterised prompt.** Each facet searches a different kind of
#: object and each will be tuned against a different number; the registry exists so a variant of
#: one can be compared without moving the others, and a single prompt with the facet interpolated
#: would make that impossible. It costs four more entries and buys independent versioning.
#:
#: **Each asks for search text, never for an answer.** The output is fed to BM25 and to an
#: embedder, so a sentence of reasoning would pollute both; the instruction is to emit only the
#: query. Empty or refused output falls back to the raw question, and the ``extraction`` channel
#: is then marked failed rather than ran — a fallback that reported as a run is exactly how, per
#: ADR 0005 §2.3, an arm quietly becomes v1's single-pass retrieval.
_REWRITE_TAIL = (
    "\n\nReply with the search text only — no preamble, no explanation, no quotes. "
    "Keep it under 30 words. If the question gives you nothing to work with, reply with "
    "the question unchanged."
)

FACET_SCHEMA_QUERY = Prompt(
    name="facet_schema_query",
    stage="facet_schema",
    why="Turns the question into a description of the tables and schemas that would answer it.",
    variants={
        "v1": (
            "You rewrite a business question into search text for finding database TABLES and "
            "SCHEMAS.\n\nDescribe the kind of tables and schemas that would be needed to answer "
            "the question — the entities they store, not the calculation asked for. Use the "
            "vocabulary a data catalogue would use." + _REWRITE_TAIL
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

#: ``Stage`` value → the prompt that rewrites the question for it. Read by ``serve/nodes/facets``.
#:
#: A mapping rather than a naming convention (``f"{stage}_query"``): a convention silently returns
#: nothing for a stage nobody wrote a prompt for, and this raises at import via the registry's own
#: coherence check instead.
#: The closing sentence, when the agent did not write one.
#:
#: **Deliberately narrow, because the wide version of this job is already done.** The agent's
#: last message normally narrates the result for free, and ``serve/nodes/narrate.py`` adopts it
#: when it exists. This prompt runs on the remainder: a loop that ended on a tool call, or on
#: reasoning blocks with no text. So it is given the question, the statement and the rows, and
#: nothing else — it is not re-deciding anything, it is reading a table out loud.
#:
#: The instruction not to add caveats is load-bearing rather than stylistic. A model handed a
#: bare result set reaches for "this may not reflect…", and a hedge invented here would be a
#: reliability claim with no measurement behind it, on the most-read line in the interface —
#: the same defect ADR 0007 §3 refuses for the tier badge.
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


FACET_QUERY_PROMPTS: Mapping[str, str] = {
    "facet_schema": "facet_schema_query",
    "facet_term": "facet_term_query",
    "facet_metric": "facet_metric_query",
    "facet_entity": "facet_entity_query",
    "facet_example": "facet_example_query",
}


#: The registry. Name → prompt.
#:
#: One dict rather than module-level constants, because :func:`prompt_set_hash` has to be able
#: to enumerate *every* prompt — and a constant somebody forgot to add to a list is precisely
#: the "fixed field list" failure this module exists to end.
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

#: Every prompt at its declared default. What a run uses unless an experiment says otherwise.
DEFAULT_VARIANTS: Mapping[str, str] = {n: p.default for n, p in PROMPT_REGISTRY.items()}


def prompt_text(name: str, variants: Mapping[str, str] | None = None) -> str:
    """The active text of one prompt.

    Raises on an unknown name rather than returning ``""``. An empty system prompt is the
    failure ``serve/scripted_model.py`` records: emptying ``SYSTEM_PROMPT`` left the suite
    byte-identical to baseline, so nothing in the repository could observe what reached the
    model. A missing prompt must be loud.
    """
    prompt = PROMPT_REGISTRY.get(name)
    if prompt is None:
        raise KeyError(
            f"no prompt named {name!r}; declared: {sorted(PROMPT_REGISTRY)}. "
            "Register it here — a prompt sent from outside this registry is a treatment the "
            "run's own prompt_set_hash does not cover."
        )
    return prompt.text((variants or {}).get(name))


def select(overrides: Mapping[str, str] | None = None) -> dict[str, str]:
    """The active variant of every prompt, defaults filled in.

    Total over the registry on purpose: the hash must describe the whole set, so a run that
    overrides one prompt and a run that overrides none differ in exactly one entry rather than
    in the number of entries.
    """
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
    """A digest of **which variant of every prompt is active, and of their text**.

    Both halves matter and each alone has a failure mode. Hashing only the variant *names*
    would make an edit to ``v1``'s wording invisible — the exact "prompt identity went
    unhashed" defect. Hashing only the *text* would collapse two variants that happen to be
    identical today and then silently diverge, so a run could not say which one it asked for.

    Sorted by name, so the hash depends on the set and not on dict insertion order — a hash
    that changed when somebody reordered a literal would be a comparability key nobody could
    trust.
    """
    active = select(overrides)
    parts: list[str] = []
    for name in sorted(PROMPT_REGISTRY):
        variant = active[name]
        parts.append(f"{name}\x1e{variant}\x1e{PROMPT_REGISTRY[name].text(variant)}")
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:16]


def unknown_prompts(names: Mapping[str, str] | None) -> list[str]:
    """Override names this registry does not declare. For a caller that wants to report rather
    than raise — the eval driver reads variant selections from a config file, and a typo there
    should name itself instead of ending the run three stages in."""
    return sorted(n for n in (names or {}) if n not in PROMPT_REGISTRY)
