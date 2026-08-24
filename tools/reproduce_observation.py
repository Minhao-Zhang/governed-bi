#!/usr/bin/env python
"""Does this failure still happen? Answered per question, at $0, with the agent model off.

    uv run --frozen python tools/reproduce_observation.py --observation obs-...
    uv run --frozen python tools/reproduce_observation.py --patch pat-...        # every one it answers
    uv run --frozen python tools/reproduce_observation.py --patch pat-... --record
    uv run --frozen python tools/reproduce_observation.py --state open --embed   # the whole queue
    uv run --frozen python tools/reproduce_observation.py --state open --embed --decline

**Exit codes, and they are a contract**: 0 = nothing reproduces, or nothing here was checkable.
1 = at least one failure still reproduces. 2 = the tool could not run — bad arguments, no corpus, no
database. A configuration error must never come back as 1: ``raise SystemExit("message")`` exits 1,
so "you did not tell me where the corpus is" was arriving at the caller as "the failure still
reproduces", and the observation got recorded as still failing on a run that never happened.
Everything that cannot run raises :class:`_Misconfigured` and ``main`` is the only place a code is
chosen.

**``--state`` is how a steward with an untriaged queue and no patch gets in.** Measured against the
live store on 2026-08-24: 71 of its 73 observations were ``open``, **52 of them no longer
reproduced**, and 71 carried a ``corpus_content_hash`` the loaded corpus does not have. All 71 in
one process with ``--embed`` took **72 seconds**, because ``_recheck`` batches them into one
compiled graph; the same rows one at a time through ``--observation`` was measured at 32 minutes,
which is the difference between a check that gets run and one that does not.

**T3 of the ladder, and the only tier that answers a question about the complaint rather than
about the corpus.** T0–T2 ask whether an edit broke something. This asks whether the thing somebody
filed is still true.

**The claim it makes is narrow and stated in the output every time**: the tables the reference
answer reads are reachable again. That is not the same as the answer being right. On turns where
every gold table *was* licensed **and the gold names at least one table**, measured accuracy is
0.7555 (n=1,145) — so about one in four complaints
"fixed" by this check would still come back with a wrong number. `retrieval_verified` is the
narrowest upgrade this licenses and the vocabulary has no `resolved`.

**Why it is free.** A session with ``agent_model=None`` serves the stub answer path: facets,
routing, retrieval, resolve and connect all run for real and no provider is called. The vector
cache is warm, so an unchanged corpus embeds nothing and a one-field edit costs 2 embed calls.
That is the whole reason this tier exists at a per-question resolution while EX cannot: EX's MDE is
2.33pp and the largest single coverage bucket is 7 questions, which is 0.52pp.

**It only applies to a coverage failure.** If the observation carries no ``gold_sql``, or the gold
statement does not parse, or every gold table was licensed at the time it was filed, there is
nothing here to re-check and the tool says which of the three — never "passed". A patch that
touched only a ``body`` is the same case: ``body`` does not enter the retrieval index, so retrieval
cannot see the change and the honest tier is T4, which costs money.

**Pass ``--embed``.** Without it the check runs lexical-only, and the arms were measured with an
embedder. Driving one observation both ways: the row recorded **1** missing gold table and the
lexical re-check reported **2** — a "still reproduces" that is an artefact of the retrieval channel
and reads exactly like a real finding. The channel is named in every run's output and the lexical
one warns.

Reuses ``eval/datalake.py``'s ``routing_recall`` and ``gold_tables`` rather than re-deriving them.
A second coverage definition here would be a second answer to the question every arm on disk was
measured with.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

import corpus_target  # noqa: E402 - sibling script

from governed_bi import credentials
from governed_bi.eval.datalake import gold_tables, routing_recall
from governed_bi.feedback.events import DeclineReason, Observation, ObservationState
from governed_bi.feedback.lifecycle import TRANSITIONS, TransitionRefused, transition_for
from governed_bi.feedback.store import FeedbackStore, Rejected
from governed_bi.paths import REPO_ROOT

DEFAULT_DB = "runs/feedback.sqlite"

#: The environment variable the corpus root comes from. Read through
#: :func:`governed_bi.credentials.secret`, which looks at the environment and then at ``.env`` --
#: the same reader ``_connector`` already used for the DSN. It did not, and the asymmetry showed:
#: ``.env`` sets ``GOVERNED_BI_CORPUS_DIR=../BIRD-corpus`` and this tool reported it unset while
#: finding the DSN in the file two functions away.
CORPUS_DIR_VAR = "GOVERNED_BI_CORPUS_DIR"


#: The shared type, aliased rather than redeclared. This file had its own ``_Misconfigured``
#: and ``corpus_target.resolve_corpus_dir`` raises the shared one -- two types for one concept,
#: so the ``except`` here stopped catching what the resolver threw and this tool went back to
#: exiting 1. One name, one type.
_Misconfigured = corpus_target.Misconfigured

#: The claim, in one place, because a CLI and a screen disagreeing about what a green T3 means is
#: the two-answers defect the derived states exist to avoid.
CLAIM = (
    "the tables the reference answer reads are reachable again. NOT that the answer is right: on "
    "turns where every gold table was licensed and the gold names at least one table, measured "
    "accuracy is 0.7555 (n=1,145; 0.7131 over all 1,272 covered turns, the 127 whose gold "
    "reads no table included)."
)


@dataclass(frozen=True, slots=True)
class Outcome:
    """One observation's answer. ``reproduced is None`` means the check does not apply."""

    observation_id: str
    reproduced: bool | None
    detail: str
    missing_now: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "observation_id": self.observation_id,
            "reproduced": self.reproduced,
            "detail": self.detail,
            "missing_now": list(self.missing_now),
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--observation", default=None)
    parser.add_argument("--patch", default=None, help="every observation this patch answers")
    parser.add_argument(
        "--state",
        default=None,
        metavar="STATE[,STATE]",
        help="every observation in these stored states, e.g. 'open'. 'all' means every declared "
        "state. This is the bulk way in: one compiled graph for the whole batch, measured at 72 "
        "seconds for the 71-row open queue against 32 minutes one --observation at a time",
    )
    parser.add_argument(
        "--decline",
        action="store_true",
        help="WRITES. Close every observation that no longer reproduces as "
        "declined/cannot_reproduce. Off by default because `declined` is terminal and a batch that "
        "silently closed 52 rows is a batch nobody can undo",
    )
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--corpus-dir", default=None, help=f"defaults to {CORPUS_DIR_VAR}")
    parser.add_argument(
        "--embed",
        action="store_true",
        help="build the index with an embedder, which is what an arm is measured with. Free on a "
        "warm vector cache; a one-field edit costs 2 embed calls. WITHOUT this the check runs "
        "lexical-only and its coverage is not the arm's",
    )
    parser.add_argument(
        "--embedding-provider",
        default="openai",
        help="the default follows the warm cache: runs/vectors/ holds 176 MB under "
        "text-embedding-3-large against 7 MB under the Titan directories, so that is the channel "
        "the rows being re-checked were measured on",
    )
    parser.add_argument("--embedding-model", default=None)
    parser.add_argument(
        "--record",
        action="store_true",
        help="write the result to the patch's ladder as T3. Needs --patch",
    )
    args = parser.parse_args(argv)

    # `.env` bridged into `os.environ`, the way every other entry point in this repo does it.
    # Bridged rather than only read through `credentials.secret`, because the embedder's client
    # reads `os.environ` itself -- `model/provider.py` calls `os.environ.get` for the key -- so a
    # `--embed` run needs the file's values *in the environment*, not just visible to this module.
    credentials.load_into_environ()

    selectors = [bool(args.observation), bool(args.patch), bool(args.state)]
    if sum(selectors) != 1:
        print("pass exactly one of --observation, --patch, --state", file=sys.stderr)
        return 2
    if args.record and not args.patch:
        print("--record needs --patch: a T3 result is recorded on the patch", file=sys.stderr)
        return 2

    try:
        return _run(args)
    except _Misconfigured as exc:
        # 2 and not 1. This is the whole reason `_Misconfigured` exists: `SystemExit("message")`
        # exits 1, and 1 means "the failure still reproduces".
        print(str(exc), file=sys.stderr)
        return 2


def _run(args: argparse.Namespace) -> int:
    store = FeedbackStore(_resolve(args.db))
    observations = _population(
        store,
        observation_id=args.observation,
        patch_id=args.patch,
        states=_states(args.state) if args.state else None,
    )
    if observations is None:
        return 2
    if not observations:
        print("nothing to re-check.")
        return 0

    checkable = [o for o in observations if _why_not(o) is None]
    skipped = [(o, _why_not(o)) for o in observations if _why_not(o) is not None]

    channel = "lexical+semantic" if args.embed else "lexical only"
    outcomes: list[Outcome] = []
    if checkable:
        session = _session(
            _corpus_root(args.corpus_dir),
            embed=bool(args.embed),
            provider=str(args.embedding_provider),
            model=args.embedding_model,
        )
        outcomes = _recheck(checkable, session)
    outcomes += [Outcome(o.observation_id, None, why or "") for o, why in skipped]

    still = [o for o in outcomes if o.reproduced is True]
    gone = [o for o in outcomes if o.reproduced is False]

    for outcome in outcomes:
        mark = {True: "STILL", False: "gone ", None: "n/a  "}[outcome.reproduced]
        print(f"{mark}  {outcome.observation_id}  {outcome.detail}")
        for table in outcome.missing_now[:5]:
            print(f"         missing: {table}")

    print(
        f"\n{len(still)} still reproduce, {len(gone)} do not, "
        f"{len(outcomes) - len(still) - len(gone)} not applicable. Retrieval channel: {channel}."
    )
    if gone:
        print(f"What a `gone` means: {CLAIM}")
    if not args.embed and (still or gone):
        print(
            "  WARNING: this ran LEXICAL-ONLY and the arm that produced these rows was measured "
            "with an embedder, so the coverage here is not the arm's. Driving one observation "
            "without --embed returned 2 missing gold tables where the row recorded 1 -- a "
            "false 'still reproduces' that reads exactly like a real finding. Pass --embed."
        )

    if args.decline:
        moved, refused = _decline_stale(store, outcomes, channel=channel)
        print(f"\ndeclined {len(moved)} as cannot_reproduce; could not move {len(refused)}.")
        for observation_id, why in refused:
            print(f"  not moved  {observation_id}  {why}")
    elif gone:
        print(
            f"  {len(gone)} of these no longer reproduce and are still in the queue. "
            "Re-run with --decline to close them as cannot_reproduce."
        )

    if args.record:
        passed = tier_verdict(outcomes)
        if passed is None:
            print(
                f"recorded nothing on {args.patch}: T3 does not apply to any of these "
                f"{len(outcomes)} observation(s). An unrun tier is absent from the ladder, not a "
                "failure -- a `body`-only edit is answerable at T4 and nowhere cheaper."
            )
            return _code(still)
        store.record_ladder(
            str(args.patch),
            "T3",
            {
                "tier": "T3",
                "passed": passed,
                "detail": (
                    f"{len(gone)} of {len(outcomes)} observation(s) no longer miss a gold table, "
                    f"on {channel}. {CLAIM}"
                ),
                "retrieval_channel": channel,
                "outcomes": [o.as_dict() for o in outcomes],
            },
        )
        print(f"recorded T3 on {args.patch} (passed={passed})")
    return _code(still)


def _code(still: list[Outcome]) -> int:
    """1 if anything still reproduces, else 0. **Not conditional on ``--record``.**

    It was. ``return 1`` sat inside ``if args.record`` and every other path fell through to
    ``return 0``, so a run that printed "20 still reproduce" handed the caller 0 unless it also
    happened to be recording a ladder row. Together with the ``SystemExit`` above, the exit code
    said "still reproduces" when the tool had not run and "clean" when it had found twenty --
    exactly inverted, and both halves silent.
    """
    return 1 if still else 0


# ── the check ─────────────────────────────────────────────────────────────────


def _why_not(observation: Observation) -> str | None:
    """Why this observation cannot be re-checked for free, or ``None``.

    Three answers rather than one, because they send the reader somewhere different: no gold
    statement means the row came from a person and not an artifact; an unparseable one is a dataset
    defect; and a row whose gold tables were all licensed already was never a coverage failure, so
    a coverage check cannot say anything about it.
    """
    if not observation.gold_sql:
        return "no gold_sql, so there is no reference answer to check coverage against"
    if gold_tables(str(observation.gold_sql)) is None:
        return "the gold statement does not parse -- a dataset defect, not a corpus gap"
    if not observation.missing_tables:
        return (
            "every gold table was licensed when this was filed, so it was not a coverage "
            "failure and coverage cannot answer it"
        )
    return None


def tier_verdict(outcomes: list[Outcome]) -> bool | None:
    """Did T3 pass, fail, or **not apply** -- ``None`` for the third.

    ``Outcome.reproduced`` is already tri-state and this used to collapse it to a boolean with
    ``bool(gone) and not still``, which reads an all-not-applicable run as a **failure**: both lists
    are empty, so the tier records ``passed: False`` and the tool exits 1. A ``body``-only patch is
    exactly that run -- ``body`` does not enter the retrieval index, so retrieval cannot see the edit
    and the honest tier is T4 -- and it was being handed a red T3 instead.

    ``verify_patch.py`` states the rule: a tier that cannot run is *absent* from the ladder rather
    than recorded as skipped-therefore-fine. Absent and not failed, because a failed tier blocks a
    handoff that nothing here has evidence against.

    A mixed run reads only the observations it could check. A not-applicable one is reported in
    ``outcomes`` for the reviewer and is not folded into the verdict.
    """
    checked = [o.reproduced for o in outcomes if o.reproduced is not None]
    if not checked:
        return None
    return not any(checked)


def _recheck(observations: list[Observation], session: object) -> list[Outcome]:
    """Re-route every question through the free path and compare coverage.

    One ``routing_recall`` call for the whole batch rather than one per question: it compiles the
    graph once and evicts each thread as it goes, and compiling per question is what makes a
    "free" check slow enough that nobody runs it.
    """
    questions = [
        {
            "question_id": o.question_id or o.observation_id,
            "question": o.question,
            "db_id": o.db_id or (o.schemas[0] if o.schemas else ""),
        }
        for o in observations
    ]
    rows = {str(r["question_id"]): r for r in routing_recall(questions, session=session)}

    out: list[Outcome] = []
    for observation in observations:
        key = observation.question_id or observation.observation_id
        row = rows.get(str(key))
        if row is None:  # pragma: no cover - routing_recall returns one row per question
            out.append(Outcome(observation.observation_id, None, "the re-run produced no row"))
            continue
        wanted = gold_tables(str(observation.gold_sql)) or set()
        licensed = {str(t) for t in (row.get("licensed") or ())}
        missing = sorted(wanted - licensed)
        was_missing = len(observation.missing_tables)
        if missing:
            out.append(
                Outcome(
                    observation.observation_id,
                    True,
                    f"{len(missing)} of {len(wanted)} gold table(s) still not licensed "
                    f"(was {was_missing})",
                    tuple(missing),
                )
            )
        else:
            out.append(
                Outcome(
                    observation.observation_id,
                    False,
                    f"all {len(wanted)} gold table(s) licensed now; {was_missing} were missing",
                )
            )
    return out


# ── plumbing ──────────────────────────────────────────────────────────────────


def _states(raw: str) -> tuple[ObservationState, ...]:
    """``"open,triaged"`` or ``"all"`` as declared states. A typo is exit 2, not a finding.

    One flag rather than ``--open`` and ``--all`` beside it. Those would be two more spellings of a
    population ``--state`` already names, kept in sync by hand, and the day a seventh state is
    declared ``--state`` needs no change while an ``--open`` alias needs a sibling. ``all`` is a
    sentinel because six enum names is not a command anybody types -- and it means *every* declared
    state, terminal ones included: a ``declined`` row somebody wants to re-check is a steward
    doubting their own decline, which is a thing to support rather than to refuse.
    """
    if raw.strip() == "all":
        return tuple(ObservationState)
    out: list[ObservationState] = []
    for name in (part.strip() for part in raw.split(",")):
        if not name:
            continue
        try:
            out.append(ObservationState(name))
        except ValueError as err:
            raise _Misconfigured(
                f"--state {name!r} is not an observation state. Declared: "
                f"{', '.join(s.value for s in ObservationState)}, or 'all'."
            ) from err
    if not out:
        raise _Misconfigured("--state was empty. Name at least one state, or 'all'.")
    return tuple(out)


def _by_state(store: FeedbackStore, states: tuple[ObservationState, ...]) -> list[Observation]:
    """Every row in those states, paged to the end.

    ``store.queue`` defaults to 50 and reports ``truncated`` for exactly this reason. The live queue
    is 72, so a selector that took the first page would have re-checked 50 of them and reported a
    total as if it were the total -- the same silent-partial defect the ``--state`` flag exists to
    fix.
    """
    rows: list[Observation] = []
    offset = 0
    while True:
        page = store.queue(states=list(states), limit=200, offset=offset)
        rows.extend(page.rows)
        if not page.truncated or not page.rows:
            return rows
        offset += len(page.rows)


def _route_to_declined(state: ObservationState) -> tuple[ObservationState, ...]:
    """The declared hops from ``state`` to ``declined``, or ``()`` if there are none.

    ``TRANSITIONS`` declares no ``open -> declined`` edge, so an open row goes through ``triaged``
    first. That is not a workaround for the table -- the re-check **is** the look ``triaged``
    records, and a steward who reads a batch report has looked at every row in it.

    Read off ``TRANSITIONS`` rather than through ``lifecycle.allowed_next``, whose docstring states
    it is "read by ``tests/feedback/`` and by nothing else"; that sentence is a claim about the table
    not reaching the client, and calling it from here would falsify the words without changing the
    fact. Two hops is the search depth because the table's longest route to ``declined`` is two.
    """
    if _declared(state, ObservationState.declined):
        return (ObservationState.declined,)
    for middle in sorted(to for (frm, to) in TRANSITIONS if frm == state):
        if _declared(middle, ObservationState.declined):
            return (middle, ObservationState.declined)
    return ()


def _declared(frm: ObservationState, to: ObservationState) -> bool:
    try:
        transition_for(frm, to)
    except TransitionRefused:
        return False
    return True


def _decline_stale(
    store: FeedbackStore, outcomes: list[Outcome], *, channel: str
) -> tuple[list[str], list[tuple[str, str]]]:
    """Close every ``gone`` row as ``cannot_reproduce``. Returns what moved and what did not.

    **First producer of ``DeclineReason.cannot_reproduce``.** It has been declared since the
    vocabulary was written, with one copy string on the review client and nothing anywhere that
    could set it — a decline reason no decline could carry.

    ``reproduced is False`` only. A ``None`` outcome is "coverage cannot answer this", and closing
    one as ``cannot_reproduce`` would report a check that never ran as a check that passed.

    One refused move must not lose the other 51, so a refusal is collected and named. Both halves
    are returned rather than printed here: a function that decides *and* reports is a function whose
    caller cannot tell 52 attempted from 52 landed, which is the number the operator acts on.
    """
    detail = f"re-checked on {channel}: every gold table is licensed again"
    moved: list[str] = []
    refused: list[tuple[str, str]] = []
    for outcome in outcomes:
        if outcome.reproduced is not False:
            continue
        observation = store.get(outcome.observation_id)
        if observation is None:  # pragma: no cover - selected from this store moments ago
            refused.append((outcome.observation_id, "no longer in the store"))
            continue
        route = _route_to_declined(observation.state)
        if not route:
            refused.append(
                (
                    outcome.observation_id,
                    f"TRANSITIONS declares no route from {observation.state.value} to declined",
                )
            )
            continue
        try:
            for hop in route:
                store.move(
                    outcome.observation_id,
                    to=hop,
                    decline_reason=(
                        DeclineReason.cannot_reproduce
                        if hop is ObservationState.declined
                        else None
                    ),
                    detail=detail,
                )
        except (Rejected, TransitionRefused, KeyError) as exc:
            refused.append((outcome.observation_id, str(exc)))
            continue
        moved.append(outcome.observation_id)
    return moved, refused


def _population(
    store: FeedbackStore,
    *,
    observation_id: str | None,
    patch_id: str | None,
    states: tuple[ObservationState, ...] | None = None,
) -> list[Observation] | None:
    if states is not None:
        return _by_state(store, states)
    if observation_id:
        observation = store.get(observation_id)
        if observation is None:
            print(f"no observation {observation_id!r}", file=sys.stderr)
            return None
        return [observation]
    patch = store.get_patch(str(patch_id))
    if patch is None:
        print(f"no patch {patch_id!r}", file=sys.stderr)
        return None
    if patch.field_path == "body":
        print(
            f"patch {patch_id} edits a `body`. `body` does not enter the retrieval index -- "
            "`serve/context.py` puts it in the model's prompt -- so retrieval cannot see this "
            "change and T3 has nothing to say about it. The honest tier is T4, which costs money.",
            file=sys.stderr,
        )
        return None
    return list(store.observations_of(str(patch_id)))


def _session(
    corpus_root: Path, *, embed: bool, provider: str = "openai", model: str | None = None
) -> object:
    """A session with **no agent model**, which is what makes this free.

    Built through ``session.from_corpus_dir`` with the same kwargs
    ``tools/run_datalake_eval.py`` uses, minus the agent, so the routing this measures is the
    routing an arm measures. Assembling one here would be a second engine, and a coverage number
    off a second engine cannot be compared with the arm's.

    **The embedder is the whole comparability question**, which is why it is a flag and not a
    default. Measured while building this: one observation recorded with 1 missing gold table came
    back with **2** on a lexical-only re-check, because lexical and embedded retrieval have
    different coverage ceilings. That is a false "still reproduces" that reads like a finding, so
    the channel is named in the output either way and the run without ``--embed`` warns.

    The provider defaults to ``openai`` rather than to the driver's ``proxy``, and the reason is on
    disk: ``runs/vectors/`` holds 176 MB under ``text-embedding-3-large`` and 7 MB under the two
    Titan directories, so that is the channel the rows being re-checked were measured on -- and the
    one whose cache is warm enough for this to be free. An arm measured on anything else is not
    comparable, which is a limit of this tool rather than something it can paper over; ``--embedding
    -provider`` and ``--embedding-model`` are there for that case.
    """
    from governed_bi.govern.policy import GovernancePolicy
    from governed_bi.serve import session as session_mod

    kwargs: dict[str, object] = {
        "connector": _connector(),
        "policy": GovernancePolicy(guard_rules_enabled={}),
        "agent_model": None,
    }
    if embed:
        from governed_bi.model import provider as provider_mod
        from governed_bi.retrieve.vector_cache import vector_cache_from_environment

        embedder = provider_mod.embedder(
            model or provider_mod.default_embedding_model(provider), provider=provider
        )
        kwargs["embedder"] = embedder
        # Keyed on the provider-qualified model, so a proxy-served vector is never handed to an
        # OpenAI-served run of the same width. This is also what makes the check free: on an
        # unchanged corpus every entry is a hit and no request leaves the process.
        kwargs["vector_cache"] = vector_cache_from_environment(model=embedder.requested_model)
    return session_mod.from_corpus_dir(corpus_root, **kwargs)


def _connector() -> object:
    from governed_bi import credentials
    from governed_bi.datasource.postgres import PostgresConnector

    dsn = credentials.secret(*credentials.PG_DSN_NAMES)
    if not dsn:
        raise _Misconfigured(
            f"no database: set one of {' / '.join(credentials.PG_DSN_NAMES)} in the environment "
            f"or {credentials.DOTENV.name}. Routing resolves against a live catalog, and a stub "
            "would measure a different engine."
        )
    return PostgresConnector(dsn)


def _corpus_root(explicit: str | None) -> Path:
    """``--corpus-dir``, then the environment, then ``.env``. In that order and no other.

    Delegated to :mod:`corpus_target`, which is where the reasoning now lives: three other tools
    asked the same question with their own copies, and the copies disagreed about whether ``.env``
    counts. This one already got it right, which is exactly why it is the version that moved.
    """
    return corpus_target.resolve_corpus_dir(explicit)


def _resolve(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (REPO_ROOT / path)


if __name__ == "__main__":
    sys.exit(main())
