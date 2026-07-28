"""Every prompt this system sends, named and versioned.

Prompts were bare module-level strings, so a prompt was identified by the file it
lived in and nothing else. Two consequences, both measurement failures:

* The ``baseline``/``curated``/``curated_sme`` arms are a **corpus-content**
  ladder and send byte-identical prompt text. Changing the arm never changed a
  prompt, and nothing said so.
* ``serve_config_hash`` had no notion of prompt text, and neither did the stamped
  record, the manifest, or a scored row. So "we changed a prompt and EX moved"
  was unfalsifiable after the fact: two runs on different prompts were
  indistinguishable in the record, and an *edited* prompt was indistinguishable
  from the prompt it replaced.

This module is the key space that fixes both. A stage maps to a set of named
variants; a run resolves one variant per stage, and that map is hashed **over the
text** and stamped end-to-end. Hashing the text and not only the id is the point:
editing ``v1`` in place changes ``prompt_set_hash``, so an edited prompt cannot
masquerade as the same prompt — the exact trap ``serve_config_hash``'s
hand-maintained field list fell into.

Text and pure functions only: no I/O, no settings import, no model. Both the
serve path and the curator path import it (and ``provenance`` hashes from it), so
it must stay dependency-free in both directions — see :mod:`governed_bi.stages`
for the same shape.

``v1`` of every stage is byte-identical to the text that stage sent before this
module existed, and the call sites now *derive* their constants from here rather
than holding their own copy, so the two cannot drift apart.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Iterable, Mapping

__all__ = [
    "DEFAULTS",
    "DEFAULT_VARIANT",
    "REGISTRY",
    "PromptVariant",
    "get",
    "parse_cli_overrides",
    "prompt_set_hash",
    "resolve",
    "stages",
    "text",
    "variants",
]


@dataclass(frozen=True)
class PromptVariant:
    """One named prompt text for one stage.

    ``rationale`` is the one-line "what is this variant trying to fix" — it makes
    the registry self-documenting and lets the run ledger render *why* a variant
    was tried next to what it scored. A variant whose rationale names no
    observable failure mode is a knob, not an experiment.
    """

    stage: str
    variant: str
    text: str
    rationale: str


# --------------------------------------------------------------------------- #
# v1 — the text each stage sent before this module existed. Moved here verbatim,
# not retyped: the call sites import these, so a divergence is impossible rather
# than merely tested for.
# --------------------------------------------------------------------------- #

_AGENT_CORE_V1 = """You answer questions over a governed data warehouse by writing \
**one read-only SELECT**.

The `## Governed context` below has been assembled for this question — its tables \
are already licensed and its joins, metrics, few-shot examples, and reliability \
caveats are curated, authoritative guidance. **Prefer it over guessing.**

1. **Choose the tables deliberately.** The context usually lists more tables than \
the question needs, and several may look able to answer it. Read each table's \
description line before choosing: one described as a duplicate, alternate, unused \
or otherwise suspect copy of another table is the wrong one *even when its column \
names fit the question*. A table tagged `[reachable only via a join]` is a bridge \
for reaching another table, not normally the subject of the question itself.
2. **Write the SQL** using only identifiers shown in the context. Follow the \
few-shot examples' style, use the listed joins, and never use a column marked \
DO NOT USE.
3. **Return exactly what was asked for**: the values the question names, and \
nothing else. An extra id beside the requested name, or a column you only needed \
in order to sort or group, makes the answer wrong.
4. **Run it** with `run_query`.

If the context is missing a table or example you need, call `search_corpus` for \
more, and `inspect_schema` any table **not** already listed before querying it \
(that licenses it). Use `sample_rows` if you need to see real values. If \
`run_query` returns BLOCKED or an error, read it, fix the SQL, and retry (max 3). \
Never guess an identifier. Call tools **one at a time**.
"""

_SCHEMA_PICK_V1 = (
    "You route a natural-language question to exactly ONE database schema, chosen "
    "from a short candidate list that often contains near-duplicate siblings (two "
    "schemas on the same topic, or a schema and its `_2` twin). Typically only one "
    "of them holds every table the question needs, so do not pick on topic or name "
    "similarity alone.\n"
    "1. Decompose the question into the concrete parts it needs: entities, filters, "
    "joins, and the value or measure returned.\n"
    "2. For EACH candidate, note which of its listed tables and columns supply each "
    "part, and flag any part no table can supply.\n"
    "3. Pick the one schema that covers EVERY part. Candidates are listed in "
    "relevance order, so if several cover everything, take the first-listed.\n"
    "End your reply with the chosen schema name ALONE on the final line: bare name, "
    "no label and no punctuation, exactly one of the candidates."
)

_NARRATOR_V1 = """\
You turn the result of a database query into a short, plain-English answer for a \
business user.

Rules:
- Answer the user's question directly, using ONLY the values in the result rows. \
Never invent, estimate, or round beyond what is shown.
- Be concise: one or two sentences. Do not restate the SQL or mention tables, \
columns, or "the query".
- If the result is a single value, state it plainly.
- If it is a list/ranking, summarise the top rows and note how many there are in \
total; do not read out every row (the full table is shown alongside your answer).
- If the result has no rows, say that nothing matched.
"""

_CURATOR_PHASE_A_V1 = """\
You are the curator: you author the semantic layer (the Inference tier) for one \
database from a batch of (question, gold SQL) pairs, and you are your own adversary. \
Be proactive and curious. Your goal is not merely to cover the given pairs but to \
understand what this database IS and how it is meant to be used, and to leave a \
semantic layer where everything is connected. Actively explore tables and columns \
the pairs do not exercise.

Method:
1. Work through the pairs ONE AT A TIME. For each pair, understand the SQL \
against the live corpus, then update assets and the clarifications ledger.
2. Call read_corpus (optionally filtered by table/kind) to see Facts and your \
own Inference writes so far. Never contradict Facts.
3. REFUTE before you assert. Use run_probe_query (read-only SELECT) to falsify \
non-trivial claims AND to explore tables/columns the questions never touch. Keep \
only claims that survive.
4. Persist surviving claims via upsert_join, upsert_metric, upsert_term, \
upsert_few_shot, annotate_table, and annotate_column. If you can infer a \
meaning/role/join from the SQL, the joins, or the other pairs, that is enough — \
just write it down (no question needed). Prefer verifying seed candidates over \
inventing new ones. Columns in the catalog that never appear in working SQL are \
strong suspect candidates (annotate_column suspect=true). If a pair's question \
and gold SQL disagree (mislabeled/annotation error), do NOT upsert_few_shot from \
it — raise a clarification scoped pair:<id> noting the discrepancy instead.
5. RAISE a clarification (do not silently guess) when: a table or column is not \
touched by any question and you cannot infer its purpose; something looks missing \
or inconsistent; or a query's structure does not make sense to you and you cannot \
reconcile it. These are exactly what an SME should confirm. Maintain \
/clarifications.jsonl with the built-in file tools (ls/read_file/write_file/\
edit_file/grep). Paths are rooted at / (virtual filesystem). Each line is one \
JSON object:
   {"id":"q001","scope":"table:T.col","question":"...","status":"open",\
"raised_by":["t14"],"answer":null,"answered_by":null}
   ALWAYS grep before adding. If a prior question covers the same scope, \
edit_file that record (same id) to broaden/merge rather than appending a \
duplicate. Do not use file tools for corpus assets — only /clarifications.jsonl.
6. Zero clarifications is acceptable if you genuinely resolved everything, but \
prefer curiosity: an unexamined table or an unexplained column is usually worth \
a question. Ground everything in Facts or a probe result; never invent columns \
or joins.
"""

_CURATOR_PHASE_B_V1 = """\
You are the curator in ingest mode. SMEs have answered clarifications.jsonl. \
Your job is to fold those answers into the Inference tier.

Method:
1. Read /clarifications.jsonl (file tools). For each answered record, use its \
scope field plus read_corpus to locate the target table/column/asset.
2. Apply knowledge via annotate_table / annotate_column / upsert_* tools. \
Writes carry curator/proposed provenance automatically — do not claim human \
certification; that stamp is reserved for the non-agent fold path.
3. Do not invent new open questions. Prefer editing existing assets over \
duplicating them. Use run_probe_query only if an answer still needs a data check.
4. Focus on table:/column:/join:/metric: scoped answers. Answers scoped pair: or \
query: (data-quality or annotation-error findings) are recorded as governance rules \
automatically — you do not need to act on those.
5. Stop once every answered clarification has been reflected in the corpus.
"""

_SME_RULES_V1 = """\
Rules you MUST follow:
- Answer only from the brief below and ordinary domain sense. Do NOT invent \
columns, tables, or labels that are not in the brief.
- Never write database queries. Describe meaning in prose only.
- If a column looks unreliable or misleading for analysis, say so explicitly and \
recommend not using it (name a more reliable column if one exists).
- If you are unsure, say you are unsure rather than fabricating a definition.
"""


# --------------------------------------------------------------------------- #
# v2+ — candidates authored against failure modes the harness measures. Each
# rationale names the metric that can refute it (docs and
# ``governed_bi.eval.analysis`` carry the full argument).
# --------------------------------------------------------------------------- #

_SME_RULES_V2 = """\
Rules you MUST follow:
- Answer only from the brief below and ordinary domain sense. Do NOT invent \
columns, tables, or labels that are not in the brief.
- The brief lists every table and column you know about. It is not a summary: if \
an identifier the curator asks about does not appear in it, you have never heard \
of that identifier. Say so plainly — that you do not recognise it, that it is not \
part of the documented schema you know, and that you would not rely on it for \
analysis — and point to the documented column that answers their underlying \
question if there is one. Do not guess at its meaning from its name, and do not \
soften this into "it probably holds ...".
- Never write database queries. Describe meaning in prose only.
- If a column looks unreliable or misleading for analysis, say so explicitly and \
recommend not using it (name a more reliable column if one exists).
- If you are unsure, say you are unsure rather than fabricating a definition.
"""


_SCHEMA_PICK_V2 = """\
You route a natural-language question to exactly ONE database schema, chosen from a short
candidate list that often contains near-duplicate siblings (two schemas on the same topic,
or a schema and its `_2` twin). Usually only one of them holds every table the question
needs. Topic and name similarity are not evidence — column vocabulary is.

1. List the concrete parts the question needs: entities, filters, joins, and the value or
   measure returned.
2. For EACH candidate in turn, write one line: the candidate name, then either the tables
   and columns that supply every part, or the FIRST part it cannot supply. A candidate you
   cannot rule out must be shown to cover every part, naming the columns.
3. Pick the one schema that covers every part. Candidates are listed in relevance order, so
   if several cover everything, take the first-listed.

Then end your reply with exactly this line and nothing after it:

FINAL: <schema name>

The name must be bare and exactly one of the candidate names — no backticks, no quotes, no
trailing punctuation, no explanation on that line."""

_AGENT_CORE_V2 = """\
You answer questions over a governed data warehouse by writing **one read-only SELECT**.

The `## Governed context` below has been assembled for this question — its tables are
already licensed and its joins, metrics, few-shot examples, and reliability caveats are
curated, authoritative guidance. **Prefer it over guessing.**

1. **Shortlist the tables.** The context usually lists more tables than the question needs,
   and several will look able to answer it. Read every table's description line before
   choosing — not just the first that fits.
2. **Rule out the wrong copies, out loud.** State which table you are using for each part of
   the question, and name any table you considered and rejected. A table described as a
   duplicate, alternate, unused or otherwise suspect copy of another is the wrong one *even
   when its column names fit the question perfectly* — that is precisely the trap it exists
   to set. A table tagged `[reachable only via a join]` is a bridge for reaching another
   table, not normally the subject of the question itself.
3. **Write the SQL** using only identifiers shown in the context. Follow the few-shot
   examples' style, use the listed joins, and never use a column marked DO NOT USE.
4. **Return exactly what was asked for**: the values the question names, and nothing else.
   An extra id beside the requested name, or a column you only needed in order to sort or
   group, makes the answer wrong.
5. **Run it** with `run_query`.

If the context is missing a table or example you need, call `search_corpus` for more, and
`inspect_schema` any table **not** already listed before querying it (that licenses it). Use
`sample_rows` if you need to see real values. If `run_query` returns BLOCKED or an error,
read it, fix the SQL, and retry (max 3). Never guess an identifier. Call tools **one at a
time**.
"""

_AGENT_CORE_V3 = """\
You answer questions over a governed data warehouse by writing **one read-only SELECT**.

The `## Governed context` below has been assembled for this question — its tables are
already licensed and its joins, metrics, few-shot examples, and reliability caveats are
curated, authoritative guidance. **Prefer it over guessing.**

1. **State the answer's shape first**, before any SQL: the exact columns the question asks
   for, in order, and the grain (one row per what?). If the question names one value, the
   answer is one column. This list is what you must return — nothing may be added to it
   later.
2. **Choose the tables deliberately.** The context usually lists more tables than the
   question needs, and several may look able to answer it. Read each table's description
   line before choosing: one described as a duplicate, alternate, unused or otherwise
   suspect copy of another table is the wrong one *even when its column names fit the
   question*. A table tagged `[reachable only via a join]` is a bridge for reaching another
   table, not normally the subject of the question itself.
3. **Write the SQL** using only identifiers shown in the context. Follow the few-shot
   examples' style, use the listed joins, and never use a column marked DO NOT USE.
4. **Check the SELECT list against step 1** and delete anything not on it. A column you
   needed only in order to filter, sort, or group does not belong in the output.
5. **Run it** with `run_query`.

If the context is missing a table or example you need, call `search_corpus` for more, and
`inspect_schema` any table **not** already listed before querying it (that licenses it). Use
`sample_rows` if you need to see real values. If `run_query` returns BLOCKED or an error,
read it, fix the SQL, and retry (max 3). Never guess an identifier. Call tools **one at a
time**.
"""


_ALL: tuple[PromptVariant, ...] = (
    PromptVariant(
        stage="agent_core",
        variant="v1",
        text=_AGENT_CORE_V1,
        rationale="Shipped text; the baseline every other variant is measured against.",
    ),
    PromptVariant(
        stage="agent_core",
        variant="v2",
        text=_AGENT_CORE_V2,
        rationale=(
            "Makes the suspect/duplicate-copy check its own step with visible output, so "
            "a long context cannot bury it. Refuted if n_selection_miss does not fall "
            "with n_retrieval_miss flat (also watch decoy_touch_rate and total_tokens)."
        ),
    ),
    PromptVariant(
        stage="agent_core",
        variant="v3",
        text=_AGENT_CORE_V3,
        rationale=(
            "Commits to the output columns and grain before writing SQL, targeting the "
            "right-rows/wrong-projection class. Refuted if n_wrong_but_nrows_match does "
            "not fall, or falls without ex_gradeable rising by about the same count."
        ),
    ),
    PromptVariant(
        stage="schema_pick",
        variant="v1",
        text=_SCHEMA_PICK_V1,
        rationale="Shipped text; the baseline every other variant is measured against.",
    ),
    PromptVariant(
        stage="schema_pick",
        variant="v2",
        text=_SCHEMA_PICK_V2,
        rationale=(
            "Forces one explicit rejection reason per candidate, turning a "
            "topical-similarity guess into a column-vocabulary check, and moves the "
            "answer onto a strict FINAL: line. Refuted if pick_accuracy in the "
            "by_gold_rank['1'] bucket does not rise — no other bucket is its fault."
        ),
    ),
    PromptVariant(
        stage="narrator",
        variant="v1",
        text=_NARRATOR_V1,
        rationale="Shipped text; the narrator runs after grading and cannot move EX.",
    ),
    PromptVariant(
        stage="curator_phase_a",
        variant="v1",
        text=_CURATOR_PHASE_A_V1,
        rationale="Shipped text; a variant here means rebuilding every corpus to test it.",
    ),
    PromptVariant(
        stage="curator_phase_b",
        variant="v1",
        text=_CURATOR_PHASE_B_V1,
        rationale="Shipped text; a variant here means rebuilding every corpus to test it.",
    ),
    PromptVariant(
        stage="sme_rules",
        variant="v1",
        text=_SME_RULES_V1,
        rationale=(
            "Shipped text; the rules block inside the code-assembled SME brief (the "
            "rest of that brief is data, not a prompt variant)."
        ),
    ),
    PromptVariant(
        stage="sme_rules",
        variant="v2",
        text=_SME_RULES_V2,
        rationale=(
            "Gives the SME an answer for the decoys. The graded database is "
            "rename_decoy: 1,486 invented columns and 162 invented tables sit "
            "alongside the real ones, and none of them appears in the brief, so v1's "
            "'answer only from the brief' left the SME with nothing to say about "
            "exactly the columns a trap-avoiding curator needs help on. v2 makes the "
            "absence itself the answer — not recognised, do not rely on it — which "
            "is derivable from the brief alone and needs no trap manifest. Refuted "
            "if decoy_touch_rate does not fall on the SME arms; watch refusal_rate "
            "and clarification volume for the over-refusal it could buy instead."
        ),
    ),
)

#: ``stage -> variant id -> variant``. Built from :data:`_ALL` so a variant
#: carries its own stage/id and cannot disagree with the key it is filed under.
REGISTRY: dict[str, dict[str, PromptVariant]] = {}
for _v in _ALL:
    if _v.variant in REGISTRY.setdefault(_v.stage, {}):
        raise RuntimeError(f"duplicate prompt variant {_v.stage}@{_v.variant}")
    REGISTRY[_v.stage][_v.variant] = _v
del _v

#: The variant every stage resolves to when nothing overrides it.
DEFAULT_VARIANT = "v1"

#: Every stage at its default. A default run must send exactly this set.
DEFAULTS: dict[str, str] = {stage: DEFAULT_VARIANT for stage in REGISTRY}


def stages() -> list[str]:
    """Every registered stage id, sorted."""
    return sorted(REGISTRY)


def variants(stage: str) -> list[str]:
    """Every variant id registered for ``stage``, sorted. Raises on unknown stage."""
    if stage not in REGISTRY:
        raise KeyError(
            f"unknown prompt stage {stage!r}; known stages: {', '.join(stages())}"
        )
    return sorted(REGISTRY[stage])


def get(stage: str, variant: str = DEFAULT_VARIANT) -> PromptVariant:
    """One variant, or ``KeyError`` naming the valid ids.

    Fails closed on purpose. A silent fall back to ``v1`` is how a prompt
    experiment becomes a lie: the run would report a variant it never sent.
    """
    known = variants(stage)  # raises on unknown stage
    try:
        return REGISTRY[stage][variant]
    except KeyError:
        raise KeyError(
            f"unknown prompt variant {stage}@{variant!r}; "
            f"valid ids for {stage}: {', '.join(known)}"
        ) from None


def resolve(overrides: Mapping[str, str] | None = None) -> dict[str, str]:
    """The full ``stage -> variant`` map for a run: :data:`DEFAULTS` + overrides.

    Every stage is present in the result, so the resolved map is a complete
    description of what a run sent — a partial map would leave the unmentioned
    stages' identity implicit, which is the state this module exists to end.
    Unknown stage or variant raises (see :func:`get`).
    """
    resolved = dict(DEFAULTS)
    for stage, variant in (overrides or {}).items():
        resolved[stage] = get(str(stage), str(variant)).variant
    return resolved


def text(stage: str, variants_map: Mapping[str, str] | None = None) -> str:
    """The prompt text ``stage`` should send under ``variants_map``."""
    return get(stage, resolve(variants_map)[stage]).text


def prompt_set_hash(variants_map: Mapping[str, str] | None = None) -> str:
    """SHA-256 over sorted ``(stage, variant, sha256(text))`` triples.

    The **text** digest is in the payload, not just the variant id: editing ``v1``
    in place has to change this hash, or an edited prompt masquerades as the
    prompt it replaced and two incomparable runs read as one experiment.

    Resolves first, so a partial map, an empty map, and the explicit full default
    map all hash to the same value — the hash describes what was *sent*, not how
    the caller happened to spell it.
    """
    resolved = resolve(variants_map)
    payload = [
        [
            stage,
            variant,
            hashlib.sha256(get(stage, variant).text.encode("utf-8")).hexdigest(),
        ]
        for stage, variant in sorted(resolved.items())
    ]
    canonical = json.dumps(payload, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def parse_cli_overrides(items: Iterable[str] | None) -> dict[str, str]:
    """Parse repeated ``--prompt stage=variant`` into a validated override map.

    Validated *here*, at parse time, so a typo fails before the run opens a
    database or spends a token — and a repeated stage raises rather than
    last-wins, because a run that silently drops one of two contradictory flags
    reports a variant it never sent.
    """
    out: dict[str, str] = {}
    for raw in items or ():
        spec = str(raw)
        stage, sep, variant = spec.partition("=")
        stage, variant = stage.strip(), variant.strip()
        if not sep or not stage or not variant:
            raise ValueError(
                f"--prompt expects stage=variant, got {spec!r}; "
                f"known stages: {', '.join(stages())}"
            )
        if stage in out and out[stage] != variant:
            raise ValueError(
                f"--prompt {stage} given twice with different variants "
                f"({out[stage]!r} then {variant!r}); pick one"
            )
        out[stage] = get(stage, variant).variant
    return out
