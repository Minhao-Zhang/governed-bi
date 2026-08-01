"""The eval metric register: one declaration of every field a run records.

Five artifacts can carry a run's meaning, and the first three used to be
undeclared dicts built independently by each driver:

- the **manifest** (``manifest.json``) — the knobs and scope that decide what a
  scored row *means*. Read by name by :data:`governed_bi.eval.index.COMPARABILITY_KEYS`
  and :data:`~governed_bi.eval.index.RESUME_DRIFT_KEYS`.
- the **generation row** (``generations.<arm>.jsonl``) — one record per
  (question, arm).
- the **arm summary** (``summary.json``) — the aggregate, read by
  :func:`governed_bi.eval.index.quotable`.
- **stage events** (``stage_events.jsonl``, pooled driver) — one record per
  (question, arm, stage), declared as :data:`STAGE_EVENT_FIELDS`.
- the **split gap** (``split_gap.json``, only under ``--split both``) — per-arm
  ``train - test`` on :data:`SPLIT_GAP_RATES`.

"One declaration of every field" is meant literally, and it is checked: the
manifest, the summary and the row each have a test asserting nothing reaches the
artifact undeclared. That check is what this file's own claim used to lack — four
manifest fields (``corpus_content_hash_observed``, ``corpus_content_hash_by_arm``,
``db_id``, ``completed_at_utc``) were written by code in this very module and by
the drivers while the register described none of them, because only the summary
side had the emitted-but-undeclared test.

Why a register rather than two builders
---------------------------------------
``comparable()`` skips a knob that is ``None`` on both sides, on the reasoning
that two runs which both predate a knob did not differ in it. That is right, and
it is also why a *missing* key is dangerous: an absent key is indistinguishable
from "both runs agree". The retired single-schema driver's manifest was missing ``split``
and ``corpus_content_hash``, so two of its runs over **different corpora on
different splits** compared as identical — and the comment on
``COMPARABILITY_KEYS`` calls ``corpus_content_hash`` out as the one thing the
check did not cover, because the corpus *is* the treatment. That fix had landed
in the pooled driver first, while the single-schema driver was the one whose
numbers were historically quoted.

:func:`build_manifest` is now the only way a manifest is built, and
:func:`validate_manifest` refuses a manifest that omits a gate key. A knob that
genuinely does not apply is recorded as ``None`` *explicitly*, alongside a flag
saying so, so "not applicable" and "not recorded" stop looking alike.

Presence, though, is all a validator can check — and a *defaulted* parameter passes
a presence check while recording a value the run never used, which is the same
failure one layer in. So every knob and every scope field is a required keyword of
:func:`build_manifest`, and :data:`MANIFEST_SCHEMA_VERSION` records that this is
true of a given manifest: ``comparable()``'s "``None`` on both sides counts as
agreement" rule holds only under that guarantee, and it refuses a pair whose
records predate it rather than extending the rule to manifests that cannot support
it.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Literal

from ..prompts import prompt_set_hash
from ..provenance import (
    corpus_content_hash,
    corpus_release_hash,
    git_head_branch,
    git_main_hash,
    working_tree_state,
)

Mode = Literal["single", "datalake"]


@dataclass(frozen=True)
class Metric:
    """One recorded field.

    ``denominator`` is the population a rate is computed over — the field that has
    caused the most defects in this harness, because a rate whose denominator
    quietly includes crashes or refusals reads backwards. ``None`` for anything
    that is not a rate.
    """

    name: str
    meaning: str
    denominator: str | None = None


# --------------------------------------------------------------------------- #
# Manifest
# --------------------------------------------------------------------------- #

#: The contract version of ``manifest.json``. Bump it when a manifest's *presence*
#: guarantee or a declared field's meaning changes such that an older manifest must
#: not be silently compared against a newer one.
#:
#: ``1`` is the first version in which every declared field is guaranteed present —
#: :func:`build_manifest` is the only builder, it takes every knob and every scope
#: field as a required keyword, and :func:`validate_manifest` refuses a manifest that
#: omits one. That guarantee is what makes
#: :func:`governed_bi.eval.index.comparable`'s "``None`` on both sides counts as
#: agreement" rule sound, and no manifest written before this version has it: there,
#: a key that was never recorded is indistinguishable from two runs agreeing on it.
#: So ``comparable()`` refuses a pair that does not carry this field rather than
#: extending the rule to records that cannot support it.
#:
#: ``2`` (M3 N10) drops the ``skip_agent`` and ``allow_git_sha_drift`` knobs: the
#: global "no model was called" bypass is retired in favour of ``--oracle-only``
#: (Option A — see ``docs/plans/batch-m3.md``), which makes "no model calls" an
#: INFERENCE from an empty fair-arm set rather than a flag that can combine with any
#: configuration. Bumping the version is what :data:`tests/test_manifest_schema_bump.py`
#: exists to enforce: a knob-set change without a version bump would leave
#: ``comparable()`` reading the missing key as "both sides agree" on a v1/v2 pair.
#:
#: ``comparable()`` only refuses a pair when a side's version is ``None`` (predates the
#: guarantee entirely) — it does NOT refuse a v1-vs-v2 pair. That is deliberate: a v1
#: record still guarantees every v1-era field is present, so the ones that survived
#: into v2 unchanged are still safely comparable. Refusing every version mismatch would
#: make the 2026-07-30 v1 ladder incomparable to anything this repo runs after M3,
#: which is the opposite of what M5's analysis work (N15) needs from this archive.
#:
#: ``3`` adds the three MODEL-IDENTITY knobs the register was missing:
#: ``llm_reasoning_effort``, ``embedding_model``, ``embedding_dimensions``. All three
#: are live ``ModelConfig`` fields (``config.py``) that change what a scored row means,
#: and none of them was recorded. This is not hypothetical: the 2026-07-30 and
#: 2026-07-31 ladders differ ONLY in reasoning effort (medium vs high) and their
#: manifests are indistinguishable — same ``model``, same ``llm_temperature``, no
#: effort field anywhere — so ``comparable()`` could not see the one variable the
#: second run existed to isolate. Effort moved the baseline arm +2.5pp against an
#: MDE of 2.3pp, i.e. a treatment ABOVE the detection threshold was invisible to the
#: comparability gate. ``llm_temperature`` one line above got exactly this treatment
#: at AUDIT E5 and the rule was not carried to its neighbours.
#:
#: An integer rather than a date, because the only question anyone asks of it is
#: "is this at least version N" and a date invites string comparison.
#:
#: Knob-set changes are pinned per version in
#: ``tests/test_manifest_schema_bump.py`` (``_SNAPSHOTS``): the current
#: ``MANIFEST_KNOBS`` name set must equal ``_SNAPSHOTS[MANIFEST_SCHEMA_VERSION]``.
#: Changing a knob without bumping (or without registering a new snapshot)
#: fails closed for every version, not only v1→v2.
MANIFEST_SCHEMA_VERSION = 3

#: The version stamp itself. Not a knob and not operational: it says how much the
#: other fields can be trusted.
MANIFEST_SCHEMA: tuple[Metric, ...] = (
    Metric(
        "manifest_schema_version",
        "contract version of this manifest; comparable() refuses a pair without it, "
        "because only from version 1 on is every declared field guaranteed present",
    ),
)

#: Knobs that change what a scored row means. Every one of these must be present
#: in every manifest, in every mode — ``None`` when it does not apply, never absent.
MANIFEST_KNOBS: tuple[Metric, ...] = (
    Metric("split", "which BIRD split was scored"),
    Metric(
        "model",
        "the configured serve model, or None when no fair arm and no model-needing "
        "oracle rung was requested (--oracle-only's inferred no-model path)",
    ),
    Metric("llm_temperature", "decoding temperature; None = provider default"),
    # The three below are the same class of fact as ``model`` and ``llm_temperature``:
    # they identify what answered the question, and two runs that differ on any of
    # them are two experiments. Recorded even when None ("provider default"), for the
    # reason ``llm_temperature`` is: a stated default is comparable, an absent key is
    # not. See MANIFEST_SCHEMA_VERSION 3.
    Metric(
        "llm_reasoning_effort",
        "serve/curator reasoning budget (none|low|medium|high|xhigh|max); the "
        "2026-07-30 vs 2026-07-31 ladders differ only here and moved baseline EX by "
        "2.5pp against a 2.3pp MDE, so it is a treatment, not an operational detail",
    ),
    Metric(
        "embedding_model",
        "the embedding model behind the schema-routing vector channel; swapping it "
        "moves shortlist recall, which is upstream of every scored row",
    ),
    Metric(
        "embedding_dimensions",
        "requested embedding width; None = the model's native size (1536 for "
        "-3-small, 3072 for -3-large), so None means different things per model and "
        "is only interpretable alongside embedding_model",
    ),
    Metric("prompt_variants", "stage -> variant id map, for a human"),
    Metric("prompt_set_hash", "hash of the prompt TEXT, so an in-place edit moves it"),
    Metric("corpus_content_hash", "digest of the served corpora — the treatment itself"),
    Metric(
        "question_pool_hash",
        "digest of the graded questions AND the gold each is graded against, so a "
        "refiltered dataset stops comparing as the same experiment",
    ),
    Metric("git_sha", "the commit that produced the run"),
    Metric("route_top_k", "schema shortlist size; None when routing is bypassed"),
    Metric("route_llm_pick", "LLM picks one schema; None when routing is bypassed"),
    Metric("schema_pick_max_columns", "columns shown to the picker; None when bypassed"),
    Metric("use_embedder", "embedding channel on; None when routing is bypassed"),
    Metric(
        "grade_semantic_failures",
        "graded delivery: a coverage / L3-L5 / execution-exhaustion failure hands the "
        "grader its last generated SQL stamped `unverified` instead of refusing, so the "
        "same turn scores 0 under one setting and can score 1 under the other",
    ),
    # ── note governance (ADR 0003) ──
    # The always-note budget is live on every run: `analyst.agent` forwards both caps
    # into `apply_always_budget` unconditionally, so they decide how much of the
    # corpus's note text reaches the prompt at all. Two runs at 8/2000 and 2/200 serve
    # different context on every question.
    Metric(
        "always_note_global_max",
        "always-notes admitted per turn; the budget applies whether or not PIN is on",
    ),
    Metric("always_note_char_max", "character ceiling on the admitted always-notes"),
    Metric(
        "pin_triggers_enabled",
        "keyword-triggered notes PIN: forced into the prompt ahead of RRF, AND their "
        "schema prepended to the router shortlist — so this moves ROUTING too",
    ),
    Metric(
        "pin_require_certified",
        "only certified notes may PIN; None when pin_triggers_enabled is False, "
        "because nothing could pin and a recorded True would claim a gate that never ran",
    ),
    Metric(
        "pin_max",
        "cap on pinned notes, and so on the schemas PIN adds to the shortlist; "
        "None when pin_triggers_enabled is False",
    ),
)

#: Scope: not knobs, but they decide which arms exist and which questions are in
#: the pool, so a resume that disagrees is a different experiment.
MANIFEST_SCOPE: tuple[Metric, ...] = (
    Metric("mode", "'single' (one pinned schema) or 'datalake' (pooled, unpinned)"),
    Metric("arms", "the arms served"),
    Metric("oracles", "oracle rungs served"),
    Metric("replicate_of", "the arm re-served to measure the noise floor"),
    Metric("db_ids", "schemas in the pool"),
    Metric("limit", "per-schema question cap"),
    Metric("limit_dbs", "schema cap"),
    Metric("question_scope_hash", "digest of the scored question-id set"),
    Metric("routing_bypassed", "True when one schema is pinned, so the router never ran"),
)

#: Recorded, deliberately NOT gate keys: these change how long a run takes, never
#: what a scored row means.
MANIFEST_OPERATIONAL: tuple[Metric, ...] = (
    Metric("bird_dir", "dataset directory"),
    Metric("created_at_utc", "when the run started"),
    Metric("pg_dsn_host", "host actually connected to"),
    Metric("serve_workers", "serve-loop concurrency"),
    Metric("build_workers", "curator-build concurrency"),
    Metric(
        "max_agent_steps",
        "operator override for the curator's per-schema TOOL-CALL budget; null = "
        "derived from schema size, and the resolved figure is each corpus's "
        "run_manifest.json tool_call_budget. Effective recursion limit is 3x + 4",
    ),
    Metric("serve_path", "always agent_core (ADR 0002)"),
    Metric(
        "git_branch",
        "branch name when HEAD is a symbolic ref; null when detached — how the run "
        "was produced, not what was scored (operational, not a knob)",
    ),
    Metric(
        "main_git_sha",
        "SHA of refs/heads/main at run start; null/unknown when the ref is absent",
    ),
    Metric(
        "dirty",
        "True when the working tree had uncommitted changes at run start",
    ),
    Metric(
        "diff_sha256",
        "SHA-256 of git status --porcelain + git diff HEAD when dirty; null when clean",
    ),
)

#: Fields no *builder* can fill, because the value does not exist yet when the
#: manifest is written. Declared here and NOT in :data:`MANIFEST_FIELDS`: requiring
#: them would make :func:`validate_manifest` reject the early write that exists so a
#: crashed run still leaves a manifest behind.
#:
#: All four used to reach ``manifest.json`` undeclared, which is how the register's
#: own opening claim was false: the summary had an emitted-but-undeclared test and
#: the manifest did not, so a field written by :func:`stamp_corpus_hashes` — twelve
#: lines below the register that failed to mention it — hid in plain sight.
MANIFEST_STAMPED: tuple[Metric, ...] = (
    Metric(
        "corpus_content_hash_observed",
        "digest of the corpora actually built, filled by stamp_corpus_hashes; differs "
        "from `corpus_content_hash` exactly when a resume served a moved corpus",
    ),
    Metric(
        "corpus_content_hash_by_arm",
        "per-arm digests, so a reader sees WHICH arm's corpus moved, not only that one did",
    ),
    Metric(
        "completed_at_utc",
        "when the run finished; absent on a crashed run, which is the signal that it "
        "did not finish (`created_at_utc` records the start)",
    ),
    Metric(
        "resumes",
        "one appended copy of each later invocation's knobs; the top level keeps the "
        "ORIGINAL run's, so this is the only record of what the earliest rows were "
        "scored under (read by index._resume_drift)",
    ),
)

#: Present in one mode only, so not required of every manifest.
MANIFEST_MODE_SPECIFIC: tuple[Metric, ...] = (
    Metric(
        "db_id",
        "single mode only: the one pinned schema, kept beside `db_ids` for readers and "
        "artifacts that address a single-schema run by its schema",
    ),
)

#: Must be present in every manifest, in every mode. :func:`validate_manifest`
#: enforces it, because a gate key absent from the manifest can never fire.
MANIFEST_FIELDS: tuple[Metric, ...] = (
    MANIFEST_SCHEMA + MANIFEST_KNOBS + MANIFEST_SCOPE + MANIFEST_OPERATIONAL
)

#: Every field that may legitimately appear in ``manifest.json``. The superset
#: ``tests/test_eval_metrics.py`` checks the drivers against, in the
#: emitted-but-undeclared direction.
MANIFEST_DECLARED: tuple[Metric, ...] = (
    MANIFEST_FIELDS + MANIFEST_STAMPED + MANIFEST_MODE_SPECIFIC
)


def question_pool_hash(rows: Iterable[tuple[str, str, str]]) -> str:
    """Digest of the graded question pool: which questions, and what gold grades them.

    ``rows`` are ``(db_id, question_key, gold_sql)`` for exactly the rows a run
    scores, where ``question_key`` is the caller's ``question_id or question`` — the
    same identity :func:`governed_bi.eval.run_datalake._question_scope_hash` uses, so
    a dataset with no ids still produces a stable digest instead of a constant one.

    Why a second hash beside ``question_scope_hash``, which covers the same rows: that
    one is SCOPE, checked only *within* one run directory on resume and only when the
    prior manifest happened to record it. This one is a KNOB, so it joins
    :data:`governed_bi.eval.index.COMPARABILITY_KEYS` and decides whether two separate
    runs may be quoted in one sentence. It also binds the **gold**, which the scope
    hash does not: a corrected gold statement under an unchanged ``question_id``
    re-points the grader without moving a single id, and every EX in the run means
    something different afterwards.

    The dataset this repo scores against is filtered upstream (``BIRD-Data-Obfuscation``
    drops rows whose gold SQL contradicts their ``evidence``), so runs either side of a
    refilter are measuring different question pools. Without this key they compare as
    the same experiment — the defect already fixed once for ``split`` and once for
    ``corpus_content_hash``.

    Deliberately NOT a digest of the whole split file. Reading every row would make a
    single-schema run's knob move when an unrelated schema was refiltered, which is
    the "changes for an unrelated reason" half of the requirement. The rows a run
    grades are also already in memory at manifest time, so this costs
    one sha256 per question and one sort, once per run — milliseconds over the full
    pool, against the minutes the run itself takes.

    ``"empty"`` rather than a digest when there are no rows: the upstream filter can
    leave a schema with no questions at all, and a graded pool of nothing must read as
    nothing rather than as a hexadecimal value that looks like a real pool.
    """
    lines = sorted(
        f"{db_id}\t{question_key}\t"
        + hashlib.sha256((gold_sql or "").encode("utf-8")).hexdigest()
        for db_id, question_key, gold_sql in rows
    )
    if not lines:
        return "empty"
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()[:16]


def build_manifest(
    *,
    mode: Mode,
    bird_dir: Path | str,
    split: str,
    model_name: str | None,
    prompt_variants: dict[str, str],
    created_at_utc: str,
    # Routing. Pass None for all four when one schema is pinned: the router did not
    # run, and recording a default would claim it ran with that value.
    route_top_k: int | None,
    route_llm_pick: bool | None,
    schema_pick_max_columns: int | None,
    use_embedder: bool | None,
    # The decoding temperature the model was actually configured with. Required, not
    # defaulted: ``validate_manifest`` checks that a knob is PRESENT, so a default
    # here satisfies every gate while recording the wrong value. That is not
    # hypothetical — this parameter defaulted to ``None`` and the retired
    # single-schema driver never passed it, so every single-schema manifest
    # recorded "provider default" for runs whose temperature was configured and
    # really forwarded to the model
    # (``llm.langchain_client.from_config``). ``None`` still means "never set, so the
    # provider's default applied", and it now means that because a caller said so.
    llm_temperature: float | None,
    # Model identity beyond the name. Required, not defaulted, for the reason
    # ``llm_temperature`` is — and for a demonstrated one: two ladders that differed
    # only in reasoning effort produced byte-indistinguishable manifests, so
    # ``comparable()`` cleared a pair that was not comparable. A default here would
    # satisfy ``validate_manifest`` while recording a value the run did not use.
    llm_reasoning_effort: str | None,
    embedding_model: str | None,
    embedding_dimensions: int | None,
    # The graded question pool's identity, from :func:`question_pool_hash`. Required for
    # the same reason ``llm_temperature`` is: this is a gate key, and a default would
    # record ``None`` — which ``comparable()`` reads as "both runs agree" — for a run
    # whose pool is perfectly well known at this point. The upstream dataset is filtered
    # (rows whose gold SQL contradicts their ``evidence`` are dropped), so the pool moves
    # without any knob in this repo changing, and nothing else in the manifest notices.
    question_pool_hash: str | None,
    # Note governance (ADR 0003), as ``Settings`` had it at serve time. Required, not
    # defaulted, for the reason the whole register is: ``pin_triggers_enabled`` reached
    # eval as a dataclass default that nothing could change, and the manifest carried
    # neither it nor ``serve_config_hash``, so a run WITH trigger pinning and a run
    # without it agreed on every recorded key and compared as the same experiment.
    # Pass the raw ``Settings`` values; the "did it apply" derivation happens here so
    # the driver cannot answer it differently.
    always_note_global_max: int,
    always_note_char_max: int,
    pin_triggers_enabled: bool,
    pin_require_certified: bool,
    pin_max: int,
    # Graded delivery, as ``Settings`` had it at serve time. ``config.py`` ships this
    # ``False`` — serve refuses rather than answering — and the eval driver overrides it
    # to ``True``, which is the single largest gap between what eval measures and what a
    # deployment does: a turn that serve would have refused becomes a row the grader can
    # mark correct. It reached ``summary.json``'s ``serve_policy`` block and stopped
    # there, so it was neither a manifest field, nor a comparability key, nor a resume
    # knob, and two runs that graded differently compared as one experiment.
    #
    # The one knob here with a default, and the exception is narrow: the driver passes
    # the value it actually served with (reading it back off ``Settings`` rather than
    # restating the literal), and ``validate_manifest`` requires the key, so the
    # default cannot silence a driver that starts disagreeing with it. It exists
    # because a required parameter here was a ``TypeError`` in the retired
    # single-schema driver at call time, and a manifest builder that raises is worse
    # than one that records a value a test pins. ``tests/test_eval_metrics.py`` pins
    # the ``Settings`` the driver serves with.
    grade_semantic_failures: bool = True,
    # Scope. Required for the same reason as the knobs: an unstated scope is recorded
    # as the empty/absent value, and ``arms=()`` for a run that served three arms, or
    # ``limit=None`` for a run capped at five questions, is a false record that no
    # presence check can catch.
    arms: tuple[str, ...],
    oracles: tuple[str, ...],
    replicate_of: str | None,
    db_ids: list[str] | None,
    limit: int | None,
    limit_dbs: int | None,
    question_scope_hash: str | None,
    # Operational. These keep defaults: they change how long a run takes, never what a
    # scored row means, so a wrong one misleads nobody about a result.
    pg_dsn_host: str | None = None,
    serve_workers: int = 1,
    build_workers: int = 1,
    max_agent_steps: int | None = None,
) -> dict[str, Any]:
    """The one manifest builder, for both modes.

    Every parameter that maps to a :data:`MANIFEST_KNOBS` or :data:`MANIFEST_SCOPE`
    field is keyword-**required**. :func:`validate_manifest` can only check that a key
    exists, so a defaulted parameter produces a manifest that passes every gate and
    describes a different run than the one that executed. Only
    :data:`MANIFEST_OPERATIONAL` parameters may default.

    ``model_name`` is recorded VERBATIM as ``model`` — the caller decides what "no
    model was called" means for its own arms/oracles and passes ``None`` for it
    directly, rather than this builder inferring it from a global bypass flag. That
    used to be ``skip_agent`` (a manifest knob of its own, retired at
    ``MANIFEST_SCHEMA_VERSION`` 2 / M3 N10): a global flag that could combine with any
    configuration is exactly the two-track hazard ``--oracle-only`` replaces it with —
    "no model calls" is now an inference from an empty fair-arm set, made once by the
    caller, not a second knob this builder has to keep in sync with the real one.

    ``corpus_content_hash`` is declared ``None`` here and filled by
    :func:`stamp_corpus_hashes` after the build: the manifest is written before any
    corpus exists, because the gold pre-flight has to run before a model is paid
    for. Declared-then-filled rather than added later, because a gate key absent
    from the manifest can never fire.
    """
    routing_bypassed = route_top_k is None and route_llm_pick is None
    dirty, diff_sha256 = working_tree_state()

    return {
        # ── contract ──
        "manifest_schema_version": MANIFEST_SCHEMA_VERSION,
        # ── scope ──
        "mode": mode,
        "arms": list(arms),
        "oracles": list(oracles),
        "replicate_of": replicate_of,
        "db_ids": list(db_ids) if db_ids is not None else None,
        "limit": limit,
        "limit_dbs": limit_dbs,
        "question_scope_hash": question_scope_hash,
        "routing_bypassed": routing_bypassed,
        # ── knobs ──
        "split": split,
        "model": model_name,
        "llm_temperature": llm_temperature,
        "llm_reasoning_effort": llm_reasoning_effort,
        "embedding_model": embedding_model,
        "embedding_dimensions": embedding_dimensions,
        "prompt_variants": dict(prompt_variants),
        "prompt_set_hash": prompt_set_hash(prompt_variants),
        "corpus_content_hash": None,
        "question_pool_hash": question_pool_hash,
        "git_sha": corpus_release_hash(),
        "route_top_k": route_top_k,
        "route_llm_pick": route_llm_pick,
        "schema_pick_max_columns": schema_pick_max_columns,
        "use_embedder": use_embedder,
        "always_note_global_max": always_note_global_max,
        "always_note_char_max": always_note_char_max,
        "pin_triggers_enabled": pin_triggers_enabled,
        # Same shape as ``model`` when no fair arm ran: a knob whose value is a claim
        # about a mechanism that did not run gets recorded as None, and the switch
        # above says why. Otherwise two PIN-off runs configured with different caps
        # read as incomparable over a difference neither run had.
        "pin_require_certified": pin_require_certified if pin_triggers_enabled else None,
        "pin_max": pin_max if pin_triggers_enabled else None,
        "grade_semantic_failures": grade_semantic_failures,
        # ── operational ──
        "bird_dir": str(bird_dir),
        "created_at_utc": created_at_utc,
        "pg_dsn_host": pg_dsn_host,
        "serve_workers": serve_workers,
        "build_workers": build_workers,
        "max_agent_steps": max_agent_steps,
        "serve_path": "agent_core",
        "git_branch": git_head_branch(),
        "main_git_sha": git_main_hash(),
        "dirty": dirty,
        "diff_sha256": diff_sha256,
    }


def stamp_corpus_hashes(
    manifest: dict[str, Any], roots_by_arm: dict[str, Path]
) -> str:
    """Fill ``corpus_content_hash`` from the corpora that were actually built.

    Returns the observed digest. Also records the per-arm digests, so a reader can
    see *which* arm's corpus moved rather than only that one did. On a resume the
    declared hash is left alone if it is already set — the caller compares them and
    decides whether the drift is fatal.
    """
    observed = corpus_content_hash([roots_by_arm[a] for a in sorted(roots_by_arm)])
    manifest["corpus_content_hash_observed"] = observed
    manifest["corpus_content_hash_by_arm"] = {
        arm: corpus_content_hash([root]) for arm, root in sorted(roots_by_arm.items())
    }
    if manifest.get("corpus_content_hash") is None:
        manifest["corpus_content_hash"] = observed
    return observed


def write_manifest(out_dir: Path, manifest: dict[str, Any]) -> None:
    """Validate, then write ``manifest.json`` atomically.

    One writer, because the resume guard reads this file back and a torn write
    silently disables that guard — the pooled driver wrote it three times per run
    with a plain ``write_text``, while ``index.append_run`` thirty lines away did the
    same job with a lock and an atomic replace. Validation happens here rather than
    at the call sites so a new write path cannot skip it.
    """
    import json

    from .atomic import atomic_write_text

    validate_manifest(manifest)
    atomic_write_text(
        out_dir / "manifest.json",
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
    )


def validate_manifest(manifest: dict[str, Any]) -> None:
    """Refuse a manifest that omits a declared field, or a ledger gate key.

    A key that is *absent* is indistinguishable from "both runs agree" to
    :func:`governed_bi.eval.index.comparable`, which skips a knob that is ``None``
    on both sides. So absence is the failure mode this guards, not a wrong value.
    ``None`` is allowed and meaningful; a missing key is not.
    """
    from .index import COMPARABILITY_KEYS, RESUME_DRIFT_KEYS

    declared = {m.name for m in MANIFEST_FIELDS}
    missing_declared = sorted(declared - set(manifest))
    if missing_declared:
        msg = (
            "manifest omits declared fields "
            f"{missing_declared}; build it through metrics.build_manifest"
        )
        raise ValueError(msg)

    gate_keys = {k for k, _ in COMPARABILITY_KEYS} | {k for k, _ in RESUME_DRIFT_KEYS}
    missing_gates = sorted(gate_keys - set(manifest))
    if missing_gates:
        msg = (
            f"manifest omits ledger gate keys {missing_gates}: comparable() would "
            "read them as None on both sides and call two different configurations "
            "the same one"
        )
        raise ValueError(msg)


# --------------------------------------------------------------------------- #
# Generation row
# --------------------------------------------------------------------------- #

#: One record per (question, arm). Names only, grouped by concern — the meanings
#: live beside the code that computes them. Declared here so
#: ``tests/test_eval_metrics.py`` can assert the drivers emit exactly this set:
#: the row is consumed by ``.get()`` in eight modules, where a renamed key
#: degrades silently to ``None`` instead of raising.
ROW_IDENTITY: tuple[str, ...] = (
    "arm", "question_id", "request_id", "run_id", "turn_id", "db_id", "split",
    "difficulty",
)
ROW_VERDICT: tuple[str, ...] = (
    "correct", "correct_strict", "error", "error_type", "outcome", "failed_stage",
    "failed_layer", "refused_by", "nrows_match",
)
ROW_PREDICTION: tuple[str, ...] = (
    "generated_sql", "pred_nrows", "pred_ncols", "gold_nrows", "attempts",
    "tables_used", "tables_used_unresolved", "n_tables_used_unresolved",
    "licensed_tables",
)
ROW_GOVERNANCE: tuple[str, ...] = (
    "tier", "safety_clearance", "semantic_assurance", "graded_delivery",
    "coverage_best_effort", "decoy_touch", "by_guardrail_layer", "ledger_len",
    "governance_ledger", "n_tool_calls",
)
ROW_CONTEXT: tuple[str, ...] = (
    "context_chars", "context_hash", "injected_note_ids", "n_notes_injected",
    "n_caveats_injected", "n_few_shots_injected", "n_joins_injected",
    "n_metrics_injected", "n_terms_injected", "retrieved_tables",
    # How many columns the analyst-side per-table budget withheld this turn. 0 both
    # when the budget is off and when it did not bind; non-zero is the only way to
    # tell a naturally small context apart from a TRUNCATED one, which is the whole
    # point of an experiment that varies the budget.
    "n_columns_omitted",
)
ROW_ROUTING: tuple[str, ...] = (
    "routed_schemas", "routed_hit", "routing_bypassed", "routing_escaped",
    "routing_escape_unknown", "schema_pick", "schema_pick_fallback", "pick_hit",
    "shortlisted_schemas", "total_schemas",
    # Which channel produced the ranking, and whether it was the degraded one.
    # ``schema_route_degraded`` exists (AUDIT R8) so a dead embedding endpoint is
    # visible: the embedding channel measures 0.953 shortlist recall@10 against
    # BM25's 0.906 on the curated corpus, so a silent fallback is a real drop with
    # nothing else in the record to explain it. Tri-state — ``None`` means the
    # router did not run (bypassed, or crashed before routing), never "fine".
    "schema_route_channel", "schema_route_degraded",
)
#: Catalog width, per row. Not routing and not context: these are properties of the
#: CATALOG, identical across arms for a given question, which is exactly what makes
#: them usable as covariates in a within-schema control. Pooled EX falls from 70.7%
#: on gold tables under 15 columns to 44.3% at 40+, but the same split controlled for
#: schema gives a sign test of p=0.23 — the observational data cannot settle it, so
#: these fields exist to let an intervention try.
ROW_WIDTH: tuple[str, ...] = (
    "gold_table_max_columns", "n_schema_tables",
)
ROW_LEAKAGE: tuple[str, ...] = (
    "gold_twin_in_train", "gold_frozen", "gold_order_sensitive", "gold_schema_rank",
)
ROW_ORACLE: tuple[str, ...] = (
    "oracle_rung", "oracle_applied", "oracle_gold_tables", "oracle_corpus_tables",
    "oracle_offered_tables", "oracle_padding_degenerate",
)
ROW_COST: tuple[str, ...] = (
    "latency_sec", "cost_est_usd", "usage", "token_usage", "token_sum",
)
ROW_PROVENANCE: tuple[str, ...] = ("prompt_set_hash", "prompt_variants")

ROW_FIELDS: tuple[str, ...] = (
    ROW_IDENTITY + ROW_VERDICT + ROW_PREDICTION + ROW_GOVERNANCE + ROW_CONTEXT
    + ROW_ROUTING + ROW_WIDTH + ROW_LEAKAGE + ROW_ORACLE + ROW_COST + ROW_PROVENANCE
)


# --------------------------------------------------------------------------- #
# Arm summary
# --------------------------------------------------------------------------- #

#: The pre-registered headline rate. Exactly one name, fixed here before the run that
#: quotes it, because two candidate headlines let a result be read off whichever
#: stratum came out higher.
#:
#: ``ex_no_twin`` is it, and the register used to name two: ``ex_lenient`` was labelled
#: "headline execution accuracy" while ``ex_no_twin`` was labelled "the defensible
#: headline". Evidence for picking the twin-free one, recomputed from
#: ``runs/datalake/20260730T034522Z-test-ladder-fixed2/20260730T034543Z/summary.json``
#: (test split, 57 schemas, 1351 questions per arm):
#:
#: - 115 of 1200 scored rows carry a structural gold twin in train, a twin rate of
#:   9.6%, and it is not uniform: ``books`` is 11 of 34.
#: - Twin stamp coverage is complete. ``n_twin_unstamped`` is 0 on all four arms, so
#:   every scored row landed in one stratum or the other and neither side is the
#:   pooled figure under a different name.
#: - ``ex_twin`` runs 0.557 / 0.643 / 0.870 / 0.843 across the ladder against
#:   ``ex_no_twin``'s 0.404 / 0.484 / 0.591 / 0.594. The twin rows score far higher,
#:   which is where a recall-shaped gain would hide.
#: - The two candidates agree on the result: baseline to ``curated`` is +18.7pp
#:   twin-free against +19.3pp on ``ex_lenient``. So committing to the twin-free
#:   number costs no measured effect, which is why it can be committed to now instead
#:   of after the next run.
#:
#: ``ex_lenient`` stays computed and reported, because its denominator is the one every
#: published BIRD number uses and dropping it would make this harness incomparable. It
#: is not a headline. Twins are measured rather than excluded by decision
#: (:mod:`governed_bi.eval.leakage`), so both strata keep existing; only the label is
#: exclusive.
HEADLINE_RATE: str = "ex_no_twin"

#: Every rate, with the population it is computed over. This is the register's
#: main job: the recurring defect class in this harness is a rate whose
#: denominator silently absorbs another outcome's failures, so an arm that
#: refuses more looks like an arm that governs better.
#:
#: Only :data:`HEADLINE_RATE` may call itself a headline. ``ex_lenient`` also claimed
#: the word, which is tracker item X11.
SUMMARY_RATES: tuple[Metric, ...] = (
    Metric(
        "ex_lenient",
        "EX over all scored rows, twins included: the figure comparable to published "
        "BIRD numbers. Reported, not the headline (see HEADLINE_RATE)",
        "all scored rows (n)",
    ),
    Metric("ex_strict", "EX under the strict normaliser", "all scored rows (n)"),
    Metric("ex_gradeable", "EX excluding un-gradeable gold", "gradeable rows"),
    Metric("ex_twin", "EX where the gold statement exists in train", "twin rows"),
    Metric(
        "ex_no_twin",
        "EX on rows with no train twin: the PRE-REGISTERED HEADLINE, the one number "
        "this harness commits to in advance (HEADLINE_RATE)",
        "twin-free rows",
    ),
    Metric("conditional_ex_lenient", "EX among turns that produced SQL", "rows that produced SQL"),
    Metric("cond_ex_given_routing", "EX among correctly-routed turns", "rows the router hit"),
    Metric("refusal_rate", "GENUINE refusals; a crash is not a refusal", "all scored rows (n)"),
    Metric("crash_rate", "our bug, counted apart from refusals", "all scored rows (n)"),
    Metric("decoy_touch_rate", "predictions touching a suspect column", "rows that produced SQL"),
    Metric("safety_clearance_rate", "delivered answers that cleared the guardrails", "delivered rows"),
    Metric("graded_delivery_rate", "delivered answers served as unverified", "delivered rows"),
    Metric("coverage_best_effort_rate", "answers delivered on partial coverage", "delivered rows"),
    Metric(
        "routing_recall",
        "the gold schema survived into `routed_schemas` — the set the turn was "
        "licensed against. NOT the retrieval channel's recall, and NOT independent of "
        "the picker: under `route_llm_pick=True` the serve path sets `routed = "
        "frozenset([picked])`, so `routed_hit` IS `pick_hit` and this rate equals "
        "`schema_pick_accuracy` BY CONSTRUCTION, to the last decimal place, on every "
        "arm of every such run (checked row-by-row on all 1351 rows of the 2026-07-31 "
        "ladder). Read `shortlist_recall` for what retrieval actually surfaced. Kept "
        "under this name and this definition because published artifacts quote it",
        "rows with a recorded routing decision",
    ),
    Metric(
        "shortlist_recall",
        "the gold schema was in the shortlist retrieval produced, before the LLM "
        "picker narrowed it to one (`gold_schema_rank is not None`). The retrieval "
        "channel's own recall, and the term `routing_recall` cannot report while the "
        "picker collapses the routed set to a single schema: 0.952 against a pick "
        "accuracy of 0.873 on the 2026-07-31 curated arm, so two thirds of the routing "
        "loss is the picker discarding a schema retrieval had already found",
        "rows that recorded a shortlist (bypassed and crashed turns excluded, as for "
        "routing_recall)",
    ),
    Metric("routing_escape_rate", "SQL reached outside the routed schemas", "rows where escape was observable"),
    Metric(
        "routing_degraded_rate",
        "the embedding channel failed and the ranking fell back to BM25; None (not "
        "0.0) when no turn recorded a channel, because a run that measured nothing "
        "must not read as a run that degraded nowhere",
        "rows where a routing channel was recorded",
    ),
    Metric("schema_pick_accuracy", "LLM picked the gold schema", "rows that recorded a pick"),
    Metric("schema_pick_accuracy_excl_fallback", "…excluding picker fallbacks", "picks that did not fall back"),
    Metric("share_with_a_note", "turns that received at least one note", "all scored rows (n)"),
)

#: Conditional diagnostics: blocks that report a rate on both sides of something
#: the corpus injected, so a per-arm number can say *which part* of the governance
#: is doing the work. Every input was already recorded per row and aggregated
#: against nothing before 2026-07-28.
#:
#: Each block carries its own ``n_*`` and, where a row can fail to record the
#: input, ``n_unstamped`` — an absent input is counted out, never filed on the
#: negative side. That is the same trap the twin strata document: ``not
#: r.get(...)`` puts an ABSENT key in the FALSE stratum, which silently turns one
#: side of the split into the pooled figure.
#: OBSERVATIONAL, every one of them. Each splits an arm's own rows on something the
#: run produced, so none is a randomised contrast and none may be read as the effect
#: of the thing it splits on. The clause saying so is part of each declaration below,
#: because a block named ``ex_by_note_injected`` reads like a treatment effect to
#: anyone who does not go looking for the caveat.
SUMMARY_CONDITIONALS: tuple[Metric, ...] = (
    Metric(
        "ex_by_semantic_assurance",
        "EX per assurance level — the calibration of the semantic axis. If "
        "`unflagged` does not out-score `heuristic`, the stamp is decoration. "
        "OBSERVATIONAL: the split is on an output of the system itself, so this is "
        "within-arm calibration and post-treatment selection ACROSS arms — comparing "
        "one arm's `unflagged` EX to another's compares differently-selected "
        "populations. `n_unstamped` counts rows that recorded no level; they are "
        "excluded, never filed under a `None` level beside the real ones.",
        "rows that recorded an assurance level",
    ),
    Metric(
        "ex_by_tier",
        "EX per display tier — the same calibration for the compact projection, and "
        "OBSERVATIONAL in the same way: the tier is the system's own output, so the "
        "strata are within-arm calibration, not an across-arm contrast. `n_unstamped` "
        "counts rows that recorded no tier, excluded rather than bucketed as `None`.",
        "rows that recorded a tier",
    ),
    Metric(
        "decoy_touch_by_caveat",
        "decoy-touch rate with vs without an injected suspect caveat — whether the "
        "caveat is what stops the model reaching for the decoy. OBSERVATIONAL: the "
        "split is on whether retrieval matched, so a difference is confounded with "
        "which questions the corpus happens to cover.",
        "delivered rows that recorded a caveat count",
    ),
    Metric(
        "ex_by_note_injected",
        "EX with vs without an injected note (ADR 0003's claim, previously unscored). "
        "OBSERVATIONAL: the split is on whether retrieval matched, so it measures "
        "corpus COVERAGE of the questions, not the value of a note.",
        "rows that recorded a note count",
    ),
    Metric(
        "ex_by_repair",
        "EX after a repair (>1 run_query attempt) vs first-attempt — whether "
        "self-repair recovers correctness or just produces valid-but-wrong SQL. "
        "OBSERVATIONAL: the `with` stratum is by construction the questions that "
        "already failed once, so the two sides are different difficulty populations "
        "and the gap is not the cost of repairing.",
        "rows that recorded an attempt count",
    ),
    Metric(
        "guardrail_cost_ceiling",
        "CEILING on answers a guardrail block may have cost, not the cost: blocked "
        "SQL cannot be graded without executing un-guardrailed SQL. Counts turns "
        "where a layer blocked and the turn still ended wrong, out of `n_observed` "
        "turns that recorded a `by_guardrail_layer` map at all — a run whose serve "
        "path never stamped one has `n_blocked == 0` for want of instrumentation, "
        "which without `n_observed` reads as a run that blocked nothing. Note that "
        "`by_guardrail_layer` creates a key at 0 when a layer is merely evaluated, "
        "so blocked means `any(v > 0)`, never a truthiness test on the dict.",
        "rows where at least one layer blocked",
    ),
)

#: Counts. Each exists so an exclusion from some rate above stays visible: a rate
#: reported without its excluded count reads as full coverage.
SUMMARY_COUNTS: tuple[str, ...] = (
    "n", "n_answered", "n_correct", "n_refused", "n_crashed", "n_missing_gold",
    "n_gradeable", "n_gold_unusable", "n_frozen_gold", "n_order_sensitive_gold",
    "n_twin_gradeable", "n_no_twin_gradeable", "n_twin_unstamped",
    "n_gold_twin_in_train", "n_decoy_touch", "n_wrong_but_nrows_match",
    "n_unmapped_refused_by", "n_with_difficulty", "n_with_governance_stamp",
    "n_tables_used_unresolved", "n_rows_no_db_id", "n_pick_fallback",
    "n_routing_observed", "n_routing_bypassed", "n_routing_crashed",
    # ``shortlist_recall``'s numerator and its own denominator. Separate from
    # ``n_routing_observed`` because they are different populations: a turn can record
    # a shortlist and no routing decision, or the reverse, and quoting the retrieval
    # rate over the router's denominator is the class of error the whole register
    # exists to stop.
    "n_shortlist_hit", "n_shortlist_observed",
    "n_routing_unrecorded", "n_routing_escaped", "n_routing_escape_observed",
    # Routing-channel census. ``*_observed`` is separate from the counts for the
    # reason every ``*_observed`` in this tuple is: a run where nothing recorded a
    # channel must not read as a run where every channel was healthy.
    "n_routing_channel_observed", "n_routing_channel_embedding",
    "n_routing_channel_bm25_fallback", "n_routing_channel_none",
    "n_routing_degraded_observed", "n_routing_degraded",
    "n_routing_escape_unknown", "n_correct_routed", "n_correct_unrouted",
    "n_correct_bypassed", "n_correct_routing_crashed",
    "n_correct_routing_unrecorded", "n_correct_via_routing_escape",
    "n_correct_unaccounted", "n_safety_clearance_observed",
    "n_graded_delivery_observed", "n_coverage_best_effort_observed",
    # ``share_with_a_note`` divides by ``n``, so an arm whose serve path stamped no
    # note counts at all reports 0.0 — "the corpus reached nothing" and "nobody
    # measured" render identically. This is the denominator that tells them apart.
    "n_notes_observed",
    # Grading free passes (audit E2): a correct answer that was correct for the
    # wrong reason. quotable() reads all three.
    "n_correct_with_empty_gold", "n_correct_and_pred_has_no_from",
    "n_correct_and_zero_table_overlap",
    # Schema WIDTH of the pool these rows came from, and of each schema inside
    # ``by_db`` (AUDIT A4). From ``statistics.schema_width_census`` over the corpus the
    # arm served, which reuses ``analysis.corpus_census`` so a per-schema ``n_columns``
    # cannot come to mean something the per-arm one does not. ``None`` — never 0 — when
    # no census was passed, because this summariser also runs over archived
    # generations files with no corpus to hand. ``max_table_columns`` has no
    # ``corpus_census`` equivalent and is the figure the wide-table hypothesis is
    # actually about: 70 narrow tables and one 118-column table are not the same pool.
    "n_tables", "n_columns", "max_table_columns",
)

#: Means, and the nested breakdown blocks.
SUMMARY_MEANS: tuple[str, ...] = (
    "mean_attempts", "mean_context_chars", "mean_ledger_len",
    "mean_notes_injected", "mean_few_shots_injected",
)
SUMMARY_BLOCKS: tuple[str, ...] = (
    "arm", "question_ids", "treatment", "cost", "tool_calls", "errors",
    "by_db", "by_difficulty", "by_outcome", "by_failed_stage", "by_error_type",
    "by_guardrail_layer", "by_tier", "by_semantic_assurance", "by_gold_rank",
)

SUMMARY_FIELDS: tuple[str, ...] = (
    tuple(m.name for m in SUMMARY_RATES)
    + tuple(m.name for m in SUMMARY_CONDITIONALS)
    + SUMMARY_COUNTS
    + SUMMARY_MEANS
    + SUMMARY_BLOCKS
)


# --------------------------------------------------------------------------- #
# Stage events
# --------------------------------------------------------------------------- #

#: One record per (question, arm, stage) in ``stage_events.jsonl``, written by the
#: pooled driver from the serve path's own ``stage_events`` provenance. A separate
#: file rather than a row field because a turn emits many of these and the row is
#: already the widest artifact.
#:
#: Declared because this file is the only per-stage timing record a run leaves, and
#: it was absent from the register entirely — including from the doc's list of what a
#: run writes, so a reader looking for latency attribution had no reason to know it
#: existed.
STAGE_EVENT_FIELDS: tuple[str, ...] = (
    "question_id", "arm", "db_id", "run_id", "turn_id",
    "stage", "status", "ms", "detail",
)


# --------------------------------------------------------------------------- #
# Split gap
# --------------------------------------------------------------------------- #

#: The rates :mod:`governed_bi.eval.split_gap` gaps, ``train - test`` per arm, in
#: ``split_gap.json`` under ``--split both``. Every one is accuracy-like, so "train is
#: higher" means "did not transfer"; gapping ``crash_rate`` or ``refusal_rate`` would
#: invite reading operational noise as overfitting, which is why this is a chosen
#: subset of :data:`SUMMARY_RATES` rather than all of them.
#:
#: Declared here so the seven cannot drift from the summary rates they read: a rate
#: renamed in :data:`SUMMARY_RATES` would otherwise leave ``split_gap`` reading a key
#: nobody writes and reporting ``None`` gaps that look like "not measured on one
#: split". ``tests/test_eval_metrics.py`` asserts this equals
#: ``split_gap.GAPPED_RATES`` and that every entry is a declared rate.
SPLIT_GAP_RATES: tuple[str, ...] = (
    "ex_lenient",
    "ex_strict",
    "ex_gradeable",
    "conditional_ex_lenient",
    "cond_ex_given_routing",
    "routing_recall",
    "schema_pick_accuracy",
)

#: ``split_gap.json``'s own top-level keys. ``error`` replaces the rest when a summary
#: could not be read — the two scored splits are already on disk by then, so the
#: reporting fault is recorded rather than raised.
SPLIT_GAP_FIELDS: tuple[str, ...] = (
    "reading", "arms", "arms_not_in_both", "train_dir", "test_dir", "error",
)
