"""Run ``g_bi_scope`` — and only it — over a question set, so the shadow projection can see it.

    uv run --frozen python tools/bi_scope_probe.py \
        --dataset ../BIRD-Data-Obfuscation/eval_dataset \
        --out runs/eval/bi_scope_gpt-5.6-luna.jsonl

Five of the six rules the served app enables are deterministic predicates over the question, so
``tools/shadow_replay.py`` replays them off a finished artifact for nothing. The sixth asks a
model whether the question is a BI task at all, and a model call is not recoverable from a row.

It is also the only prod gate whose behaviour on benign analytics traffic has never been
measured. ``api/graph_app.py`` shipped ``{BI_SCOPE_RULE_ID: True}`` and the eval driver ships
``{}``, so this rule has been on in production and off in every measured arm since it existed;
``govern/adversarial_run.py`` enables ``GUARD_RULE_IDS``, which by construction excludes it. A
false refusal here is a lost answer in production, and nothing in this tree knows the rate.

**Its own pass, not part of an arm**, because it reads only the question: no database, no
corpus, no index, no agent model. ~136 tokens a call on the utility surface, so the whole
1,351-question set is a rounding error against an arm — which is the point of running it
*before* one.

The verdict vocabulary is ``GuardVerdict``'s, unreduced: ``clear``, ``blocked`` and
``error_failed_open``. The third is not folded into the first even though the served path lets
both through, because a gate that failed open on a third of the set is a different fact from
one that cleared it, and a probe that reported them as one would be measuring its own
provider's availability.

Writes one JSON object per line as each question completes, so an interrupted probe is
resumable by hand and never returns a partial file that looks whole. Exit 2 with no credential.
"""


from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any


def _questions(dataset: Path, limit: int | None, stride: int | None) -> list[dict[str, Any]]:
    """The question set, whole or sampled.

    ``--limit`` takes a **prefix**, and a prefix of this file is one schema: the first dozen
    rows of ``test_final.jsonl`` are all ``address``. That is fine for checking the probe runs
    and worthless as an estimate of a rate, so ``--stride`` exists to walk the file evenly and
    reach all 57. Neither can feed a projection — ``shadow_replay.py`` refuses a verdict file
    that does not cover the arm, which is the property that keeps a preview from being quoted
    as the measurement.
    """
    with (dataset / "test_final.jsonl").open(encoding="utf-8") as handle:
        rows = [json.loads(line) for line in handle if line.strip()]
    if stride:
        step = max(1, len(rows) // stride)
        rows = rows[::step][:stride]
    return rows[:limit] if limit else rows


async def _probe(
    questions: list[dict[str, Any]], model: Any, out: Path, concurrency: int
) -> dict[str, int]:
    """Screen each question and append its verdict. Returns the outcome histogram.

    ``serve/nodes/guard.py::_bi_scope`` is called directly rather than reimplemented: the rule
    *is* its parsing (``_clears_scope`` refuses a reply that says both yes and no, which
    ``startswith("yes")`` did not), and a second copy here would be a second rule wearing the
    same id. ``tools/check_one_implementation.py`` is the standing objection to the copy.

    Concurrent, and the rows land in completion order rather than file order. That is safe
    because every consumer joins on ``question_id`` — and it is *stated* because an artifact
    whose order looks like the dataset's invites a reader to zip the two.
    """
    from governed_bi.serve.nodes.guard import _bi_scope

    counts: dict[str, int] = {}
    limiter = asyncio.Semaphore(concurrency)
    done = 0

    with out.open("a", encoding="utf-8") as handle:

        async def one(question: dict[str, Any]) -> None:
            nonlocal done
            async with limiter:
                verdict, _usage = await _bi_scope(str(question.get("question") or ""), model, 1)
            outcome = str(verdict["outcome"])
            counts[outcome] = counts.get(outcome, 0) + 1
            handle.write(
                json.dumps(
                    {
                        "question_id": str(question["question_id"]),
                        "outcome": outcome,
                        "rule_id": verdict.get("rule_id"),
                        # Free text and kept: on a `blocked` it carries the model's own reply,
                        # which is the only way to tell a real out-of-scope judgement from a
                        # model that misread the format. Dropped from the turn record for
                        # rule-probing reasons that do not apply to an offline probe.
                        "detail": verdict.get("detail"),
                    }
                )
                + "\n"
            )
            handle.flush()
            done += 1
            if done % 50 == 0:
                print(f"  {done}/{len(questions)} {counts}", flush=True)

        await asyncio.gather(*(one(question) for question in questions))
    return counts


def main(argv: list[str] | None = None) -> int:
    from governed_bi import credentials
    from governed_bi.model import provider as provider_mod

    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--dataset", type=Path, default=Path("../BIRD-Data-Obfuscation/eval_dataset"))
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--model",
        default=None,
        help="utility model id; defaults to GOVERNED_BI_UTILITY_MODEL, the surface the "
        "served guard actually calls",
    )
    parser.add_argument("--provider", default=None)
    parser.add_argument("--effort", default=None)
    parser.add_argument("--limit", type=int, default=None, help="a PREFIX, which is one schema")
    parser.add_argument("--stride", type=int, default=None, help="sample N evenly across the file")
    parser.add_argument("--concurrency", type=int, default=8)
    args = parser.parse_args(argv)

    model_id = args.model or credentials.secret("GOVERNED_BI_UTILITY_MODEL")
    provider = args.provider or credentials.secret("GOVERNED_BI_UTILITY_PROVIDER") or "openai"
    effort = args.effort or credentials.secret("GOVERNED_BI_UTILITY_MODEL_EFFORT")
    if not model_id:
        print(
            "no utility model: pass --model or set GOVERNED_BI_UTILITY_MODEL. The served "
            "guard calls the utility surface, so probing the agent model would measure a "
            "gate nobody runs",
            file=sys.stderr,
        )
        return 2
    if provider != "proxy" and not provider_mod.credentials_present(provider):
        names = " / ".join(provider_mod.credential_names(provider)) or "none known"
        print(f"no {provider} credential reachable ({names})", file=sys.stderr)
        return 2

    if args.out.exists():
        print(
            f"{args.out} exists. A probe appends, so re-running would write a second verdict "
            "for every question and the join in shadow_replay.py would silently take one of "
            "them. Move it or name another file.",
            file=sys.stderr,
        )
        return 2
    args.out.parent.mkdir(parents=True, exist_ok=True)

    model = provider_mod.chat_model(model_id, surface="utility", provider=provider, effort=effort)
    questions = _questions(args.dataset, args.limit, args.stride)
    print(f"g_bi_scope over {len(questions)} question(s): {model_id} on {provider}", flush=True)
    counts = asyncio.run(_probe(questions, model, args.out, args.concurrency))
    print(f"wrote {args.out}")
    print(f"outcomes: {counts}")
    blocked = counts.get("blocked", 0)
    if blocked:
        print(
            f"{blocked} benign question(s) refused by the scope gate. Every one is an answer "
            "production does not give, and the arm this projects onto never paid that cost."
        )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
