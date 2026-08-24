#!/usr/bin/env python
"""Did a patch land? Answered from the corpus, and stored nowhere (ADR 0015 §3).

    uv run --frozen python tools/check_landed.py                       # every exported patch
    uv run --frozen python tools/check_landed.py --patch pat-...
    uv run --frozen python tools/check_landed.py --verify --bundle ./bundles/bnd-pat-...

**No webhook, no callback, and no state to keep in step.** The engine learns a change landed by
reading the corpus it already loads. A landed asset carries ``obs:<observation_id>`` in
``Provenance.source_refs``, which makes the join cheap — but the *corroboration* is the text: the
asset has to be present **and** hold what the bundle said it would. ``source_refs`` is unvalidated
free text in a human-editable file, so a typo makes a change invisible and a copied block attributes
one to a complaint it did not come from. This reports; it does not infer a state from a string it
cannot check.

**Four states and not two**, because the two-state version silently mislabels the common case. Two
bundles landing in one week make exact-hash matching fail for a change that *did* ship, and a
two-state model calls that "handed off, forever" — which is the unclosable ``open: true`` row this
whole design replaces, reintroduced one level up.

**``retrieval_verified`` is the fifth state and the only one that needs something to have been
run.** The other four are read off the corpus. This one says the tables the affected question needs
are reachable again, which is a claim about a *question* and not about a tree -- so it comes from the
patch's own T3 row, written by ``tools/reproduce_observation.py --record``. A patch nobody ran it on
reports ``landed_matched``, not a failure: an unrun check must not read as a failed one.

``--verify`` asks the other question: is this bundle still applicable? Between export and commit the
corpus can move, and the honest answers are three — applies cleanly, the base moved but the field is
untouched (re-export and go), or the field changed under it (back to the steward). Without this the
engineer learns it from a conflict, which is a worse place.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import corpus_target  # noqa: E402

from governed_bi.corpus.hash import corpus_content_hash
from governed_bi.corpus.patch import (
    FieldNotLocatable,
    StaleValue,
    UnwritableValue,
    apply_edit,
    read_field,
)
from governed_bi.corpus.store import load
from governed_bi.feedback.events import DerivedState, PatchState
from governed_bi.feedback.lifecycle import derived_state
from governed_bi.feedback.store import FeedbackStore
from governed_bi.paths import REPO_ROOT

DEFAULT_DB = "runs/feedback.sqlite"

#: What each derived state means to the person reading this, in the words the UI uses. One source
#: for the sentence, because a CLI and a screen disagreeing about whether something landed is the
#: two-answers defect in miniature.
_SENTENCE = {
    DerivedState.handed_off: "not in the engine yet -- nobody has committed it",
    DerivedState.landed_verified: "in the corpus this server runs, and the hash matches exactly",
    DerivedState.landed_matched: "in the corpus this server runs, alongside other changes that landed with it",
    DerivedState.retrieval_verified: "landed, and the tables the question needs are reachable again",
    DerivedState.superseded: "the corpus moved and this change is not in it -- dropped or rewritten on the way",
}


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--corpus-dir", default=None, help="defaults to GOVERNED_BI_CORPUS_DIR")
    parser.add_argument("--patch", default=None, help="one patch id; default is every exported one")
    parser.add_argument("--bundle", default=None, help="with --verify: the bundle directory")
    parser.add_argument(
        "--verify",
        action="store_true",
        help="ask whether a bundle still applies, rather than whether it landed",
    )
    args = parser.parse_args(argv)

    corpus_root = _corpus_root(args.corpus_dir)
    store = FeedbackStore(_resolve(args.db))

    if args.verify:
        return _verify(store, corpus_root, patch_id=args.patch, bundle=args.bundle)
    return _report(store, corpus_root, patch_id=args.patch)


def _report(store: FeedbackStore, corpus_root: Path, *, patch_id: str | None) -> int:
    loaded_hash = corpus_content_hash(corpus_root)
    assets, _ = load(corpus_root)
    text_now = {
        str(getattr(a, "id", "")): (str(getattr(a, "summary", "")), str(getattr(a, "body", "") or ""))
        for a in assets
    }
    refs = _source_refs(assets)

    patches = (
        [p for p in (store.get_patch(patch_id),) if p is not None]
        if patch_id
        else [p for p in _exported(store)]
    )
    if not patches:
        print("no exported patches to report on.")
        return 0

    print(f"corpus {corpus_root} at {loaded_hash[:16]}\n")
    for patch in patches:
        state = derived_state(
            patch,
            loaded_corpus_hash=loaded_hash,
            asset_text_now=text_now,
            retrieval_ok=_retrieval_ok(patch),
        )
        print(f"{patch.patch_id}  {state.value}")
        print(f"    {_SENTENCE[state]}")
        print(f"    asset {patch.asset_id}.{patch.field_path}")
        observations = store.observations_of(patch.patch_id)
        for observation in observations:
            cited = observation.observation_id in refs
            print(
                f"    obs {observation.observation_id}  "
                f"source_refs: {'cited by ' + refs[observation.observation_id] if cited else 'not cited'}"
            )
        print()

    dangling = sorted(set(refs) - {o.observation_id for p in patches for o in store.observations_of(p.patch_id)})
    if dangling:
        print(
            f"{len(dangling)} `obs:` reference(s) in the corpus name observations this store has "
            f"never heard of: {dangling[:5]}"
        )
        print(
            "    Reported and not treated as a landing. An unvalidated string is a claim, and a "
            "claim about a row that does not exist is the one case where believing it invents a "
            "state out of nothing."
        )
    return 0


def _verify(
    store: FeedbackStore, corpus_root: Path, *, patch_id: str | None, bundle: str | None
) -> int:
    """Whether a bundle still applies. Three answers, and the middle one is the useful one."""
    resolved = patch_id or (_patch_id_of(Path(bundle)) if bundle else None)
    if not resolved:
        print("--verify needs --patch or --bundle", file=sys.stderr)
        return 2
    patch = store.get_patch(resolved)
    if patch is None:
        print(f"no patch {resolved!r}", file=sys.stderr)
        return 2

    loaded_hash = corpus_content_hash(corpus_root)
    target = _file_declaring(corpus_root, str(patch.asset_id), str(patch.field_path))
    if target is None:
        print(f"{resolved}: the asset {patch.asset_id} is no longer in the corpus. Back to the steward.")
        return 1

    current = read_field(target, asset_id=str(patch.asset_id), field_path=str(patch.field_path))
    if current == patch.becomes:
        print(f"{resolved}: already applied -- the field already holds what the bundle sets it to.")
        return 0
    if current != patch.was:
        print(
            f"{resolved}: **the field changed under this bundle.** It was authored against\n"
            f"    {patch.was[:100]!r}\n"
            f"  and now holds\n"
            f"    {current[:100]!r}\n"
            f"  Back to the steward: re-reading and re-drafting is the only correct move, and "
            f"`apply_edit` would refuse anyway."
        )
        return 1

    try:
        apply_edit(
            target,
            asset_id=str(patch.asset_id),
            field_path=str(patch.field_path),
            was=str(patch.was),
            becomes=str(patch.becomes),
        )
    except (StaleValue, FieldNotLocatable, UnwritableValue) as exc:
        print(f"{resolved}: will not apply -- {exc}")
        return 1

    if loaded_hash == patch.base_corpus_content_hash:
        print(f"{resolved}: applies cleanly, and the corpus has not moved since it was drafted.")
    else:
        print(
            f"{resolved}: the corpus moved ({patch.base_corpus_content_hash[:16]} -> "
            f"{loaded_hash[:16]}) but this field is untouched. Re-export and go -- the diff's "
            f"context lines are what a stale base breaks, not the edit itself."
        )
    return 0


# ── plumbing ─────────────────────────────────────────────────────────────────


def _exported(store: FeedbackStore) -> list:
    """Every patch a bundle was produced for. The only population "did it land" is about."""
    out = []
    for observation in store.queue(limit=10_000).rows:
        for patch in store.patches_of(observation.observation_id):
            if patch.state in (PatchState.exported, PatchState.draft) and patch not in out:
                out.append(patch)
    return out


def _retrieval_ok(patch: Any) -> bool | None:
    """The patch's T3 verdict, or ``None`` when nothing ran it.

    **``None`` and ``False`` are different answers and both leave the landing unchanged.** ``None``
    is "nobody asked"; ``False`` is "asked, and the question still fails". Collapsing them would let
    an unrun check read as a failed one, which sends a landed change back to the steward.

    `retrieval_verified` was unreachable until this existed: `derived_state` has taken
    `retrieval_ok` since it was written and every caller passed `None`, so the state was declared
    and nothing could compute it. The value comes from `tools/reproduce_observation.py --record`.
    """
    tier = dict(getattr(patch, "ladder", {}) or {}).get("T3")
    if not isinstance(tier, dict) or "passed" not in tier:
        return None
    return bool(tier["passed"])


def _source_refs(assets: list) -> dict[str, str]:
    """``observation_id`` -> the asset citing it, from ``Provenance.source_refs``.

    The receipt lives in the content: an observation cannot be called addressed by anything other
    than the change actually being there, and this is how the engine notices without being told.
    """
    out: dict[str, str] = {}
    for asset in assets:
        provenance = getattr(getattr(asset, "audit", None), "provenance", None)
        for ref in getattr(provenance, "source_refs", ()) or ():
            text = str(ref)
            if text.startswith("obs:"):
                out[text[len("obs:") :]] = str(getattr(asset, "id", "?"))
    return out


def _file_declaring(corpus_root: Path, asset_id: str, field_path: str) -> Path | None:
    for candidate in sorted(corpus_root.rglob("*.yaml")):
        if ".git" in candidate.parts:
            continue
        try:
            read_field(candidate, asset_id=asset_id, field_path=field_path)
        except FieldNotLocatable:
            continue
        return candidate
    return None


def _patch_id_of(bundle: Path) -> str | None:
    name = bundle.name
    return name[len("bnd-") :] if name.startswith("bnd-") else None


def _corpus_root(explicit: str | None) -> Path:
    """``--corpus-dir``, the environment, then ``.env`` -- through the one shared answer.

    This asked ``os.environ.get`` and raised ``SystemExit`` with a message, which exits **1**,
    and in this tool 1 is "a patch did not land as claimed". So forgetting the flag reported a verdict the tool never
    formed. It also could not see ``.env``, which is where this repository keeps
    ``GOVERNED_BI_CORPUS_DIR``.
    """
    return corpus_target.resolve_corpus_dir(explicit)


def _resolve(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (REPO_ROOT / path)


def main(argv: list[str] | None = None) -> int:
    """The entry point, and the only place a code is chosen.

    In this tool 1 is a verdict about the patch, and forgetting ``--corpus-dir`` is not a
    verdict. ``corpus_target.Misconfigured`` is a ``RuntimeError``, so without this it escapes as
    a traceback and still exits 1 -- the same defect wearing a worse face.
    """
    try:
        return _main(argv)
    except corpus_target.Misconfigured as err:
        print(str(err), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
