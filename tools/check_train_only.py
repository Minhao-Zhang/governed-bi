"""Detect held-out test question text leaking into a corpus.

Checks: provenance citing test split; verbatim containment (min NGRAM content
words; few_shots exempt with stricter provenance); n-gram rate vs a train-only
control. Paraphrase leaks are undetectable — a pass is not cleanliness.
"""


from __future__ import annotations

import argparse
import collections
import json
import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

DEFAULT_CONTROL = "corpora/gold-semantic-layer-20260804"
DEFAULT_DATASET = REPO.parent / "BIRD-Data-Obfuscation" / "eval_dataset" / "test_final.jsonl"

#: Content-word window for containment / n-gram checks.
NGRAM = 5

#: ``few_shot`` summaries are train questions; exempt from containment, stricter provenance instead.
CONTAINMENT_EXEMPT = frozenset({"few_shot"})

#: Words dropped before windowing, so "how many of the" does not carry a collision by itself.
FILLER = frozenset(
    "a an the of and or in on for to with by is are was were be been from as at that this it its "
    "which who whom what when where how many much per each all any not no do does did there".split()
)


def words(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in FILLER]


def windows(tokens: list[str], n: int) -> set[tuple[str, ...]]:
    return {tuple(tokens[i : i + n]) for i in range(max(len(tokens) - n + 1, 0))}


def corpus_texts(root: pathlib.Path) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """``(authored_texts, provenance_texts)``, both as ``(where, text)``. Never raises.

    The two are kept apart because they are checked differently: authored prose is compared
    against question wording, while ``audit`` is compared against question *ids* and file names.
    An id in a summary would be strange; an id in ``audit.evidence`` is the normal case and is
    exactly where a leak would announce itself.
    """
    from governed_bi.corpus.store import load

    assets, problems = load(root)
    if problems:
        print(f"  note: {len(problems)} load problems in {root.name}", file=sys.stderr)
    authored: list[tuple[str, str]] = []
    provenance: list[tuple[str, str]] = []
    for asset in assets:
        kind = asset.asset_type.value
        for field in ("summary", "body"):
            value = getattr(asset, field, None)
            if isinstance(value, str) and value.strip():
                # The type is carried in the label so the containment check can exempt a type
                # without a second pass over the corpus.
                authored.append((f"{kind}:{asset.id}.{field}", value))
        for index, rule in enumerate(getattr(asset, "rules", ()) or ()):
            if isinstance(rule, str) and rule.strip():
                authored.append((f"{kind}:{asset.id}.rules[{index}]", rule))
        audit = getattr(asset, "audit", None)
        provenance.append(
            (
                f"{kind}:{asset.id}.audit",
                json.dumps(_plain(audit), default=str) if audit is not None else "",
            )
        )
    return authored, provenance


def _plain(value: object) -> object:
    """Dataclasses and mappings to something ``json.dumps`` will walk."""
    if hasattr(value, "__dataclass_fields__"):
        return {f: _plain(getattr(value, f)) for f in value.__dataclass_fields__}
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value


def scan(root: pathlib.Path, questions: list[dict]) -> dict:
    """The three checks over one corpus."""
    authored, provenance = corpus_texts(root)

    held_out_ids = {str(q.get("question_id")) for q in questions}
    cited: list[tuple[str, str]] = []
    unproven: list[str] = []
    for where, blob in provenance:
        if "test_final" in blob:
            cited.append((where, "test_final.jsonl"))
        for token in re.findall(r"[A-Za-z0-9_]+", blob):
            if token in held_out_ids:
                cited.append((where, token))
        # The other half of the few-shot exemption: excused from the wording check, so it owes a
        # provable source. A few-shot citing nothing is not a leak, but it is the one asset class
        # whose origin can be checked exactly, and an unproven one is where a leak would hide.
        if where.startswith("few_shot:") and "train_final" not in blob:
            unproven.append(where)

    # question n-gram -> the question that owns it. A window shared by two questions is kept
    # under the first; attributing it twice would double-count one collision.
    owner: dict[tuple[str, ...], str] = {}
    # Only questions long enough to be identifiable are containment candidates. A short one is
    # a substring of something by luck, which is the false-positive class that made the first
    # draft of this file useless.
    candidates: list[tuple[str, str]] = []
    for question in questions:
        tokens = words(str(question.get("question") or ""))
        qid = str(question.get("question_id"))
        if len(tokens) >= NGRAM:
            candidates.append((qid, " ".join(tokens)))
        for window in windows(tokens, NGRAM):
            owner.setdefault(window, qid)

    exact: list[tuple[str, str]] = []
    collisions: dict[str, set[str]] = collections.defaultdict(set)
    for where, text in authored:
        tokens = words(text)
        joined = " ".join(tokens)
        if len(tokens) >= NGRAM and where.split(":", 1)[0] not in CONTAINMENT_EXEMPT:
            # One direction only: a held-out question sitting inside authored text. The reverse
            # -- authored text inside a question -- is what produced 67 false positives, because
            # every short few-shot summary is a substring of some longer question.
            for qid, question_text in candidates:
                if question_text in joined:
                    exact.append((where, qid))
        for window in windows(tokens, NGRAM):
            qid = owner.get(window)
            if qid is not None:
                collisions[where].add(qid)

    return {
        "corpus": str(root.relative_to(REPO)) if root.is_relative_to(REPO) else str(root),
        "texts_scanned": len(authored),
        "provenance_blocks": len(provenance),
        "test_provenance_citations": len(cited),
        "citation_examples": cited[:10],
        "few_shots_without_train_provenance": len(unproven),
        "unproven_examples": unproven[:10],
        "exact_containments": len(exact),
        "exact_examples": exact[:10],
        "assets_with_ngram_collision": len(collisions),
        "collision_rate": round(len(collisions) / max(len(authored), 1), 5),
        "questions_touched": len({q for qids in collisions.values() for q in qids}),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="check_train_only", description=__doc__)
    parser.add_argument("corpus", help="the corpus under test")
    parser.add_argument(
        "--control",
        default=DEFAULT_CONTROL,
        help="a corpus certified train-only, measured alongside as the reference rate. "
        "Pass '' to skip, which makes the result uninterpretable and is for debugging only.",
    )
    parser.add_argument("--dataset", type=pathlib.Path, default=DEFAULT_DATASET)
    parser.add_argument(
        "--tolerance",
        type=float,
        default=2.0,
        help="fail when the corpus's collision rate exceeds the control's by this factor",
    )
    args = parser.parse_args(argv)

    if not args.dataset.exists():
        print(f"no test set at {args.dataset}", file=sys.stderr)
        return 2
    questions = [
        json.loads(line)
        for line in args.dataset.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    print(f"{len(questions)} held-out questions, {NGRAM}-gram windows over content words\n")

    under_test = scan(REPO / args.corpus, questions)
    print(json.dumps(under_test, indent=2))

    if under_test["test_provenance_citations"]:
        print(
            f"\nFAIL: {under_test['test_provenance_citations']} audit block(s) cite the held-out "
            "set. Provenance is exact -- a corpus whose own record names a test question was "
            "authored against it.",
            file=sys.stderr,
        )
        return 1

    if under_test["exact_containments"]:
        print(
            f"\nFAIL: {under_test['exact_containments']} authored field(s) contain a held-out "
            f"question verbatim ({NGRAM}+ content words, few_shot excluded). This is not a rate "
            "to compare -- it is the leak itself.",
            file=sys.stderr,
        )
        return 1

    if under_test["few_shots_without_train_provenance"]:
        print(
            f"\nFAIL: {under_test['few_shots_without_train_provenance']} few-shot(s) do not cite "
            "the train file. They are excused the wording check on the grounds that their source "
            "is provable, so one that proves nothing is not excused.",
            file=sys.stderr,
        )
        return 1

    if not args.control:
        print("\nno control arm: the collision rate above is uninterpretable on its own")
        return 0

    control = scan(REPO / args.control, questions)
    print(f"\ncontrol ({control['corpus']}): rate {control['collision_rate']}")
    ratio = under_test["collision_rate"] / max(control["collision_rate"], 1e-9)
    print(f"ratio to control: {ratio:.2f}x  (tolerance {args.tolerance}x)")

    if ratio > args.tolerance:
        print(
            f"\nFAIL: {ratio:.2f}x the control's collision rate. Not proof of a leak -- "
            "inspect the colliding assets and decide.",
            file=sys.stderr,
        )
        return 1
    print("\nok: no verbatim containment, collision rate in line with the train-only control")
    print(
        "This is the absence of the cheap failure, not proof of cleanliness -- a reworded "
        "leak is invisible here."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
