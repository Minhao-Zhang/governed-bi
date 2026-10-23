"""Adding a row records and checks a field by default.

``missing_required`` treats ``None`` and an unmeasured ``Measured`` as absent for ``Absence.never``. Records are
unredacted: a redaction vocabulary with no enforcer would read as behaviour.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Mapping

from .quantity import Measured
from .stages import CHANNEL_STAGES, Stage

__all__ = [
    "Tier",
    "Absence",
    "RecordField",
    "RECORD_REGISTER",
    "CHECK_CONDITIONS",
    "record_keys",
    "required_keys",
    "check_keys",
    "live_capture_keys",
    "missing_required",
    "undeclared_keys",
    "project",
]


class Tier(str, Enum):
    identity = "identity"
    under_test = "treatment"
    decision = "decision"
    outcome = "outcome"
    cost = "cost"
    #: Every health field must feed a quotability check, enforced at import.
    health = "health"


class Absence(str, Enum):
    """All three encode as JSON null. This column is what distinguishes them.

    ``never`` fails the presence test. ``not_applicable`` means the stage did not run. ``not_measured`` means upstream
    did not report it.
    """

    never = "never"
    not_applicable = "not_applicable"
    not_measured = "not_measured"


@dataclass(frozen=True, slots=True)
class RecordField:
    name: str
    tier: Tier
    absence: Absence
    owner: Stage
    why: str
    check: str | None = None
    #: Otherwise capture it live; it cannot be rebuilt after the run.
    reconstructable: bool = False

def _f(
    name: str,
    tier: Tier,
    absence: Absence,
    owner: Stage,
    why: str,
    *,
    check: str | None = None,
    reconstructable: bool = False,
) -> RecordField:
    return RecordField(name, tier, absence, owner, why, check=check, reconstructable=reconstructable)


#: Adding a row here is the only way to add a recorded field.
RECORD_REGISTER: tuple[RecordField, ...] = (
    _f("run_id", Tier.identity, Absence.never, Stage.finish,
       "joins a turn to its run", reconstructable=True),
    _f("turn_id", Tier.identity, Absence.never, Stage.finish,
       "the upsert key. Derived per invoke, never at build time: a reused graph "
       "deriving it once wrote every question to the same id and the idempotent "
       "upsert overwrote each row with the next"),
    _f("thread_id", Tier.identity, Absence.never, Stage.finish, "multi-turn grouping"),
    _f("question_id", Tier.identity, Absence.never, Stage.finish, "joins to gold"),
    _f("db_id", Tier.identity, Absence.never, Stage.finish,
       "schema-qualified. A pooled corpus repeats table names across schemas, so a "
       "bare name would credit a table from the wrong schema"),
    _f("attempt_id", Tier.identity, Absence.never, Stage.finish,
       "which resume attempt served this row. Without it a ladder blends attempts at "
       "different worker counts into one file, and worker count saturates the shared "
       "quota the crash and degradation checks read"),

    _f("evicted", Tier.under_test, Absence.not_applicable, Stage.assemble,
       "what the char budget dropped before the model saw it: bodies, whole tables, and by "
       "how much the block still overran. Null means the block fit, which the assemble node "
       "distinguishes from an empty mapping. Treatment, not outcome, because it changes what "
       "was served: `table_coverage` is computed over `licensed` and is therefore a retrieval "
       "figure -- a table can be routed, retrieved, counted as covered, and then evicted here. "
       "First measured on the 2026-08-09 v3-fold config: the budget bit on 19 of 1 351 turns "
       "(1.4%), dropping bodies only and never a whole table. So the 80 000-char budget is "
       "NOT the binding constraint an offline reconstruction had suggested -- that estimate "
       "built the context from every retrieved table's every column and ignored the per-type "
       "budgets pass two applies. `DeliveryTracker.merge_into` destroyed this field mid-turn "
       "for its whole life before it was carried, which is why no earlier config can report it"),

    _f("context_hash", Tier.under_test, Absence.not_applicable, Stage.assemble,
       "the delivery check. Deterministic: a pure function of corpus content and "
       "pipeline decisions. Null on paths that skip assemble, and NEVER the string "
       "'unknown' — v1's sentinel compared equal to itself and let two runs with no "
       "recorded treatment pass comparability",
       check="both configs recorded a context_hash on every shared question. Distinctness was "
            "the condition until audit D9: it measured retrieval nondeterminism rather than "
            "treatment change and passed at 0.9993 on a seed-only pair, so the treatment "
            "judgement moved to settings_resolved"),
    _f("delivery_hash", Tier.under_test, Absence.not_applicable, Stage.finish,
       "context plus every tool-delivered body. NOT deterministic -- it depends on "
       "which read_body calls the model chose -- so it is a diagnostic, never a check. "
       "The only field that answers whether curated bodies reached the model"),
    _f("tool_delivered", Tier.under_test, Absence.not_applicable, Stage.agent_core,
       "call_id -> sha256 of every corpus- or database-derived tool return. Not just "
       "read_body: real database values are the largest source of config-to-config variation "
       "in what the model sees. Null when the agent loop did not run"),
    _f("corpus_content_hash", Tier.under_test, Absence.never, Stage.finish,
       "the corpus IS the treatment. Gated from 2026-08-10 (audit D7): the sentence above was "
       "written and nothing enforced it, so two configs measured over two different corpora passed "
       "all six checks and compared as one treatment. The two runs the power analysis designates "
       "as its null replicate carry this field as null on all 1351 rows, which is how a corpus "
       "identity of `unknown` passing a comparability check happened a second time",
       check="every row in one config carries the same non-null corpus_content_hash"),
    _f("prompt_set_hash", Tier.under_test, Absence.never, Stage.finish,
       "hashes the prompt TEXT, not the variant id — editing a variant in place "
       "must change the digest, or an edited prompt is indistinguishable from the "
       "one it replaced"),
    _f("settings_resolved", Tier.under_test, Absence.never, Stage.finish,
       "the resolved value of every comparability setting. Without it a turn cannot be "
       "joined against the configuration it ran under, and a manifest-only record lets "
       "two runs over different corpora on different splits compare as one",
       check="every row in one config agrees on settings.setting_names(); across two configs the "
            "declared treatment differs and every other settings.setting_names() entry is "
            "recorded on both and equal"),

    _f("channel_hits", Tier.decision, Absence.not_applicable, Stage.route,
       "per retrieval channel: asset_id, asset_type, queries, lexical, semantic. Counts alone "
       "cannot attribute a finding to an asset, so no feedback loop is possible. Null "
       "when the fan-out did not run"),
    _f("channel_scoring", Tier.health, Absence.not_applicable, Stage.route,
       "per retrieval channel, per scoring channel: ran / not_configured / failed, as explicit values never "
       "inferred from scores. If the extractor is rate-limited every retrieval channel falls back "
       "to the raw question, the run completes and grades normally, and the config quietly "
       "IS single-pass retrieval. Null when the fan-out did not run -- which the check "
       "must not read as clean",
       check="on turns where the fan-out ran, no channel state differs from its "
            "declared expectation; the observed count is published beside the rate"),
    _f("channel_degraded", Tier.decision, Absence.not_applicable, Stage.route,
       "true when some retrieval channel ran on fewer scoring channels than it declares -- "
       "`registry.channels.is_degraded` over the field above, computed once and written "
       "rather than re-derived by each reader. Unregistered, `project()` cannot write "
       "it and the degradation check published `[pass] channel_scoring 0.0000` on a config "
       "with no index. Null when the fan-out did not run. Tier is `decision`, not "
       "`health`: the quotability condition over this evidence is declared on "
       "`channel_scoring`, and a second check here would be two results on one comparison",
       reconstructable=True),
    _f("schema_ranking", Tier.decision, Absence.not_applicable, Stage.route,
       "ALL scored schemas pre-truncation. Without it, 'the gold schema was not a "
       "candidate' and 'it ranked 4th' are the same observation, which published a "
       "documented failure bucket at a perfect score over 2030 rows"),
    _f("schemas", Tier.decision, Absence.not_applicable, Stage.route,
       "the selected top-N. The only falsifiable check on the routing formula"),
    _f("budget_dropped", Tier.decision, Absence.not_applicable, Stage.route,
       "asset_type -> how many hits a per-type cap discarded before anything downstream could "
       "see them. **Without it a cap and a retrieval miss are the same row**: `pass_two` "
       "deletes over-budget ids from `selected`, so a 9th-ranked gold table does not exist to "
       "the turn and `schema_ranking` shows a schema that was found while nothing says the "
       "table was cut. `register/citations.py` retired the offline estimate of how often that "
       "happens and kept the requirement, because the shape survives the number: a budget set "
       "too tight and a router that never found the table want opposite fixes. Null means no "
       "cap bit -- which `pass_two` distinguishes from an empty mapping -- or that the fan-out "
       "did not run, and `schema_ranking` being null is what tells those two apart. Declared "
       "2026-08-12: `pass_two` had written the key since the caps existed and `resolve` "
       "destroyed it one super-step later, so carrying it to `finish` (the `merge_delta` fix) "
       "moved it one step and no further -- it reached no artifact a check or a reader could "
       "open until it was a register row"),
    _f("budget_best_dropped_score", Tier.decision, Absence.not_applicable, Stage.route,
       "asset_type -> the highest score that did not survive the cap. The count alone cannot "
       "tell a drop at 0.97 from a drop at 0.01 and those want opposite decisions: one says "
       "the cap is too tight, the other says the cap is doing its job. Null on the same two "
       "conditions as `budget_dropped`, and always null or non-null together with it"),
    _f("pulled_in", Tier.decision, Absence.not_applicable, Stage.connect,
       "asset_id -> resolve|connect. Answers what expand_hops is worth"),
    _f("retrieved_tables", Tier.decision, Absence.not_applicable, Stage.connect,
       "what the turn may reach. Deliberately not the post-budget table list: "
       "budgets shape what is rendered, licensing what is reachable"),
    _f("crossings", Tier.decision, Absence.not_applicable, Stage.connect,
       "cross-schema Steiner points, so 'how often does connect cross, and what is "
       "accuracy on those turns' is a query rather than a guess"),
    _f("lexical_coverage", Tier.decision, Absence.not_measured, Stage.route,
       "feeds weak_retrieval. With an embedder every asset scores above zero, so an "
       "out-of-corpus question still returns top_k tables and a clean run writes "
       "confidence"),
    _f("rewrite", Tier.decision, Absence.not_applicable, Stage.rewrite,
       "before / after / outcome. Null means the node did not run (single turn); "
       "'failed' is a distinct outcome value, because a nullable string cannot tell "
       "those apart and any rate built on it reads 0.0 on a run where every rewrite "
       "failed. Free text: it holds the user's question"),
    _f("guard", Tier.decision, Absence.never, Stage.guard,
       "total record, written every turn including clear. A check that leaves a trace "
       "only when it fires cannot afterwards be told from one never wired up. The "
       "rule_id is closed-vocabulary; the detail is free text and dropped"),
    _f("abstention", Tier.decision, Absence.not_applicable, Stage.abstain,
       "what the declared abstention policy decided and the evidence behind it (ADR 0013): "
       "{policy, outcome, reason, rules_evaluated, evidence}, outcome one of answer | withhold "
       "| disabled and `reason` a member of stages.ABSTENTION_REASONS. **The engine's answer to "
       "'why did you not answer'**, which until 2026-08-12 it did not have: 19 of the v4 config's "
       "20 refusals end on `r_table_not_retrieved`, so the histogram said which layer objected "
       "and nothing said whether anything had decided. Null means the turn ended before the "
       "node -- guard, negative check, route or connect -- which is a different fact from "
       "`disabled`, and both are different from a policy that ran and let the turn through. "
       "The result is a pure function of state, so a reader can recompute it from the row.\n"
       "**No check, and no score in it.** There is no confidence, certainty or graded field "
       "here and there will not be: a learned abstainer was measured at OOF AUC 0.597 with an "
       "'unsure' bucket as likely to be right as its 'correct' one (open-work.md 3.11), every "
       "risk-coverage curve reads 0.7144 at the engine's own coverage, and ADR 0007 forbids a "
       "trust field on the answer card. `reason` is a closed vocabulary, held closed by two "
       "bidirectional import-time guards and read by `eval/report.py::refusal_histogram` "
       "through `terminal_reason` -- which is a different histogram from the driver's "
       "`_refusal_layers`, and had to be built: that one counts ledger attempts, and a withheld "
       "turn writes no ledger row, so the abstention reasons were unreachable to it. This field "
       "carries the evidence beside the reason"),
    _f("execution", Tier.decision, Absence.never, Stage.finish,
       "attempts, per-attempt result layer, terminal, guardrail_errors "
       "(ADR 0006 section 12). Total, including the 'no SQL was attempted' case"),
    _f("guardrail_errors", Tier.health, Absence.never, Stage.finish,
       "exceptions swallowed by check(). Zero is a measured zero, including on a turn "
       "where check never ran. Without the counter, a NameError there turns every turn "
       "in a config into a refusal while crash_rate stays 0 and the run reads as quotable",
       check="guardrail_errors == 0"),

    _f("outcome", Tier.outcome, Absence.never, Stage.finish,
       "written at the source and never re-derived: `stages.classify_outcome` under `finish` "
       "is the only derivation, and nothing downstream recomputes it. The second entry point "
       "that preferred the finish over re-derivation was deleted (audit section 10), so a row "
       "whose written `outcome` does not parse is a broken row, not a row to guess about",
       check="no turn classified crashed"),
    _f("terminal_reason", Tier.outcome, Absence.not_applicable, Stage.finish,
       "WHY a turn declined: missing_join_path / no_schema_matched / "
       "over_connect_bounds / no_sql are four different engineering problems and "
       "`outcome: declined` is one value for all of them. Held only in graph state, "
       "'routing found nothing' and 'the join graph is disconnected' are the same row"),
    _f("failed_stage", Tier.outcome, Absence.not_applicable, Stage.finish,
       "null when the turn did not fail"),
    _f("error_type", Tier.outcome, Absence.not_applicable, Stage.finish,
       "exception CLASS only. Tracebacks echo SQL and row values"),
    _f("failure_cause", Tier.outcome, Absence.not_applicable, Stage.finish,
       "why an answered-but-WRONG turn was wrong, as a `eval.attribution.FailureCause` member. "
       "A separate field from `error_type` above, deliberately, and for the same reason "
       "`computed_correct` is separate from `correct` (`eval/projection.py`: 'one merge of the "
       "two and the artifact silently reports an engine that commits to everything'). "
       "`error_type` is declared as the exception CLASS of a turn that RAISED, and a consumer "
       "reading `error_type is not None` as 'this turn crashed' is reading the declaration. The "
       "downstream fork wrote taxonomy strings into it first and measured what that cost -- the "
       "crashed count on one config's baseline went from 0 to 78 -- which is why this is a field "
       "and not a second meaning. Null when the turn is not "
       "answered-and-wrong -- which includes every correct answer, every refusal, and a row "
       "whose gold is missing, since `correct: None` means the instrument had nothing to "
       "compare against and is not a failure to explain. Also null on every served turn: this "
       "is written by `eval/projection.py::project_turn`, which is the only place both the "
       "prediction and the answer key are in one row, so `finish` projects the null the register "
       "declares. Reconstructable by construction -- a pure function of `outcome`, `correct`, "
       "`generated_sql`, `gold_sql` and `touched_decoy`, all carried on the same row",
       reconstructable=True),
    _f("generated_sql", Tier.outcome, Absence.not_applicable, Stage.finish,
       "null when no SQL was produced, which is not the same as empty. **This is the "
       "statement the engine SENT** -- canonicalised, quoted and row-limited, read from the "
       "ledger's `executed_sql` and from nothing else. There is **no fallback to the model's "
       "proposal**: that fallback was audit C4, closed, because two eval callers execute this "
       "field and a proposal here is ungoverned SQL run against the database by the "
       "measurement harness. A turn that executed nothing records null -- every refused turn, "
       "and any capped turn whose attempts were all declined; a capped turn that did execute "
       "carries its last executed statement. The proposal is recoverable from the transcript "
       "through `serve.messages.last_proposed_sql`, which is where `eval/projection.py` reads it "
       "to price an abstention. A consumer that re-runs this field must still check on "
       "`outcome == 'answered'`, or it reports a decline as a broken statement -- which is "
       "how 14 capped turns looked like an engine defect on 2026-08-04 "
       "(git-history:198e76e)"),
    _f("usage", Tier.cost, Absence.never, Stage.finish,
       "one record per model call including retrieval-channel and rewrite calls. An empty list is "
       "a measured zero -- a guard-blocked turn made no model calls"),
    _f("cache_read_tokens", Tier.cost, Absence.not_measured, Stage.finish,
       "null means the provider did not report it, NOT zero"),
    _f("cache_write_tokens", Tier.cost, Absence.not_measured, Stage.finish,
       "billed at 1.25x, so it cannot be folded into the input count"),
    _f("latency_sec", Tier.cost, Absence.not_measured, Stage.finish, "wall clock"),

    _f("n_re_served", Tier.decision, Absence.never, Stage.finish,
       "always 0 on every production path: Session.turn seeds it and nothing increments. "
       "Retained so historical rows and the schema stay comparable. It was meant to catch "
       "LangGraph node RetryPolicy re-serving a crashed draw; that policy is banned, and "
       "provider SDK retries (llm_max_retries) are invisible here. Demoted from Tier.health "
       "so it cannot sit as an always-pass quotability check"),

    _f("assumptions", Tier.outcome, Absence.never, Stage.finish,
       "what the answer depends on, each basis one of layer, convention, guess or "
       "found_in_query (ADR 0018). An empty list is a turn that did not accept an answer "
       "call, not a list that was dropped"),
    _f("answer_tool_called", Tier.outcome, Absence.never, Stage.finish,
       "false when an answered or no-SQL turn ended without an accepted answer call. "
       "Refuse, decline, clarification, cap and crash record true: those endings are not "
       "a missing call"),
    _f("guess_count", Tier.outcome, Absence.never, Stage.finish,
       "how many accepted assumptions have basis guess. Zero when the turn did not accept "
       "an answer call"),
    _f("ambiguous_guess_refusals", Tier.outcome, Absence.never, Stage.finish,
       "how many answer calls this turn were refused because a guess was about a phrase "
       "two retrieved terms bind differently. Zero when answer was not called"),
)

#: Refuse; do not warn.
CHECK_CONDITIONS: Mapping[str, str] = {
    f.name: f.check for f in RECORD_REGISTER if f.check is not None
}


def record_keys() -> frozenset[str]:
    return frozenset(f.name for f in RECORD_REGISTER)


def required_keys() -> frozenset[str]:
    return frozenset(f.name for f in RECORD_REGISTER if f.absence is Absence.never)


def check_keys() -> frozenset[str]:
    return frozenset(CHECK_CONDITIONS)


def live_capture_keys() -> frozenset[str]:
    return frozenset(f.name for f in RECORD_REGISTER if not f.reconstructable)


def missing_required(record: Mapping[str, Any]) -> frozenset[str]:
    out: set[str] = set()
    for f in RECORD_REGISTER:
        if f.absence is not Absence.never:
            continue
        if f.name not in record:
            out.add(f.name)
            continue
        value = record[f.name]
        if value is None:
            out.add(f.name)
        elif isinstance(value, Measured) and not value.is_measured:
            out.add(f.name)
    return frozenset(out)


def undeclared_keys(record: Mapping[str, Any]) -> frozenset[str]:
    return frozenset(record) - record_keys()


def project(
    state: Mapping[str, Any], *, extract: Callable[[Mapping[str, Any], str], Any]
) -> dict[str, Any]:
    """``extract`` is injected so this module does not import serve types."""
    return {f.name: extract(state, f.name) for f in RECORD_REGISTER}


def _assert_register_is_coherent() -> None:
    names = [f.name for f in RECORD_REGISTER]
    dupes = sorted({n for n in names if names.count(n) > 1})
    if dupes:  # pragma: no cover - import-time guard
        raise AssertionError(f"duplicate record fields: {dupes}")

    bad_owner = [f.name for f in RECORD_REGISTER if not isinstance(f.owner, Stage)]
    if bad_owner:  # pragma: no cover - import-time guard
        raise AssertionError(f"fields whose owner is not a Stage: {bad_owner}")

    ungated_health = sorted(
        f.name for f in RECORD_REGISTER if f.tier is Tier.health and f.check is None
    )
    if ungated_health:  # pragma: no cover - import-time guard
        raise AssertionError(
            "health-tier fields with no check reading them: "
            f"{ungated_health}. Every health field must be a quotability input."
        )

    per_channel = sorted(f.name for f in RECORD_REGISTER if f.owner in CHANNEL_STAGES)
    if per_channel:  # pragma: no cover - import-time guard
        raise AssertionError(
            f"fields owned by a retrieval-channel stage: {per_channel}. `owner` is the stage after "
            "which the value is final; retrieval-channel evidence is finalised by `route`."
        )


_assert_register_is_coherent()
