"""Curator orchestration for the eval ladder (``baseline`` / ``curated`` / ``curated_sme``).

``build_baseline_corpus``: deterministic, DB-derivable corpus only (names,
types, sample values, naming-convention FK candidates) — no curator LLM, no
train-SQL seeding. The eval floor (arm naming: ``docs/glossary.md``).

``build_curated_corpus`` (Phase A / ``curated``): Facts profile → deterministic
train-SQL seed → deep-agent explore (all pairs + ``clarifications.jsonl`` via
``FilesystemBackend``) → validate fix pass → write.

``build_curated_corpus_with_sme`` (Phase B / ``curated_sme``): SME-answered
ledger → deep-agent ingest (same tools, ingest prompt) → validate → write.
Offline/tests may use a deterministic fold only when ``model`` is None;
mechanical ledger seeding requires explicit opt-in.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import traceback
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Sequence

from ..corpus.validate import validate_corpus
from ..obs import tracing_callbacks
from .asset_bag import AssetBag
from .clarifications import (
    ClarificationRecord,
    ClarificationRecordStatus,
    clarifications_path,
    fill_clarifications_with_responder,
    load_clarifications,
    load_clarifications_with_repairs,
    quarantine_agent_answers,
    resolve_clarifications_path,
    seed_gap_clarifications,
    write_clarifications,
)
from .profile import profile_database
from .prompts import _PHASE_A_PROMPT, _PHASE_B_PROMPT
from .seed import SeedBundle, qualified_ref, seed_from_train_sql

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from ..config import Settings
    from ..corpus.schemas import TableAsset
    from ..eval.dataset import EvalItem
    from ..gateway import Gateway
    from ..gateway.connectors.base import Connector
    from .clarifications import Responder

_READ_TOOLS = frozenset({"read_corpus", "run_probe_query"})
_WRITE_TOOLS = frozenset(
    {
        "upsert_join",
        "upsert_metric",
        "upsert_term",
        "upsert_few_shot",
        "annotate_table",
        "annotate_column",
        "annotate_columns",
    }
)


#: (question, gold SQL) pairs rendered into ONE Phase A user turn — the target batch
#: width, not a ceiling on intake. It used to be both: ``_render_train_batch`` sliced
#: ``items[:40]`` and was called once, so on the 57-schema benchmark (49 pairs at the
#: smallest, 86 median, 306 largest — every schema over 40) the curator saw 2094 of
#: the 4900 unique ``evidence`` hints in the train split, a median 47.1% per schema.
#: The remaining 57.3% reached the SME brief, which caps nothing
#: (:mod:`governed_bi.curator.sme`, "dropping any starves the SME"), and never
#: reached the arm that produces the +11.5pp step.
PAIRS_PER_BATCH = 40

#: How many Phase A invocations one schema may cost. **This is the cost knob.** Each
#: batch is a separate :func:`_invoke_agent` with its own budget, so raising it buys
#: whole extra agent runs, not extra tool calls: at the default the 57-schema
#: benchmark takes 147 Phase A invocations (24 schemas at 2 batches, 33 at 3) against
#: 57 before. Bounded on purpose, and bounded on the *count* — a schema with more
#: pairs than ``max_batches * PAIRS_PER_BATCH`` gets wider batches rather than more
#: of them, so every pair is still rendered somewhere. On this benchmark that means
#: the widest schema's 306 pairs arrive as 3 turns of 102 instead of 8 of 40, and no
#: schema drops a pair at any setting.
MAX_PAIR_BATCHES = 3

#: Per-pair ceiling on rendered gold SQL. The 40-pair slice was never a size bound
#: and the split contains 48 pairs whose ``sql_rename`` exceeds this — BIRD-
#: Obfuscation rewrites some gold as a literal ``VALUES`` list, and the largest single
#: pair renders 2.53 MB (~630k tokens, more than any context window here). Uncapped,
#: ``language_corpus``'s first 40 pairs alone rendered 323k chars, re-sent on every
#: turn of the agent loop. Clipping at 2000 chars costs the curator nothing on those
#: pairs (a materialised ``VALUES`` list names no table or column) and brings the
#: widest single batch to ~44k chars, below the old worst case by 7x. Every clip is
#: announced in the rendered text.
MAX_RENDERED_SQL_CHARS = 2000


def plan_pair_batches(
    n_items: int,
    *,
    max_batches: int = MAX_PAIR_BATCHES,
    per_batch: int = PAIRS_PER_BATCH,
) -> list[tuple[int, int]]:
    """Contiguous ``[start, stop)`` slices covering **all** ``n_items`` train pairs.

    ``per_batch`` is the target width and ``max_batches`` the hard bound on how many
    invocations one schema may cost. When the two conflict the bound wins and the
    batches widen, so the return value always partitions the whole split: nothing is
    dropped at any setting, which is the property the 40-pair truncation lacked.

    Widths are balanced to within one pair rather than ``per_batch``-then-remainder:
    a 49-pair split is 25 + 24, not 40 + 9, so no invocation pays a whole agent's fixed
    overhead to render nine pairs — and one budget figure fits every batch, which keeps
    ``tool_call_budget`` in the manifest a scalar (see :func:`derive_step_budget`).
    """
    if n_items <= 0:
        return []
    max_batches = max(int(max_batches), 1)
    per_batch = max(int(per_batch), 1)
    n_batches = min(max_batches, -(-n_items // per_batch))
    base, wide = divmod(n_items, n_batches)
    out: list[tuple[int, int]] = []
    start = 0
    for i in range(n_batches):
        stop = start + base + (1 if i < wide else 0)
        out.append((start, stop))
        start = stop
    return out


def _render_train_batch(
    items: Sequence["EvalItem"],
    *,
    start: int = 0,
    total: int | None = None,
    batch: int = 1,
    n_batches: int = 1,
    max_sql_chars: int = MAX_RENDERED_SQL_CHARS,
) -> str:
    """Render one batch of pairs. Renders **every** item it is given.

    Truncation used to live here (``items[:40]`` plus an "N more pairs omitted" line);
    slicing is :func:`plan_pair_batches`'s job now. ``start`` keeps the displayed
    numbering global across batches so a ``raised_by`` reference means the same pair
    in every batch.
    """
    total = len(items) if total is None else total
    header = "## Train (question, gold SQL, evidence) pairs — curate from these"
    if n_batches > 1:
        first = start + 1 if items else start
        # Only the true half of each statement. A first batch told that earlier writes
        # exist goes looking for them, and a last batch told more pairs are coming
        # leaves work for a turn that never runs.
        note = ""
        if batch > 1:
            note += " Your writes from the earlier batch(es) are already in the corpus."
        if batch < n_batches:
            note += " The remaining pairs arrive in later batches."
        header += (
            f"\n(batch {batch} of {n_batches}: pairs {first}-{start + len(items)} of "
            f"{total}.{note})"
        )
    lines = [header]
    for offset, item in enumerate(items):
        i = start + offset + 1
        evidence = (item.evidence or "").strip()
        qid = item.question_id or f"t{i}"
        sql = item.sql or ""
        lines.append(f"{i}. id={qid} Q: {item.question}")
        if evidence:
            lines.append(f"   evidence: {evidence}")
        if max_sql_chars > 0 and len(sql) > max_sql_chars:
            lines.append(f"   sql: {sql[:max_sql_chars]}")
            lines.append(
                f"   ... (gold SQL clipped at {max_sql_chars} of {len(sql)} chars)"
            )
        else:
            lines.append(f"   sql: {sql}")
    return "\n".join(lines)


def _apply_seed(bag: AssetBag, seed: SeedBundle) -> dict[str, int]:
    """Materialise seed candidates.

    Returns ``{joins_ok, joins_fail, metrics_ok, joins_written, metrics_written}``.

    The ``*_ok`` counts are **calls that succeeded**; the ``*_written`` counts are
    assets that exist afterwards. They used to be reported as one number, and the
    gap between them is not noise: an upsert whose id already exists overwrites,
    so a call count overstates coverage by however many candidates collided. That
    gap is what made a run look like the agent had deleted joins — the manifest's
    call count was differenced against a YAML asset count, and the residue was read
    as agent churn. Reporting both makes the collapse visible instead of inferable.
    """
    joins_before, metrics_before = len(bag.joins), len(bag.metrics)
    joins_ok = joins_fail = metrics_ok = 0
    for j in seed.joins:
        msg = bag.propose_join(j.left_table, j.right_table, j.on, confidence=0.55)
        if msg.startswith("ok:"):
            joins_ok += 1
        else:
            joins_fail += 1
    for m in seed.metrics[:20]:
        msg = bag.propose_metric(m.name, m.base_table, m.expression, confidence=0.5)
        if msg.startswith("ok:"):
            metrics_ok += 1
    return {
        "joins_ok": joins_ok,
        "joins_fail": joins_fail,
        "metrics_ok": metrics_ok,
        "joins_written": len(bag.joins) - joins_before,
        "metrics_written": len(bag.metrics) - metrics_before,
    }


def _norm_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _fk_candidates_from_names(
    tables: Sequence["TableAsset"],
    *,
    dialect: str = "postgres",
) -> list[tuple[str, str, str]]:
    """Naming-convention FK guesses over Facts alone: no train SQL, no LLM.

    A column named ``<other>_id`` (or ``<other>Id``) that is not its own
    table's primary key is proposed as a foreign key to another table's
    primary-key column, when a table whose (normalized, singular-or-plural)
    name matches ``<other>`` exists. This is the same cheap prior a human
    skimming the catalog would form from names alone — it is the
    ``baseline`` arm's only source of relationship candidates (D5: baseline
    is deterministic-max, DB-derivable only; the train-SQL-derived
    :func:`seed_from_train_sql` joins belong to ``curated``, not here).

    Returns ``(left_table, right_table, on)`` triples of physical names.
    """
    pk_by_table: dict[str, str] = {}
    for t in tables:
        for c in t.columns:
            if c.is_unique:
                pk_by_table.setdefault(t.physical_name, c.physical_name)

    norm_table_names = {_norm_name(name): name for name in pk_by_table}

    candidates: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for t in tables:
        own_pk = pk_by_table.get(t.physical_name)
        for c in t.columns:
            if c.physical_name == own_pk:
                continue  # not a candidate for its own primary key
            m = re.match(r"^(.+?)[_]?id$", c.physical_name, re.IGNORECASE)
            if not m:
                continue
            stem = _norm_name(m.group(1))
            if not stem:
                continue
            target = (
                norm_table_names.get(stem)
                or norm_table_names.get(stem + "s")
                or (norm_table_names.get(stem[:-1]) if stem.endswith("s") else None)
            )
            if not target or target == t.physical_name:
                continue
            target_pk = pk_by_table.get(target)
            if not target_pk:
                continue
            # Through the same helper the train-SQL seeder uses, not an f-string. Raw
            # interpolation is how `Air Carriers` produced the unparseable
            # `Air Carriers.carrier_id = Carriers.CarrierID`, and this producer feeds
            # the BASELINE arm — the one rung `build_baseline_corpus` deliberately
            # never runs `validate_corpus` over, so nothing downstream would have
            # reported the malformed edge.
            on = (
                f"{qualified_ref(t.physical_name, c.physical_name, dialect=dialect)} = "
                f"{qualified_ref(target, target_pk, dialect=dialect)}"
            )
            key = (t.physical_name, target, on)
            if key in seen:
                continue
            seen.add(key)
            candidates.append(key)
    return candidates


def _apply_fk_candidates(
    bag: AssetBag, tables: Sequence["TableAsset"], *, dialect: str = "postgres"
) -> dict[str, int]:
    """Materialise naming-convention FK candidates. Low, honest confidence: an
    unverified prior, not a measured or SME-confirmed relationship."""
    ok = fail = 0
    for left, right, on in _fk_candidates_from_names(tables, dialect=dialect):
        msg = bag.propose_join(left, right, on, confidence=0.3)
        if msg.startswith("ok:"):
            ok += 1
        else:
            fail += 1
    return {"fk_candidates_ok": ok, "fk_candidates_fail": fail}


def build_baseline_corpus(
    connector: "Connector",
    schema: str,
    out_root: Path | str,
    *,
    sample_limit: int = 5,
) -> Path:
    """The ``baseline`` arm (plan D5): deterministic-max, DB-derivable only.

    Everything a script can pull from the database with **no curator LLM**:
    names, types, sample values (:func:`profile_database`'s default
    ``sample_limit``) and naming-convention FK candidates
    (:func:`_fk_candidates_from_names`). Deliberately does **not** call
    :func:`seed_from_train_sql` and proposes no few-shots — anything learned
    from the train ``(question, SQL)`` pairs belongs to ``curated``, not
    ``baseline``. Served through the same :func:`~governed_bi.eval.arms.agent_solver`
    path as every other rung.
    """
    out_root = Path(out_root)
    out_root.mkdir(parents=True, exist_ok=True)

    tables = profile_database(connector, schema=schema, sample_limit=sample_limit)
    bag = AssetBag.from_tables(schema, tables)
    fk_stats = _apply_fk_candidates(bag, tables)
    bag.write(out_root)

    _write_run_manifest(
        out_root,
        {
            "phase": "baseline",
            "schema": schema,
            "sample_limit": sample_limit,
            "fk_candidates": fk_stats,
        },
    )
    return out_root


def _empty_tool_counts() -> dict[str, Any]:
    return {
        "read": {name: 0 for name in sorted(_READ_TOOLS)},
        "write": {name: 0 for name in sorted(_WRITE_TOOLS)},
        "other": 0,
        "read_total": 0,
        "write_total": 0,
    }


def _unmeasured_tool_counts() -> dict[str, Any]:
    """Counts for an invocation that CRASHED — every total ``None``, not ``0``.

    ``_count_tool_calls`` reconstructs the tally from the returned message list, and
    :func:`_invoke_agent` nulls that list on any exception, so a crash produced a
    complete-looking dict of zeros. The agent's writes are unaffected — the write tools
    mutate the shared :class:`AssetBag` as they are called and ``bag.write`` runs after
    the ``except`` — so ``write_total: 0`` described a half-authored corpus as an
    untouched one. On the 2026-07-29 run that read as "the agent wrote nothing" for 13
    of 55 schemas, and the SME phase republished the same zero as
    ``clarifications_applied``, a reported metric.

    Zero is a measurement. This is the absence of one.
    """
    return {
        "read": {name: None for name in sorted(_READ_TOOLS)},
        "write": {name: None for name in sorted(_WRITE_TOOLS)},
        "other": None,
        "read_total": None,
        "write_total": None,
        # Named in the artifact so a reader does not have to infer why the totals are
        # null, and so `None` cannot be mistaken for an old manifest that lacked them.
        "unmeasured_reason": "agent invocation raised; counts cannot be reconstructed",
    }


def _merge_tool_counts(parts: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Sum per-batch Phase A tool counts into one tally.

    A single part is returned unchanged, so the one-batch shape (and everything that
    reads ``run_manifest.json``'s ``tool_calls``) is untouched.

    ``None`` wins over a number rather than summing as zero. That is the whole point
    of :func:`_unmeasured_tool_counts`: a batch that died before its first super-step
    is unmeasured, and adding it in as 0 would report a partial Phase A as a complete
    one — the 2026-07-29 failure, one level up. ``repeats`` is deliberately absent from
    the merge: ``distinct`` is a property of one trace and summing it across
    independent traces would overcount, so per-batch repeat summaries stay in
    ``pair_batches`` where each belongs to exactly one invocation.
    """
    if not parts:
        return _empty_tool_counts()
    if len(parts) == 1:
        return dict(parts[0])

    def _add(values: list[Any]) -> Any:
        return None if any(v is None for v in values) else sum(values)

    merged: dict[str, Any] = {
        "read": {
            name: _add([p.get("read", {}).get(name) for p in parts])
            for name in sorted(_READ_TOOLS)
        },
        "write": {
            name: _add([p.get("write", {}).get(name) for p in parts])
            for name in sorted(_WRITE_TOOLS)
        },
    }
    for key in ("other", "read_total", "write_total", "n_super_steps"):
        merged[key] = _add([p.get(key) for p in parts])
    merged["n_batches"] = len(parts)
    merged["exhausted"] = any(bool(p.get("exhausted")) for p in parts)
    # The per-invocation limit, which every batch shares (equal batch widths => one
    # budget). Kept a scalar so `recursion_limit_for(tool_call_budget)` still checks out
    # against it; the summed ceiling is `tool_call_budget_total` in the manifest.
    limits = {p.get("recursion_limit") for p in parts if p.get("recursion_limit")}
    merged["recursion_limit"] = limits.pop() if len(limits) == 1 else sorted(limits)
    reasons = [p["unmeasured_reason"] for p in parts if p.get("unmeasured_reason")]
    if reasons:
        merged["unmeasured_reason"] = (
            f"{len(reasons)} of {len(parts)} pair batch(es) unmeasured: {reasons[0]}"
        )
    return merged


def _count_tool_calls(result: Any) -> dict[str, Any]:
    """Tally domain tool calls, split into read vs write."""
    counts = _empty_tool_counts()
    messages = []
    if isinstance(result, dict):
        messages = result.get("messages") or []
    for msg in messages:
        tool_calls = getattr(msg, "tool_calls", None) or []
        if not tool_calls and isinstance(msg, dict):
            tool_calls = msg.get("tool_calls") or []
        for tc in tool_calls:
            name = tc.get("name") if isinstance(tc, dict) else getattr(tc, "name", None)
            if name in _READ_TOOLS:
                counts["read"][name] = counts["read"].get(name, 0) + 1
                counts["read_total"] += 1
            elif name in _WRITE_TOOLS:
                counts["write"][name] = counts["write"].get(name, 0) + 1
                counts["write_total"] += 1
            elif name:
                counts["other"] += 1
    return counts


#: ``curator_phase_a`` variants whose own TEXT states the triage order, so
#: :func:`_budget_brief` must not state a second one. v1 and v2 leave the ordering to
#: the user turn and are measured with it; v3 was written because that ordering ranks
#: clarifications third and the agent obeyed it (186 questions across 57 schemas,
#: median 3, budget-to-question correlation -0.353). Shipping both lists would put a
#: prompt in contradiction with its own call site, which is the failure the
#: ``sme_rules`` note in the registry documents costing 11 of 381 answers. A later
#: variant that also carries its own order must be named here.
_SELF_TRIAGING_PHASE_A_VARIANTS = frozenset({"v3"})


def _phase_a_variant(system_prompt: str | None) -> str | None:
    """The registered ``curator_phase_a`` variant id whose text this is, or ``None``.

    The drivers resolve a variant to *text* before calling
    (``prompt_text("curator_phase_a", ...)``), so the id never arrives as an argument.
    Recovering it by exact text match cannot drift: it compares against the same
    registry the caller resolved from, and follows any edit to that text
    automatically. ``None`` means an unregistered prompt — a test fixture, or a caller
    passing its own — which is treated as not self-triaging. ``None`` *input* means
    the caller took the default variant.
    """
    from .. import prompts

    if system_prompt is None:
        return prompts.DEFAULT_VARIANT
    for variant_id, variant in prompts.REGISTRY["curator_phase_a"].items():
        if variant.text == system_prompt:
            return variant_id
    return None


def _budget_brief(tool_calls: int, *, n_tables: int, triage: bool = True) -> str:
    """The step budget and a triage order, stated to the agent.

    Two separate failures on the 2026-07-29 run motivate this. First, the budget was
    never disclosed: nothing in the system prompt, the user turn, or the harness
    mentions a limit, and the deepagents base prompt pushes the other way. Second,
    the prompt's ordering put the agent-only work — the reliability sweep,
    clarifications — *last*, so exhaustion took exactly the assets no other
    mechanism produces. Joins and metrics are seeded deterministically before the
    agent starts and survive regardless, which is why re-deriving them is the
    cheapest thing to drop and marking columns is the most expensive.

    ``triage=False`` emits the budget without the numbered order, for a system prompt
    that carries its own (:data:`_SELF_TRIAGING_PHASE_A_VARIANTS`). The order below is
    byte-identical to what v1 and v2 were measured with and must stay that way: it is
    un-versioned text, so editing it silently re-defines the baseline of every run
    stamped ``curator_phase_a=v1`` or ``v2``.
    """
    brief = (
        f"## Budget\n"
        f"You have about {tool_calls} tool calls. Several tool calls in ONE reply cost "
        f"the same as one, so batch aggressively — emit all the probes for a table "
        f"together, and use annotate_columns to do a whole table's columns in a single "
        f"call ({n_tables} tables here).\n"
    )
    if triage:
        brief += (
            "If you cannot do everything, this is the order that matters, most first:\n"
            "1. Mark unreliable columns suspect (annotate_columns). Nothing else in the "
            "system writes reliability; an unmarked column is served to the analyst as "
            "usable.\n"
            "2. Describe what tables and columns mean.\n"
            "3. Raise clarifications for genuine unknowns.\n"
            "4. Few-shots and terms.\n"
            "5. Re-verifying seeded joins and metrics — they are already recorded, so this "
            "is the first thing to skip.\n"
        )
    else:
        brief += "Triage against the order of work in your instructions.\n"
    return brief + (
        "Do not re-issue a call you have already made; read_corpus(todo_only=true) "
        "tells you what is left."
    )


def _args_digest(args: Any) -> str:
    """Stable short digest of one tool call's arguments.

    The point is repeat detection. Two calls with the same (tool, digest) are the
    same request issued twice, which is the churn signature; the raw arguments are
    not needed to see that, and keeping them out of the summary keeps SQL and
    column prose out of the manifest.
    """
    try:
        canonical = json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        canonical = repr(args)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:8]


def _collect_trace(update: dict, trace: list[dict[str, Any]]) -> None:
    """Append one record per tool call found in a streamed ``updates`` chunk."""
    for node_update in update.values():
        if not isinstance(node_update, dict):
            continue
        for msg in node_update.get("messages") or []:
            tool_calls = getattr(msg, "tool_calls", None) or []
            if not tool_calls and isinstance(msg, dict):
                tool_calls = msg.get("tool_calls") or []
            for tc in tool_calls:
                if isinstance(tc, dict):
                    name, args = tc.get("name"), tc.get("args") or {}
                else:
                    name, args = getattr(tc, "name", None), getattr(tc, "args", None) or {}
                if not name:
                    continue
                trace.append(
                    {
                        "i": len(trace),
                        "tool": name,
                        "args_digest": _args_digest(args),
                        "args": args,
                    }
                )


def _write_trace(
    path: Path,
    trace: list[dict[str, Any]],
    *,
    append: bool = False,
    tag: str | None = None,
) -> None:
    """Write the per-tool-call trace as JSONL. Never raises — it is diagnostics.

    Verbatim arguments live here and nowhere else. This sits in the run directory
    beside ``run_manifest.json``, which already carries full tracebacks, so it
    inherits that artifact's trust level rather than the portable log's content
    tiers; only the derived counts are promoted into the manifest and the run
    record.

    ``append`` exists for Phase A's pair batches: they are several invocations
    writing one artifact, and the pooled driver promotes a fixed list of sidecar
    *names* (``run_datalake._SIDECARS``), so a per-batch filename would be written and
    then deleted. Truncating instead would leave only the last batch's trace — the
    same blind spot the trace was added to close. ``tag`` labels which invocation a
    row came from, since ``i`` restarts per invocation.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a" if append else "w", encoding="utf-8") as fh:
            for row in trace:
                if tag is not None:
                    row = {**row, "tag": tag}
                fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    except OSError as err:
        print(f"*** WARNING: could not write curator trace to {path}: {err} ***")


def _repeat_summary(trace: list[dict[str, Any]], *, top: int = 5) -> dict[str, Any]:
    """Identical-call statistics — the compact answer to "did it loop?".

    ``distinct`` against ``total`` is the headline: a run that spent 300 calls on
    40 distinct requests was churning, and no other recorded field would show it.
    """
    keys = [(row["tool"], row["args_digest"]) for row in trace]
    counter = Counter(keys)
    repeated = [(k, n) for k, n in counter.most_common(top) if n > 1]
    return {
        "total": len(trace),
        "distinct": len(counter),
        "max_repeat": max(counter.values()) if counter else 0,
        "top_repeated": [{"tool": t, "args_digest": d, "n": n} for (t, d), n in repeated],
    }


#: Super-steps LangGraph spends per *sequential* tool call in the deepagents graph.
#: The loop is ``model → TodoListMiddleware.after_model → tools``, so it is three,
#: not the two a plain ``create_agent`` model+ToolNode loop would cost. Measured
#: against deepagents 0.6.12 / langgraph 1.2.8: a 100-step limit admits exactly 33
#: sequential tool calls. N tool calls emitted in ONE assistant message still cost a
#: single ``tools`` super-step, so a batching agent gets far more than the budget
#: nominally buys — this constant is the pessimistic (fully serial) rate.
SUPER_STEPS_PER_TOOL_CALL = 3

#: Fixed super-step overhead: the one-off ``before_agent`` node plus a final model
#: turn that answers without calling a tool.
_RECURSION_SLACK = 4


def derive_step_budget(*, n_tables: int, n_columns: int, n_pairs: int) -> int:
    """Tool-call budget for one Phase A curator invoke, scaled to the schema.

    The 2026-07-29 run capped 30 of 57 curator agents at a **constant** 100
    super-steps — 33 sequential tool calls — while the Phase A prompt asks for
    per-pair work AND a column-by-column reliability sweep. On the median
    benchmark schema (8 tables, 74 columns, 40 rendered pairs) that is roughly 126
    calls at the most charitable reading and 238 read literally: oversubscribed
    3.8×-7.2×. Cap rate was flat across schema size, which is the signature of a
    budget too small for the *fixed* costs rather than one exhausted by hard
    schemas.

    A constant cannot be right for a pool spanning 3-73 tables and 25-703 columns,
    so the budget is derived. ``annotate_columns`` makes the sweep cost per *table*
    rather than per column, which is why the column term is small — it is slack for
    probes, not one call per column.

    This is a knob with a cost consequence: every unit is up to one more model
    call. It is deliberately generous, because the failure it replaces silently
    discarded whole schemas from a paid run.
    """
    return 30 + 3 * max(n_tables, 0) + max(n_columns, 0) // 10 + max(n_pairs, 0) // 2


def recursion_limit_for(tool_calls: int) -> int:
    """LangGraph ``recursion_limit`` that admits ``tool_calls`` sequential calls.

    Note ``create_deep_agent`` already defaults this to ``9_999``
    (``deepagents/graph.py:880``); the curator was *lowering* it to 100. Nothing
    framework-imposed was being hit.
    """
    return SUPER_STEPS_PER_TOOL_CALL * max(tool_calls, 1) + _RECURSION_SLACK


def _write_run_manifest(out_root: Path, payload: dict) -> None:
    path = out_root / "run_manifest.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _phase_a_run_manifest(curated_root: Path, schema: str) -> dict[str, Any] | None:
    """Phase-A ``run_manifest.json`` from the live root or relocated ``<schema>/_build``.

    After sidecar relocation the manifest sits under ``_build/``; before relocate
    (and during the same-process SME build) it is still at the arm root.
    """
    candidates = (
        curated_root / "run_manifest.json",
        curated_root / schema / "_build" / "run_manifest.json",
    )
    for path in candidates:
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            return data
    return None


def _write_validate_findings(out_root: Path, findings) -> None:
    path = out_root / "validate_findings.jsonl"
    with path.open("w", encoding="utf-8") as fh:
        for f in findings:
            fh.write(
                json.dumps(
                    {"code": f.code, "asset_id": f.asset_id, "message": f.message},
                    ensure_ascii=False,
                )
                + "\n"
            )


def _settings_or_load(settings: "Settings | None") -> "Settings | None":
    """The caller's Settings, or a freshly loaded one as a last resort.

    Every producer in this module stamps its run record from whatever this
    returns, so a caller that resolved a prompt set (or any other knob) must hand
    it in. Loading here is the fallback for standalone CLI use, not the norm: a
    record stamped from a re-read TOML describes a configuration the agent may
    never have run under.
    """
    if settings is not None:
        return settings
    try:
        from ..config import load_settings

        return load_settings(apply_local=False)
    except Exception:
        return None


def _invoke_agent(
    agent: Any,
    *,
    user: str,
    max_agent_steps: int,
    settings: "Settings | None" = None,
    run_id: str | None = None,
    thread_id: str | None = None,
    trace_path: Path | None = None,
    trace_append: bool = False,
    trace_tag: str | None = None,
) -> tuple[Any | None, dict[str, Any], str | None]:
    """Stream the agent to completion; return (result, tool_counts, error_string).

    **Streams rather than invokes**, which is what makes an exhausted run
    diagnosable. ``agent.invoke`` returns the accumulated state only on success, so
    every crash — including the recursion exhaustion that cost 30 of 57 schemas on
    2026-07-29 — left ``result=None`` and the tool counts unmeasurable. Accumulating
    the last ``values`` chunk keeps the message history that was already built, so
    the counts are real on the failure path and the trajectory can be written out.
    This is the same technique the analyst already uses for the same reason
    (``analyst/agent.py::_stream_agent``, which carries ``partial_state`` onto
    ``GraphRecursionError`` so the governance ledger survives).

    ``trace_path`` writes one JSON line per tool call — ordered, with an argument
    digest — which is the artifact that can answer "what did it loop on". The
    digest is what makes a loop legible: a repeated (tool, digest) pair is a
    re-issued identical call, which counts alone cannot show.
    """
    import time

    from ..analyst.run_log import emit_run_record, new_run_id
    from ..provenance import Producer

    result = None
    error = None
    t0 = time.perf_counter()
    rid = run_id or new_run_id()
    tid = thread_id or rid
    usage_cb = None
    cbs = tracing_callbacks(with_usage=True)
    for cb in cbs:
        if type(cb).__name__ == "UsageMetadataCallbackHandler":
            usage_cb = cb
            break
    limit = recursion_limit_for(max_agent_steps)
    trace: list[dict[str, Any]] = []
    n_super_steps = 0
    try:
        for mode, chunk in agent.stream(
            {"messages": [{"role": "user", "content": user}]},
            config={
                "recursion_limit": limit,
                "callbacks": cbs,
                "configurable": {"thread_id": tid},
            },
            stream_mode=["updates", "values"],
        ):
            if mode == "values":
                # One `values` chunk per super-step, and the last one is what
                # `.invoke()` would have returned.
                n_super_steps += 1
                if isinstance(chunk, dict):
                    result = chunk
                continue
            if isinstance(chunk, dict):
                _collect_trace(chunk, trace)
    except Exception as err:
        # Keep the FULL traceback, not just class + message. The bare
        # "KeyError: 'restaurant'" that lands in run_manifest.json is
        # un-diagnosable on its own (it hides which frame keyed on the schema);
        # the manifest is the only durable artifact once runs/ is swept, so the
        # frame has to be captured here or it is lost. The short form still goes
        # to stdout for a readable progress line.
        short = f"{type(err).__name__}: {err}"
        error = f"{short}\n{traceback.format_exc()}"
        print(
            f"deep-agent stopped early ({short}) after {len(trace)} tool call(s) / "
            f"{n_super_steps} of {limit} super-steps"
        )
    if trace_path is not None:
        _write_trace(trace_path, trace, append=trace_append, tag=trace_tag)
    # A crash no longer forfeits the counts: `result` holds the last streamed state,
    # so the tally is reconstructible from the messages the agent actually produced.
    # `_unmeasured_tool_counts` remains for the case where not even one `values`
    # chunk arrived (a failure before the first super-step committed) — that really
    # is unmeasured, and must not be reported as zero.
    counts = _count_tool_calls(result) if result is not None else _unmeasured_tool_counts()
    counts["n_super_steps"] = n_super_steps
    counts["recursion_limit"] = limit
    counts["exhausted"] = error is not None and "GraphRecursionError" in error
    counts["repeats"] = _repeat_summary(trace)
    settings = _settings_or_load(settings)
    if settings is not None:
        usage_list: list = []
        if usage_cb is not None:
            from ..analyst.run_log import usage_callback_entries

            usage_list = usage_callback_entries(usage_cb, source="curator")
        emit_run_record(
            settings=settings,
            producer=Producer.curator,
            run_id=rid,
            thread_id=tid,
            outcome="error" if error else "ok",
            error=error,
            token_usage=usage_list,
            t0=t0,
            # Both keys are already on `_TIER_A_EXTRA_KEYS`, so they survive with
            # full-content logging off. This is what puts step/tool counts in the
            # durable sqlite log, where previously only tokens and latency were
            # available as proxies for how far the agent got.
            extra={"n_tool_calls": len(trace), "n_steps": n_super_steps},
        )
    return result, counts, error


def _validate_fix_pass(
    make_agent: "Callable[[], Any] | None",
    bag: AssetBag,
    *,
    connector: "Connector",
    out_root: Path,
    max_agent_steps: int,
) -> tuple[list, dict[str, Any], str | None]:
    """Run validate_corpus; deterministically repair what we can; then optionally
    one agent fix pass for whatever survives. Returns findings + counts.

    ``make_agent`` is a factory (not a prebuilt agent): the fix-pass gets a
    *fresh* agent so it never shares mutable state — notably the filesystem
    backend — with the fold invoke that ran before it. The shared corpus lives
    in ``bag``, which is passed explicitly; nothing else should carry across.
    """
    # Reference integrity is machine-fixable — repair coercible references
    # (term bindings, column.references, metric.base_table, join endpoints,
    # rule.scope) in code before spending (and risking a crash on) a stochastic
    # agent pass.
    repaired = bag.repair_references()
    if repaired:
        print(f"fix-pass: repaired {repaired} dangling reference(s) deterministically")
    findings = validate_corpus(bag.all_assets(), connector=connector)
    _write_validate_findings(out_root, findings)
    fix_counts = _empty_tool_counts()
    fix_error = None
    if findings and make_agent is not None:
        summary = "\n".join(f"- {f.code} [{f.asset_id}]: {f.message}" for f in findings[:40])
        user = (
            "validate_corpus reported the following findings. Fix them with the "
            f"write tools (do not edit clarifications.jsonl unless needed):\n{summary}"
        )
        _result, fix_counts, fix_error = _invoke_agent(
            make_agent(), user=user, max_agent_steps=max(max_agent_steps // 2, 8)
        )
        findings = validate_corpus(bag.all_assets(), connector=connector)
        _write_validate_findings(out_root, findings)
    return findings, fix_counts, fix_error


def _run_adversary_signal(
    bag: AssetBag, *, connector: "Connector", out_root: Path
) -> list[dict]:
    """Structural adversary: record findings, soft confidence penalties, then gate.

    Soft heuristic codes (missing provenance, FK-without-ref) only discount
    confidence. Hard findings (dangling refs, bad ids, missing physical tables,
    and every other ``validate_corpus`` code) raise
    :class:`~governed_bi.curator.adversary.StructuralGateError` so the caller
    must not ``bag.write`` — fail closed.
    """
    from .adversary import SOFT_ADVERSARY_CODES, gate_hard_findings, review

    findings = review(bag.all_assets(), connector=connector)
    records = [
        {"code": f.code, "asset_id": f.asset_id, "message": f.message} for f in findings
    ]
    path = out_root / "adversary_findings.jsonl"
    with path.open("w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    by_id: dict[str, list[str]] = {}
    for f in findings:
        if f.asset_id:
            by_id.setdefault(f.asset_id, []).append(f"{f.code}: {f.message}")

    for asset_id, notes in by_id.items():
        for name, table in list(bag.tables.items()):
            if table.id != asset_id:
                continue
            audit = table.audit
            from ..corpus.schemas import Audit, Provenance, ProvenanceSource, ProvenanceStatus

            if audit is None:
                audit = Audit(
                    provenance=Provenance(
                        source=ProvenanceSource.curator,
                        status=ProvenanceStatus.proposed,
                    )
                )
            data = audit.model_dump(mode="python")
            data["adversary_findings"] = notes
            new_audit = Audit.model_validate(data)
            conf = table.confidence
            # Soft notes only: hard findings block write, so a penalty is moot,
            # but we still record them on the audit trail above.
            soft_n = sum(1 for n in notes if n.split(":", 1)[0] in SOFT_ADVERSARY_CODES)
            if conf is not None and soft_n:
                conf = max(0.0, float(conf) - 0.1 * soft_n)
            bag.tables[name] = table.model_copy(
                update={"audit": new_audit, "confidence": conf}
            )
        for store in (bag.joins, bag.metrics, bag.terms, bag.few_shots):
            if asset_id not in store:
                continue
            asset = store[asset_id]
            audit = asset.audit
            from ..corpus.schemas import Audit, Provenance, ProvenanceSource, ProvenanceStatus

            if audit is None:
                audit = Audit(
                    provenance=Provenance(
                        source=ProvenanceSource.curator,
                        status=ProvenanceStatus.proposed,
                    )
                )
            data = audit.model_dump(mode="python")
            data["adversary_findings"] = notes
            new_audit = Audit.model_validate(data)
            conf = getattr(asset, "confidence", None)
            updates: dict = {"audit": new_audit}
            soft_n = sum(1 for n in notes if n.split(":", 1)[0] in SOFT_ADVERSARY_CODES)
            if conf is not None and soft_n:
                updates["confidence"] = max(0.0, float(conf) - 0.1 * soft_n)
            store[asset_id] = asset.model_copy(update=updates)

    gate_hard_findings(findings)
    return records


def _corpora_differ(curated_root: Path, curated_sme_root: Path, schema: str) -> bool:
    """True when curated_sme is not a byte-identical copy of curated (curated_sme acceptance)."""
    import hashlib

    def _fingerprint(root: Path) -> str:
        h = hashlib.sha256()
        base = root / schema
        if not base.is_dir():
            return ""
        for path in sorted(base.rglob("*.yaml")):
            h.update(path.relative_to(base).as_posix().encode())
            h.update(path.read_bytes())
        return h.hexdigest()

    return _fingerprint(curated_root) != _fingerprint(curated_sme_root)


# `_mark_columns_absent_from_gold` used to live here: a mask that stamped every
# column no train gold SQL referenced as suspect. It is gone, and it should not come
# back. "BIRD never queried this column" is not evidence the column is unreliable,
# and where the gold SQL was defective the mask was actively wrong — it banned
# columns the generator needed. Reliability is now authored: the curator agent marks
# suspect columns with `annotate_column(suspect=True, ...)` after sweeping the
# schema, and an SME answer that disowns a column folds into the same mark
# (`AssetBag.mark_unrecognised_columns`). `governance.excluded` stays human-only.


def _write_sme_clarifications_log(
    records: Sequence[ClarificationRecord],
    out_root: Path,
    *,
    schema: str,
    tables: Sequence | None = None,
) -> int:
    """Durable audit log of the SME clarification round-trip (ledger shape)."""
    by_name = {t.physical_name: t for t in (tables or [])}
    path = out_root / "sme_clarifications.jsonl"
    rows = []
    for rec in records:
        table = None
        column = None
        table_id = None
        if rec.scope.startswith("table:"):
            rest = rec.scope[len("table:") :]
            if "." in rest:
                table, column = rest.split(".", 1)
            else:
                table = rest
            if table in by_name:
                table_id = by_name[table].id
        rows.append(
            {
                "schema": schema,
                "table_id": table_id,
                "table": table,
                "column": column,
                "question": rec.question,
                "answer": rec.answer,
                "answered_by": rec.answered_by,
                "asked_by": ",".join(rec.raised_by) if rec.raised_by else None,
                "status": rec.status.value,
                "at": None,
                "id": rec.id,
                "scope": rec.scope,
            }
        )
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return len(rows)


def build_curated_corpus(
    connector: "Connector",
    gateway: "Gateway",
    schema: str,
    train_items: Sequence["EvalItem"],
    out_root: Path | str,
    *,
    model: Any | None = None,
    dialect: str = "postgres",
    max_agent_steps: int | None = None,
    max_pair_batches: int = MAX_PAIR_BATCHES,
    run_agent: bool = True,
    system_prompt: str | None = None,
    settings: "Settings | None" = None,
) -> Path:
    """Phase A: profile → seed → explore agent → validate → write curated corpus.

    Does **not** pre-create ``clarifications.jsonl`` — the agent must
    ``write_file`` it (FilesystemBackend rejects write-to-existing). An empty
    missing ledger after Phase A is visible in the manifest
    (``clarification_count: 0``, ``ledger_source: missing``).

    ``system_prompt`` injects a registered ``curator_phase_a`` variant; ``None``
    keeps ``v1``. A caller that stamps a prompt set on the run **must** pass the
    resolved text — a corpus built under one prompt and recorded under another is
    the attribution failure this whole mechanism exists to prevent.

    Pass ``settings`` alongside it. The run record this build emits is stamped from
    ``settings``, so re-deriving config here would record the corpus under the
    TOML's prompt set while the agent ran on the caller's — the same mismatch, one
    layer down, and invisible because both halves look internally consistent.

    ``max_agent_steps`` is the agent's budget in **tool calls**, granted to *each*
    pair batch. ``None`` derives it from the schema's size and the batch width
    (:func:`derive_step_budget`), which is the default because no constant fits a pool
    spanning 3-73 tables. An explicit value wins and is the way to cap cost. It was
    previously a constant 25 fed through ``max(steps * 4, 100)``, which pinned the real
    limit at 100 super-steps for every value at or below the default — so the knob the
    drivers tell an operator to raise did nothing.

    ``max_pair_batches`` bounds how many agent invocations the train split may cost
    (:func:`plan_pair_batches`). It is the other cost knob and the more expensive one:
    ``max_agent_steps`` buys tool calls, this buys whole agent runs. Every pair is
    rendered at any setting — batches widen rather than multiply — so lowering it
    trades per-turn context size for invocation count, never coverage. Set it to 1 to
    reproduce the single-invocation shape (which is *not* the pre-batching behaviour:
    that one also truncated the split at 40 pairs).
    """
    out_root = Path(out_root)
    out_root.mkdir(parents=True, exist_ok=True)

    tables = profile_database(connector, schema=schema)
    bag = AssetBag.from_tables(schema, tables)
    seed = seed_from_train_sql([it.sql for it in train_items], dialect=dialect)
    seed_stats = _apply_seed(bag, seed)
    if seed_stats["joins_fail"]:
        print(
            f"seed: {seed_stats['joins_ok']} joins applied, "
            f"{seed_stats['joins_fail']} failed lookup (check alias resolution)"
        )
    if seed_stats["joins_ok"] != seed_stats["joins_written"]:
        print(
            f"seed: {seed_stats['joins_ok']} join upserts collapsed onto "
            f"{seed_stats['joins_written']} assets (same table pair AND same ON clause)"
        )
    # Every pair reaches the agent, across as many as `max_pair_batches` invocations.
    # `[(0, 0)]` for an empty split: the agent still has a schema to sweep, and the
    # fix pass still needs a budget.
    pair_batches = plan_pair_batches(len(train_items), max_batches=max_pair_batches) or [(0, 0)]
    # One budget for every batch, derived from the widest. Batch widths differ by at
    # most one pair, so a single figure is accurate for all of them and
    # `tool_call_budget` in the manifest stays the scalar that
    # `recursion_limit_for()` was derived from.
    batch_width = max(stop - start for start, stop in pair_batches)
    step_budget = (
        max_agent_steps
        if max_agent_steps is not None
        else derive_step_budget(
            n_tables=len(tables),
            n_columns=sum(len(t.columns) for t in tables),
            # The pairs in ONE batch, not the whole split: the agent cannot work a
            # pair that is not in the turn it is answering. The rest are budgeted for
            # in the batches that carry them.
            n_pairs=batch_width,
        )
    )
    # No deterministic suspect marking happens here any more (see the note where
    # `_mark_columns_absent_from_gold` used to be, above). Between this point and the
    # agent pass the corpus carries zero suspect columns, so the curated arm's decoy
    # defence is entirely whatever the agent authors.
    tool_counts = _empty_tool_counts()
    fix_counts = _empty_tool_counts()
    agent_error: str | None = None
    fix_error: str | None = None
    make_agent: "Callable[[], Any] | None" = None
    agent_ran = False
    batch_records: list[dict[str, Any]] = []

    if run_agent and model is not None:
        from ..analyst.run_log import new_run_id
        from .deep_agent import build_curator_agent

        _settings = _settings_or_load(settings)
        _run_id = new_run_id()
        _thread_id = f"curator:{schema}:{out_root.name}"
        # No checkpointer. deepagents requires one only for `interrupt_on`, and the
        # curator sets none — it invokes once and never resumes. The sqlite saver it
        # used to create was written, closed, relocated and never read back by
        # anything: the harness's own `--resume` decides from existing YAML, and the
        # fix pass mints a fresh thread rather than continuing this one.
        #
        # It was not free. An open sqlite handle is unmovable on Windows, which
        # aborted every curated build and would have ended a paid run with
        # "every db failed to build"; the fixes for that (release-in-finally,
        # copy fallback, promotion exemption) all exist to serve a file nothing reads.
        # Removing it deletes that whole class of failure rather than guarding it.

        def make_agent() -> Any:  # fresh agent per invoke — no shared fs/state
            return build_curator_agent(
                model,
                connector=connector,
                schema=schema,
                gateway=gateway,
                bag=bag,
                run_dir=out_root,
                system_prompt=system_prompt or _PHASE_A_PROMPT,
            )

        agent_ran = True
        n_batches = len(pair_batches)
        # v3 carries its own triage order; v1/v2 get theirs from `_budget_brief`. Two
        # copies would contradict each other — see `_SELF_TRIAGING_PHASE_A_VARIANTS`.
        variant_states_own_triage = (
            _phase_a_variant(system_prompt) in _SELF_TRIAGING_PHASE_A_VARIANTS
        )
        counts_per_batch: list[dict[str, Any]] = []
        errors: list[str] = []
        for i, (lo, hi) in enumerate(pair_batches, 1):
            last = i == n_batches
            # Read at loop time, not before it: the previous batch's agent may have
            # created the ledger, and `write_file` FAILS on an existing path (deepagents
            # `FilesystemBackend.write`). An agent told to "create" a file that is
            # already there spends a turn on a guaranteed error.
            ledger_exists = clarifications_path(out_root).exists()
            user = "\n\n".join(
                [
                    f"Curate schema `{schema}`. Work pair-by-pair; persist via tools.",
                    seed.render(),
                    _render_train_batch(
                        train_items[lo:hi],
                        start=lo,
                        total=len(train_items),
                        batch=i,
                        n_batches=n_batches,
                    ),
                    (
                        "/clarifications.jsonl already exists — read_file it, then "
                        "edit_file to append or broaden (write_file FAILS on a path that "
                        "exists). grep before adding."
                        if ledger_exists
                        else "Create /clarifications.jsonl for genuine unknowns "
                        "(write_file on first create; grep before add; edit_file to "
                        "broaden/merge)."
                    ),
                    "Mark unreliable or misleading columns suspect. Propose at least the verified seed joins.",
                    (
                        "Stop once pairs are covered, seed joins verified, and obviously "
                        "unreliable columns marked."
                        if last
                        else "Stop once THIS batch's pairs are covered and the columns they "
                        "reach are described or marked. Do not redo work "
                        "read_corpus(todo_only=true) no longer lists — the next batch "
                        "continues from the corpus you leave behind."
                    ),
                    # Stating the budget is the point: nothing else in the context does,
                    # and the deepagents harness prompt says "Keep working until the task
                    # is fully complete. Don't stop partway." An agent that cannot see a
                    # limit cannot triage against it, and the stages that died on the
                    # 2026-07-29 run were the late ones.
                    _budget_brief(
                        step_budget,
                        n_tables=len(tables),
                        triage=not variant_states_own_triage,
                    ),
                ]
            )
            _result, batch_counts, batch_error = _invoke_agent(
                make_agent(),
                user=user,
                max_agent_steps=step_budget,
                settings=_settings,
                # One run_id per batch, or the durable run log records N invocations
                # under one id and their token totals collapse into the last writer's.
                run_id=_run_id if n_batches == 1 else f"{_run_id}-b{i}",
                thread_id=_thread_id if n_batches == 1 else f"{_thread_id}:b{i}",
                trace_path=out_root / "curator_trace.jsonl",
                # Appended, because the pooled driver promotes sidecars by NAME and a
                # per-batch filename would be deleted with the staging tree.
                trace_append=i > 1,
                trace_tag=None if n_batches == 1 else f"pairs_batch_{i}",
            )
            counts_per_batch.append(batch_counts)
            batch_records.append(
                {
                    "batch": i,
                    "pairs": [lo, hi],
                    "n_pairs": hi - lo,
                    "tool_call_budget": step_budget,
                    "read_total": batch_counts.get("read_total"),
                    "write_total": batch_counts.get("write_total"),
                    "n_super_steps": batch_counts.get("n_super_steps"),
                    "exhausted": batch_counts.get("exhausted"),
                    "repeats": batch_counts.get("repeats"),
                    "error": batch_error,
                }
            )
            if batch_error:
                errors.append(f"[pairs_batch_{i}] {batch_error}")
                if not batch_counts.get("exhausted"):
                    # Exhaustion is local to a batch: the next one is a fresh agent with
                    # a fresh budget on different pairs, so it is worth running. Any
                    # other exception is an environment failure (a dead connector, a
                    # revoked key) that would repeat, and repeating it costs another
                    # paid invocation per batch for nothing.
                    batch_records[-1]["stopped_remaining_batches"] = True
                    break
        tool_counts = _merge_tool_counts(counts_per_batch)
        agent_error = "\n\n".join(errors) if errors else None

    findings, fix_counts, fix_error = _validate_fix_pass(
        make_agent if agent_ran else None,
        bag,
        connector=connector,
        out_root=out_root,
        max_agent_steps=step_budget,
    )
    _run_adversary_signal(bag, connector=connector, out_root=out_root)
    bag.write(out_root)

    ledger, ledger_repairs = load_clarifications_with_repairs(clarifications_path(out_root))
    if ledger_repairs:
        logger.warning(
            "curator ledger for %s needed %d repair(s): %s",
            schema,
            len(ledger_repairs),
            "; ".join(ledger_repairs),
        )
    if clarifications_path(out_root).exists():
        ledger_source = "agent" if agent_ran else "preexisting"
    else:
        ledger_source = "missing"

    # The agent owns this file, so it can pre-answer its own questions and mint a
    # certified "human" fact (AUDIT C6). Strip that here, at the Phase A boundary,
    # and rewrite the artifact so nothing downstream can read the forged form.
    forged_answers: list[str] = []
    if ledger and ledger_source == "agent":
        ledger, forged_answers = quarantine_agent_answers(ledger)
        # Rewritten when the loader repaired anything too, not only on a forged
        # answer. A repair that stays in memory leaves the malformed line on disk for
        # the SME arm and every later resume to re-repair — and for any future strict
        # reader to die on. Write the normalised form once, here, where the artifact
        # is still owned.
        if forged_answers or ledger_repairs:
            write_clarifications(clarifications_path(out_root), ledger)
        if forged_answers:
            logger.warning(
                "curator agent pre-answered %d clarification(s) in %s; reset to open: %s",
                len(forged_answers),
                schema,
                ", ".join(forged_answers),
            )

    _write_run_manifest(
        out_root,
        {
            "phase": "A",
            "schema": schema,
            "agent_ran": agent_ran,
            "ledger_source": ledger_source,
            "clarification_count": len(ledger),
            # Non-empty means the agent tried to answer its own questions (AUDIT C6).
            "agent_forged_answers": forged_answers,
            # Non-empty means the agent wrote a record this loader had to normalise
            # (an out-of-enum status, an undeclared key). Recorded because the
            # alternative reading — a clean ledger — is indistinguishable otherwise,
            # and because the rate is what says whether the prompt needs the schema
            # spelled out more plainly.
            "ledger_repairs": ledger_repairs,
            "seed": seed_stats,
            # Successor to the deleted `decoy_defense` block: every suspect column in
            # this corpus is now agent-authored, so one count is the whole story. Zero
            # means the curated arm went out with no decoy defence at all.
            "suspect_columns": bag.suspect_count(),
            # Assets actually in the corpus, so a reader never has to difference a
            # call count against a YAML count to find out (which is what produced a
            # phantom "the agent deleted 21 joins" diagnosis).
            "assets": {
                "joins": len(bag.joins),
                "metrics": len(bag.metrics),
                "terms": len(bag.terms),
                "few_shots": len(bag.few_shots),
            },
            # The budget the agent actually ran under, so a capped run is legible
            # without re-deriving it from the driver's flags. Per INVOCATION: every pair
            # batch is granted this, and `recursion_limit_for()` of it is the limit each
            # one ran with. `tool_call_budget_total` is the Phase A ceiling.
            "tool_call_budget": step_budget,
            "tool_call_budget_total": step_budget * max(len(batch_records), 1),
            # Intake, recorded because its absence is what hid the 40-pair ceiling: the
            # manifest reported budgets and tool calls and never how much of the split
            # the agent was shown. `rendered` short of `available` means a pair was
            # dropped, which `plan_pair_batches` should make impossible.
            "train_pairs": {
                "available": len(train_items),
                "rendered": sum(r["n_pairs"] for r in batch_records) if batch_records else 0,
                "batches": len(batch_records),
                "max_batches": max_pair_batches,
                "per_batch_target": PAIRS_PER_BATCH,
            },
            # Per-invocation detail. The merged `tool_calls` cannot show that batch 2
            # exhausted while batch 1 did not, and that difference is the whole reason
            # to look.
            "pair_batches": batch_records,
            "tool_calls": tool_counts,
            "fix_pass_tool_calls": fix_counts,
            "error": agent_error,
            "fix_pass_error": fix_error,
            "validate_finding_count": len(findings),
            # Relative to the corpus root, not absolute. An absolute path embeds the
            # run's own output directory, so it was the one field that differed
            # between two otherwise byte-identical builds — enough to make
            # "did these two runs produce the same corpus?" un-answerable with a
            # plain diff, and it leaks a machine-local path into a durable artifact.
            "clarifications_path": clarifications_path(out_root).name,
        },
    )
    return out_root


def build_curated_corpus_with_sme(
    connector: "Connector",
    gateway: "Gateway",
    schema: str,
    train_items: Sequence["EvalItem"],
    out_root: Path | str,
    *,
    responder: "Responder",
    curated_root: Path | str | None = None,
    model: Any | None = None,
    dialect: str = "postgres",
    max_agent_steps: int | None = None,
    run_agent_repass: bool | None = None,
    seed_ledger_if_empty: bool = False,
    system_prompt: str | None = None,
    phase_a_system_prompt: str | None = None,
    settings: "Settings | None" = None,
) -> Path:
    """Phase B: answered clarifications ledger → ingest → write curated_sme corpus.

    Requires an agent-authored (or explicitly planted) open ledger. Mechanical
    ``seed_gap_clarifications`` runs **only** when ``seed_ledger_if_empty=True``
    (opt-in for ``--skip-agent``); the default path raises if the ledger is empty.

    When ``model`` is set, ``run_agent_repass`` defaults to True and the ingest
    agent folds answers (no silent deterministic fold). When ``model`` is None,
    a deterministic scope-based fold is used for offline tests.

    ``system_prompt`` injects a registered ``curator_phase_b`` variant;
    ``phase_a_system_prompt`` is forwarded to the Phase-A build this function may
    run for itself when no ``curated_root`` is supplied. Both ``None`` keep ``v1``.
    ``settings`` is what this build's run records are stamped from, and is
    forwarded to that Phase-A build for the same reason (see
    :func:`build_curated_corpus`).
    """
    from ..corpus.loader import load_corpus
    from ..corpus.schemas import TableAsset

    out_root = Path(out_root)
    out_root.mkdir(parents=True, exist_ok=True)

    if run_agent_repass is None:
        run_agent_repass = model is not None

    if curated_root is None:
        curated_root = out_root.parent / "corpus_curated"
        build_curated_corpus(
            connector,
            gateway,
            schema,
            train_items,
            curated_root,
            model=model,
            dialect=dialect,
            max_agent_steps=max_agent_steps,
            run_agent=model is not None,
            system_prompt=phase_a_system_prompt,
            settings=settings,
        )
    curated_root = Path(curated_root)

    corpus = load_corpus(curated_root, schema=schema)
    tables = [a for a in corpus.assets if isinstance(a, TableAsset)]
    other = [a for a in corpus.assets if not isinstance(a, TableAsset)]

    ledger_path = resolve_clarifications_path(curated_root, schema)
    phase_a = _phase_a_run_manifest(curated_root, schema)
    ledger_was_written = (
        phase_a is not None
        and phase_a.get("ledger_source") not in (None, "missing")
    )
    if ledger_was_written and ledger_path is None:
        raise RuntimeError(
            f"curated clarifications ledger for {schema!r} was recorded "
            f"(ledger_source={phase_a.get('ledger_source')!r}, "
            f"clarification_count={phase_a.get('clarification_count')!r}) but is "
            f"absent from both {clarifications_path(curated_root)} and "
            f"{curated_root / schema / '_build' / 'clarifications.jsonl'}; "
            "refusing to continue with a missing relocated ledger"
        )

    records = load_clarifications(ledger_path) if ledger_path is not None else []
    open_records = [r for r in records if r.status is ClarificationRecordStatus.open]
    ledger_source = "agent" if open_records else "missing"

    if not open_records and seed_ledger_if_empty and ledger_path is None:
        # Offline/--skip-agent scaffolding only: synthesize gap questions so the
        # deterministic fold has something to do.
        #
        # Never invent a ledger when a real one was resolved from the live root or
        # relocated ``<schema>/_build/`` — including an all-answered file. That is
        # the cross-resume failure: looking only at the live root made the relocated
        # ledger look absent and this scaffolding synthesised a misleading fold.
        #
        # Written to THIS arm's root, not to ``curated_root``. Seeding the shared input
        # made the *next* SME arm read those synthetic records as if the curator agent
        # had raised them: with the answer write-back removed they stay open, so
        # ``curated_sme`` found open records and stamped ``ledger_source="agent"`` — the
        # one field whose job is telling agent-authored clarifications from mechanically
        # seeded ones, lying on exactly the rung the write-back fix was repairing. Both
        # arms now report ``seed_gap`` and both still fold.
        records = seed_gap_clarifications(tables)
        # Written now, before the responder runs, and truncated by the answered write
        # further down — so in a successful build this line leaves no trace. It exists for
        # the failing build: if the responder raises (a rate limit, a dead gateway), the
        # arm root is left holding the questions that were pending, which is the only
        # record of what this build was trying to do. Without it a crashed SME build
        # leaves an arm root with no ledger at all, indistinguishable from one that never
        # got that far.
        write_clarifications(clarifications_path(out_root), records)
        open_records = [
            r for r in records if r.status is ClarificationRecordStatus.open
        ]
        ledger_source = "seed_gap"
        if not open_records:
            raise RuntimeError("seed_ledger_if_empty produced no open clarifications")
    # An empty ledger from a real agent run is NOT a failure: the agent resolved
    # everything itself, so the SME round-trip has nothing to fold and curated_sme == curated.
    # A true agent no-op is distinguishable via the Phase-A manifest's write_total.
    # Paid path with a *required* (recorded) ledger absent already raised above.

    answered = fill_clarifications_with_responder(records, responder)
    # Written to THIS arm's root only. It used to also write back into
    # ``curated_root``'s ledger, which is this build's *input* — and that voided the
    # arm of any *second* SME build over the same curated root.
    #
    # The sequence: SME build A reads ``curated``'s ledger, finds its open records,
    # answers them, and marks them answered *in curated's ledger*. Build B then reads
    # the same ledger, finds nothing open, records ``ledger_source="missing"``, folds
    # nothing, and produces a corpus identical to ``curated``. It is caught downstream
    # — ``_sme_fold_signal`` -> ``sme_noop_dbs`` -> unquotable — but only after paying
    # for the whole build. Reachable by a resume, a ``--replicate curated_sme``, or a
    # future second SME arm.
    #
    # Harmless to what either arm *serves*: the corpus loader never reads
    # ``clarifications.jsonl`` and ``_corpora_differ`` fingerprints ``*.yaml`` only. The
    # damage was confined to the ledger, which is precisely where the next arm looks.
    write_clarifications(clarifications_path(out_root), answered)
    _write_sme_clarifications_log(answered, out_root, schema=schema, tables=tables)

    # Phase B's work is bounded by the ledger, not by schema width: each answered
    # record needs a locate and a write. Derived rather than constant for the same
    # reason as Phase A — and note the old constant 15 was never the real limit
    # either, since `max(15 * 4, 100)` floored it at 100 super-steps.
    step_budget = (
        max_agent_steps if max_agent_steps is not None else 30 + 3 * len(answered)
    )

    bag = AssetBag.from_tables(schema, tables)
    for asset in other:
        if asset.asset_type == "join":
            bag.joins[asset.id] = asset  # type: ignore[assignment]
        elif asset.asset_type == "metric":
            bag.metrics[asset.id] = asset  # type: ignore[assignment]
        elif asset.asset_type == "term":
            bag.terms[asset.id] = asset  # type: ignore[assignment]
        elif asset.asset_type == "few_shot":
            bag.few_shots[asset.id] = asset  # type: ignore[assignment]

    tool_counts = _empty_tool_counts()
    fix_counts = _empty_tool_counts()
    agent_error: str | None = None
    fix_error: str | None = None
    make_agent: "Callable[[], Any] | None" = None
    agent_ran = False
    applied = 0
    fold_mode = "none"

    if not open_records:
        fold_mode = "none"  # no clarifications → nothing to fold; curated_sme == curated
    elif run_agent_repass and model is not None:
        from ..analyst.run_log import new_run_id
        from .deep_agent import build_curator_agent

        _settings = _settings_or_load(settings)
        _run_id = new_run_id()
        _thread_id = f"curator-sme:{schema}:{out_root.name}"

        def make_agent() -> Any:  # fresh agent per invoke — no shared fs/state
            return build_curator_agent(
                model,
                connector=connector,
                schema=schema,
                gateway=gateway,
                bag=bag,
                run_dir=out_root,
                system_prompt=system_prompt or _PHASE_B_PROMPT,
            )

        agent_ran = True
        fold_mode = "agent"
        user = (
            f"Ingest answered clarifications for schema `{schema}`. "
            "Read /clarifications.jsonl and fold each answered record into the "
            "corpus via annotate/upsert tools (curator/proposed provenance only). "
            f"There are {len(answered)} answered record(s). You have about "
            f"{step_budget} tool calls; several calls in ONE reply cost the same as "
            "one, so batch them, and use annotate_columns for several columns of the "
            "same table at once."
        )
        _result, tool_counts, agent_error = _invoke_agent(
            make_agent(),
            user=user,
            max_agent_steps=step_budget,
            settings=_settings,
            run_id=_run_id,
            thread_id=_thread_id,
            trace_path=out_root / "curator_sme_trace.jsonl",
        )
        # Count successful writes via tool totals; unanswered leftovers are NOT
        # folded — agent owns the fold.
        applied = tool_counts["write_total"]
    else:
        fold_mode = "deterministic"
        applied = bag.apply_answered_clarifications(answered)

    # An SME who says they do not recognise a column has delivered a reliability
    # verdict, and neither fold mode reliably records it as one: the deterministic
    # fold writes prose into the description, and the agent fold is asked to mark the
    # column but may not. Runs for both modes, after them, so the mark lands on top
    # of whatever description they wrote.
    unrecognised = bag.mark_unrecognised_columns(answered)
    if unrecognised["no_column_in_scope"]:
        print(
            f"sme fold: {unrecognised['no_column_in_scope']} unrecognised-column "
            "answer(s) had no column in scope — recorded as notes only"
        )

    # pair:/query:-scoped answers (trap / annotation-error findings) don't map to a
    # table/column asset, so the fold above skips them. Land them as governance
    # rules so the caveat reaches the served corpus instead of dying in the ledger.
    caveats_recorded = bag.record_caveats(answered)

    findings, fix_counts, fix_error = _validate_fix_pass(
        make_agent if agent_ran else None,
        bag,
        connector=connector,
        out_root=out_root,
        max_agent_steps=step_budget,
    )
    # Phase B has no separate soft-adversary pass; validate findings are all hard.
    from .adversary import gate_hard_findings

    gate_hard_findings(findings)
    bag.write(out_root)

    _write_run_manifest(
        out_root,
        {
            "phase": "B",
            "schema": schema,
            "agent_ran": agent_ran,
            "ledger_source": ledger_source,
            "fold_mode": fold_mode,
            "clarifications_applied": applied,
            "caveats_recorded": caveats_recorded,
            "unrecognised_column_marks": unrecognised,
            "suspect_columns": bag.suspect_count(),
            "clarification_count": len(answered),
            "assets": {
                "joins": len(bag.joins),
                "metrics": len(bag.metrics),
                "terms": len(bag.terms),
                "few_shots": len(bag.few_shots),
            },
            "tool_call_budget": step_budget,
            "tool_calls": tool_counts,
            "fix_pass_tool_calls": fix_counts,
            "error": agent_error,
            "fix_pass_error": fix_error,
            "validate_finding_count": len(findings),
        },
    )

    if open_records and not _corpora_differ(curated_root, out_root, schema):
        raise RuntimeError(
            f"curated_sme corpus is identical to curated at {out_root}; SME round-trip produced no edits"
        )
    return out_root
