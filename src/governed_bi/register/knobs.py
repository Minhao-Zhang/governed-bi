"""Every knob, declared once, with what its difference means.

The union of ADR 0005 §5 and ADR 0006 §13. Three artifacts derive from it: the
manifest, the comparability keys, and the serve config hash — so a new knob joins
the gate by default, and **two runs with different security configuration cannot
hash identically.**

Four v1 incidents are the reason each piece is shaped this way:

* **A hand-curated hash payload.** ``serve_config_hash`` hashed a written-out
  field list. Three separate omissions were found: five note-governance knobs
  absent while eight dead cache knobs were hashed, so flipping note pinning
  produced an identical digest; two knobs that *drop corpus content from the
  prompt*, so two runs served different context on every question while agreeing
  on every hashed field; and the prompt-set hash itself.
* **Model identity was not a knob.** ``llm_reasoning_effort``,
  ``embedding_model`` and ``embedding_dimensions`` were live config fields
  recorded nowhere. Two ladders differed **only** in reasoning effort, so
  comparability cleared the pair the second run existed to isolate — and effort
  moved the baseline arm **+2.5pp against a 2.3pp detection threshold.**
* **Comparability and resume-drift are different questions.** Two runs at
  different commits are the *normal* comparison, so ``git_sha`` in the
  comparability set would declare nearly every pair incomparable. Inside one
  directory the same difference is corrupting: v1 checked ``git_sha`` only, so a
  resume across an **uncommitted** edit blended two harness versions into one
  arm's score — 1025 rows under one diff digest and 326 under another, averaged,
  with no gate firing.
* **Knobs reachable only from an eval CLI.** Three routing knobs had no
  deployment surface, so the benchmark measured a configuration no deployment
  could run. Every knob here must be settable by the same mechanism a deployment
  uses; ``tests/conformance`` asserts it.

**``UNSET`` is not a default.** Two knobs ship uncalibrated on purpose, and a
number in their place would be a fabricated measurement rather than a starting
point. Reading one raises.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Final, Mapping

from .assets import ASSET_REGISTER

__all__ = [
    "UNSET",
    "Unset",
    "Role",
    "Knob",
    "KNOB_REGISTER",
    "knob_names",
    "defaults",
    "knob_default",
    "comparability_keys",
    "resume_drift_keys",
    "config_hash_keys",
]


class Unset:
    """A knob deliberately shipped uncalibrated. Distinct from every value.

    Reading one raises rather than substituting a plausible number. ``negative_tau``
    cannot be calibrated on a benchmark whose questions are all answerable by
    construction, and ``lexical_saturation_k`` must be fitted against a real BM25
    score distribution — a guess in either slot is a fabricated measurement, and
    the gate that reads it would be worse than absent.
    """

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return "UNSET"

    def __bool__(self) -> bool:
        raise TypeError(
            "UNSET has no truth value. A knob that ships uncalibrated must be "
            "handled explicitly, not defaulted through a boolean test."
        )


UNSET: Final[Unset] = Unset()


class Role(str, Enum):
    """What a difference in this knob means for two runs."""

    #: Differing values make two runs incomparable. In the config hash, in the
    #: manifest, in the comparability set.
    comparability = "comparability"
    #: Recorded, but a difference does not invalidate a comparison — worker counts,
    #: commit sha, working-tree state. **Fatal inside one run directory**, normal
    #: between two, which is why drift and comparability are separate sets.
    operational = "operational"
    #: Scope, not configuration: which arms, which schemas, which questions. Not a
    #: comparability key, because a capped probe run exists precisely to be
    #: compared against the full baseline it subsets — but **fatal on resume**,
    #: because v1's documented resume line omitted the scope flags and silently
    #: picked up four default arms, costing a paid run two curator passes.
    scope = "scope"


@dataclass(frozen=True, slots=True)
class Knob:
    """One declared knob."""

    name: str
    default: Any
    role: Role
    why: str
    #: True when the value is a digest of something larger — a committed function
    #: list, the per-type budget table — so the hash moves when the content does
    #: rather than when someone remembers to bump a version.
    hashed_by_content: bool = False


def _k(name: str, default: Any, role: Role, why: str, *, hashed_by_content: bool = False) -> Knob:
    return Knob(name, default, role, why, hashed_by_content=hashed_by_content)


#: Digest input for the per-type budgets, which are declared in :mod:`.assets`
#: beside the types they belong to. Referenced here so that changing a budget
#: moves the config hash without duplicating the table.
_ASSET_BUDGETS: Final[tuple[tuple[str, Any], ...]] = tuple(
    sorted((t.value, p.budget) for t, p in ASSET_REGISTER.items())
)


KNOB_REGISTER: tuple[Knob, ...] = (
    # ── corpus and validation ───────────────────────────────────────────────
    _k("summary_max_chars", 250, Role.comparability,
       "the index's unit of text. Enforced in the model, not at the tool boundary, "
       "so every writer is covered: tool calls, the seed, hand-edited YAML, the "
       "loader. Over-length is a validation error, never a truncation"),
    _k("summary_min_chars", 1, Role.comparability,
       "a blank document is a live provider hazard: OpenAI returns a vector that "
       "can score above zero and pollute a ranking, Bedrock Titan rejects it and "
       "kills the turn"),
    _k("asset_budgets", _ASSET_BUDGETS, Role.comparability,
       "per-type retrieval budgets, declared in register.assets beside the types",
       hashed_by_content=True),

    # ── retrieval ───────────────────────────────────────────────────────────
    # `max_queries_per_facet` was declared here at 8, with the rationale "extraction is
    # model-controlled, and an unbounded phrase list is an unbounded network fan-out". There is
    # no phrase list: `_rewritten_query` returns one string and `_facet_result` builds
    # `[question]`, so the bounded list is always length <= 1 and the bound has never been
    # reachable. It is deleted rather than wired because wiring it would give a knob to a
    # fan-out that does not exist — and it was `Role.comparability`, so every run published a
    # limit on a feature it did not have. If per-facet multi-query retrieval is built, the knob
    # comes back with the code that needs it.
    _k("candidate_depth", 50, Role.comparability,
       "top-N per query WITHIN the facet's target types. A global cut then "
       "filtered would give the term facet an empty result on most queries"),
    _k("route_top_n", 3, Role.comparability, "schemas selected"),
    _k("facet_weight_schema", 1.0, Role.comparability,
       "the schema facet's vote arguably deserves more — it is a direct statement "
       "of what the database is for — but no data supports a multiplier"),
    _k("facet_weight_other", 1.0, Role.comparability, "every other facet"),
    _k("w_lexical", 0.5, Role.comparability,
       "renormalised by active channels, so a single-channel facet is not "
       "structurally half-weighted"),
    _k("w_semantic", 0.5, Role.comparability, "as above"),
    _k("lexical_saturation_k", 1.2, Role.comparability,
       "the k in raw/(raw+k). Declared at 1.2 because that is the value every run "
       "has always used: it shipped UNSET here while retrieve/lexical.py defaulted "
       "k=1.2 and index.py called BM25(docs) with no k, so the register said nobody "
       "had chosen it and the code chose it anyway. Declaring what runs is not the "
       "same as fitting it -- 1.2 is still UNFITTED, and it is the constant that sets "
       "where the lexical scale sits, so a fit is real outstanding work. It matters "
       "less than it did: nodes/facets.py now scales each channel within its own "
       "facet before comparing them, so k no longer decides which channel wins"),
    _k("expand_hops", 0, Role.comparability,
       "FK-neighbourhood expansion, off until its contribution is measured: of the "
       "tables gold SQL uses, how many entered neither by facet hit nor by Steiner "
       "path?"),
    _k("max_steiner_points", 5, Role.comparability, "exceed => decline"),
    _k("max_crossings", 2, Role.comparability, "cross-schema connects; exceed => decline"),
    _k("negative_tau", UNSET, Role.comparability,
       "absolute threshold on the semantic score. The gate ships DISABLED: this "
       "cannot be calibrated on a benchmark whose questions are all answerable by "
       "construction, and an uncalibrated refusal gate is worse than none"),

    # ── context and delivery ────────────────────────────────────────────────
    _k("context_budget_chars", 80_000, Role.comparability,
       "the total rendered budget, a BACKSTOP and not a cost lever. Counted in "
       "CHARACTERS because a token count needs a tokeniser per provider and must "
       "be right at delivery time in production, where chars are free and exact. "
       "80_000 sits above the largest context v1 ever delivered (76,354 chars, "
       "max over 19,095 turns), so it provably never fires on observed traffic. "
       "That is deliberate: measured per arm, any BINDING threshold truncates only "
       "the treated arms -- at 24,000 it fires on 23.5% of `curated` and 27.4% of "
       "`curated_sme` turns and 0.0% of `baseline` and `seeded` -- which weakens "
       "the treatment in exactly the arms whose treatment is being measured. Per "
       "the R2 rule, truncation MUST be recorded when it fires; a cap that "
       "silently trims context is an undelivered treatment reported as delivered"),
    _k("read_body_max_tokens", 20_000, Role.comparability,
       "below the deep-agent middleware eviction threshold. Past roughly 80 KB a "
       "tool result is evicted to a file and replaced by a preview, so one read "
       "silently becomes several turns out of the step budget"),

    # ── models ──────────────────────────────────────────────────────────────
    _k("chat_model", None, Role.comparability, "the main generation model"),
    _k("facet_model", None, Role.comparability,
       "extraction is classification; a small model. Four concurrent calls, so "
       "latency counts once and cost counts four times"),
    _k("rewrite_model", None, Role.comparability,
       "separate from facet_model even though both are small: two call sites under "
       "one knob means a run with a different rewrite model hashes identically"),
    _k("llm_temperature", None, Role.comparability,
       "None means provider default. v1 recorded None for runs that really did "
       "forward a temperature, because a defaulted parameter passes a presence "
       "check while recording a value the run never used"),
    _k("llm_reasoning_effort", None, Role.comparability,
       "two v1 ladders differed ONLY in this and compared as one experiment; it "
       "moved the baseline arm +2.5pp against a 2.3pp detection threshold"),
    _k("llm_utility_model", None, Role.comparability,
       "the model behind the guard's scope gate and the five facet query rewriters. "
       "Separate from llm_model because it is separately choosable and it decides what "
       "gets retrieved at all: a cheaper rewriter that phrases the schema query worse "
       "moves routing recall, which moves everything downstream. Written even when it "
       "falls back to llm_model, because 'shared one model' and 'split them' are two "
       "treatments and a blank would make them compare as one"),
    _k("llm_max_retries", 3, Role.comparability,
       "how many times the provider SDK retries one call, across EVERY model surface: the "
       "agent, the utility model and the embedder. It was the SDK's own default of 2 and "
       "nothing in this repository set it, while governed_bi.toml carried max_retries = 8 "
       "under a comment calling it 'the entire defence' against 429s -- in a file v2 deleted "
       "the reader for. It is comparability and not just config because retries move "
       "crash_rate, and crash_rate is what the quotability gates read: two runs differing "
       "only here would compare as one, which is precisely what llm_reasoning_effort did"),
    _k("llm_timeout_s", 300.0, Role.comparability,
       "wall clock for one AGENT call. Separate from the retry count because the two answer "
       "different questions -- how long may a legitimate call take, versus how flaky is the "
       "provider -- and separate from the utility timeout because the two tiers now run at "
       "different reasoning efforts. The number that matters is the product: worst case for "
       "one call is timeout x (retries + 1), so the SDK's 600s default at 3 retries is a "
       "40-minute hang, not a defence. More retries is also the WRONG fix for a slow call, "
       "which the replaced toml comment conflated: retries defend against 429/5xx, timeouts "
       "against hangs, and raising one without the other multiplies the ceiling"),
    _k("llm_utility_timeout_s", 30.0, Role.comparability,
       "wall clock for the small calls -- the scope gate, the five facet rewriters, and the "
       "embedder, which shares this because it is the same latency class on the same critical "
       "path. Measured at 1.2-1.5s each, and they all run BEFORE anything appears on screen, "
       "so the SDK's 600s default meant one hung call stalled a turn for ten minutes when "
       "every one of those call sites already degrades gracefully. Failing fast into a "
       "degradation the code already handles beats waiting for a call that is not coming"),
    _k("embedding_model", None, Role.comparability,
       "part of every vector cache key. cosine returns 0.0 on a width mismatch "
       "rather than raising, so a cross-model cache hit degrades routing to "
       "'nothing scores' with no error anywhere"),
    _k("embedding_dimensions", None, Role.comparability, "as above"),
    _k("prompt_set", None, Role.comparability,
       "resolved variant per stage. The hash covers the TEXT, so editing a variant "
       "in place changes the digest"),

    # ── execution governance (ADR 0006) ─────────────────────────────────────
    _k("permitted_functions", UNSET, Role.comparability,
       "the positive function allowlist, hashed by content. UNSET rather than None "
       "because ADR 0006 G1 is 'absence refuses' and this is the knob that defines "
       "what may execute: a None default contributes None to the config hash, and "
       "`if not permitted_functions` reads it as an empty allowlist — which either "
       "blocks everything or, read the other way, is exactly the hole a positive "
       "allowlist exists to close. The value is the committed list in "
       "govern.functions; config resolution copies its digest here",
       hashed_by_content=True),
    _k("sqlglot_version", UNSET, Role.comparability,
       "canonical function names are release-dependent and the allowlist is keyed "
       "on them, so an unresolved version means the allowlist's correctness is "
       "unknown. Resolved from installed metadata at config time; UNSET so it "
       "cannot be silently absent"),
    _k("guard_rules_enabled", UNSET, Role.comparability,
       "per rule_id. UNSET because ADR 0006 OQ3 requires both numbers — red-team "
       "recall and benign firing rate — before a rule ships enabled, so there is no "
       "honest default: 'all on' ships uncalibrated rules and 'all off' ships no "
       "guard while claiming one"),
    _k("hard_block_suspect", True, Role.comparability,
       "True in development and on the benchmark, False in production, where a "
       "suspect column warns instead of refusing"),
    _k("graded_delivery_enabled", True, Role.comparability,
       "eligible only on a cost-layer failure. An open question asks whether the "
       "path earns its complexity at all, and with the rule narrowed to one layer "
       "deleting it is a small change"),
    _k("run_query_attempt_cap", 3, Role.comparability,
       "the cap TERMINATES the turn. v1's returned a 'capped' tool message and the "
       "agent kept going, burning unbounded round-trips against a cap it could "
       "never clear"),
    _k("max_rows", 200_000, Role.comparability,
       "applied in the connector base class as max_rows + 1 so truncation is "
       "detectable. v1 documented a gateway-wide cap and SQLite was the one path "
       "without it"),
    _k("g_length_max_chars", 8_000, Role.comparability, "guard's hard input bound"),
    _k("cost_budget", UNSET, Role.comparability, "the cost layer's shape estimate bound"),

    # ── structured checks (serve-level result sanity, not ADR 0006 governance) ──
    _k("enable_structured_percentage_check", False, Role.comparability,
       "flags a 'percentage' question whose run_query SQL never scales by 100 "
       "(ported from v1's UtkuAI-line finding: Experiment 006 K2-c, a percentage "
       "question answered as a 0-1 ratio). Off by default because it changes what "
       "the model sees, so a run with it on is not comparable to one without"),
    _k("enable_clarification_to_draft", False, Role.operational,
       "an answered (not declined) live clarification is mined into a TermAsset draft "
       "(curator/clarification.py), written proposed and invisible until an admin "
       "approves it. Operational, not comparability: unlike the check above, this "
       "changes the corpus on disk between two turns of the SAME run, never what a "
       "given turn's own answer looks like — the next turn only sees the draft if "
       "someone certified it first, so two runs with this on/off still answer every "
       "question identically until a human acts"),

    # ── measurement ─────────────────────────────────────────────────────────
    _k("cache_cost_reduction_target", 0.30, Role.comparability,
       "the acceptance criterion for message placement and cache breakpoints, "
       "measured over 200 questions with EX reported alongside and NOT asserted "
       "equal — equivalence needs more power than difference detection, and "
       "nothing tighter than about 3pp is demonstrable at this sample size"),

    # ── operational: recorded, never a comparability key ────────────────────
    _k("git_sha", None, Role.operational,
       "two runs at different commits are the NORMAL comparison. Inside one run "
       "directory the same difference is corrupting, which is why this is a "
       "resume-drift key and not a comparability key"),
    _k("git_main_sha", None, Role.operational,
       "on the experiment server the code sits on a branch never equal to main, so "
       "the branch tip alone cannot say which main commit a paid run was based on"),
    _k("working_tree_dirty", None, Role.operational, "see diff_sha256"),
    _k("diff_sha256", None, Role.operational,
       "uncommitted changes. v1 checked git_sha only, so a resume across an "
       "uncommitted edit blended two harness versions into one arm's score with no "
       "gate firing"),
    _k("serve_workers", None, Role.operational,
       "results are worker-count invariant BY TEST, but the two fields the gates "
       "read — crashed outcomes and channel degradation — come from a shared "
       "provider quota that worker count is precisely what saturates"),
    _k("build_workers", None, Role.operational,
       "separate from serve_workers: a build worker holds a connection AND a "
       "long-lived agent conversation, so the sensible ceilings differ"),

    # ── scope: fatal on resume, not a comparability key ─────────────────────
    _k("arms", None, Role.scope, "which arms exist"),
    _k("schemas_under_test", None, Role.scope,
       "serve from an explicit list, never a directory scan: a schema dropped from "
       "one attempt leaves its YAML behind and competes as a router candidate for "
       "every other schema's questions"),
    _k("split", None, Role.scope, "train or test"),
    _k("question_subset", None, Role.scope,
       "a probe set's identity is not its count, and its EX is a biased sample "
       "because the questions were picked as ones an intervention could plausibly "
       "move"),
)


def knob_names() -> frozenset[str]:
    return frozenset(k.name for k in KNOB_REGISTER)


def defaults() -> Mapping[str, Any]:
    """Every knob at its declared default. ``UNSET`` values are included as
    ``UNSET`` — a caller that reads one must handle it, not default it."""
    return {k.name: k.default for k in KNOB_REGISTER}


def knob_default(name: str) -> Any:
    """One knob's declared default, by name. ``KeyError`` for an undeclared knob.

    **Here rather than in each consumer**, because "look up the declared default of
    one knob" was independently written twice within a day — once in ``corpus/``
    and once in ``govern/`` — which is precisely the outcome
    ``tools/check_one_implementation.py`` predicts when layers are parcelled to
    agents who cannot import each other's unwritten modules. The consumer that
    needs a *bound* still owns the comparison; what it must not own is a second
    answer to what the knob says.

    The raise is the useful half. A typo'd name in a consumer would otherwise ship a
    plausible literal that no knob backs, so the config hash would not move when the
    real knob did — and a threshold outside the comparability hash is v1's
    ``serve_config_hash`` defect.

    ``UNSET`` is returned as ``UNSET``, never resolved to a number. A knob that
    ships uncalibrated must be handled at the call site: ``Unset.__bool__`` raises
    so it cannot be defaulted through a truth test.
    """
    for knob in KNOB_REGISTER:
        if knob.name == name:
            return knob.default
    raise KeyError(f"{name!r} is not a declared knob")


def comparability_keys() -> frozenset[str]:
    """Knobs whose difference makes two runs incomparable."""
    return frozenset(k.name for k in KNOB_REGISTER if k.role is Role.comparability)


def resume_drift_keys() -> frozenset[str]:
    """Knobs whose change **within one run directory** is fatal.

    A strict superset of the comparability keys, asserted at import. The extra
    members are the operational and scope knobs: normal to differ between runs,
    corrupting to differ inside one.
    """
    return comparability_keys() | frozenset(
        k.name for k in KNOB_REGISTER if k.role in (Role.operational, Role.scope)
    )


def config_hash_keys() -> frozenset[str]:
    """Knobs that enter the serve config hash.

    The comparability set. Content-hashed knobs contribute their digest rather than
    a version string, so the hash moves when the content moves rather than when
    someone remembers to bump.
    """
    return comparability_keys()


#: Knobs whose placement v1 got wrong, and where each must be.
#:
#: Asserted individually at import rather than as a set relation. ``drift ⊇
#: comparability`` is *definitionally* true here — :func:`resume_drift_keys` is
#: built as the union of all three roles — so asserting it would be asserting a
#: module against its own constant, which is the authoring rule L§7 records after
#: v1 shipped several guards that could not fail. These can fail: change one
#: ``Role`` and the corresponding line goes red.
_PLACEMENT_INVARIANTS: Mapping[str, Role] = {
    # Fatal inside one run directory, normal between two. v1's drift check iterated
    # the *comparability* list, which does not contain git_sha, so a resume across
    # an uncommitted edit was not fatal — 1025 rows under one diff digest and 326
    # under another, averaged into one arm score.
    "git_sha": Role.operational,
    "diff_sha256": Role.operational,
    "working_tree_dirty": Role.operational,
    # Scope is re-read from argv unless it is pinned, and v1's own documented
    # resume line omitted these, silently picking up four default arms on a paid
    # run — two curator passes and three extra serve passes.
    "arms": Role.scope,
    "split": Role.scope,
    "schemas_under_test": Role.scope,
    "question_subset": Role.scope,
    # Two runs differing in either of these are not the same experiment. v1 hashed
    # a curated field list that omitted both, so two ladders differing ONLY in
    # reasoning effort compared as one experiment — and effort moved the baseline
    # arm +2.5pp against a 2.3pp detection threshold.
    "llm_reasoning_effort": Role.comparability,
    "llm_utility_model": Role.comparability,
    "embedding_model": Role.comparability,
}


def _assert_knobs_are_coherent() -> None:
    """Import-time invariants. Two, neither definitional."""
    names = [k.name for k in KNOB_REGISTER]
    dupes = sorted({n for n in names if names.count(n) > 1})
    if dupes:  # pragma: no cover - import-time guard
        raise AssertionError(f"duplicate knobs: {dupes}")

    by_name = {k.name: k for k in KNOB_REGISTER}

    # A typo'd name here would make the invariant below skip silently — an
    # assertion that cannot fire, which is the thing this file is trying not to be.
    unknown = sorted(set(_PLACEMENT_INVARIANTS) - set(by_name))
    if unknown:  # pragma: no cover - import-time guard
        raise AssertionError(
            f"_PLACEMENT_INVARIANTS names knobs that do not exist: {unknown}"
        )

    wrong = [
        f"{name}: role={by_name[name].role.value}, must be {expected.value}"
        for name, expected in _PLACEMENT_INVARIANTS.items()
        if by_name[name].role is not expected
    ]
    if wrong:  # pragma: no cover - import-time guard
        raise AssertionError(
            "knobs whose role placement was a v1 incident are misplaced: " + "; ".join(wrong)
        )


_assert_knobs_are_coherent()
