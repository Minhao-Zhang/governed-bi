"""Take one corpus's summaries and another's authored fields. Named fields only, never a blanket copy.

**Why this is a tool and not a script.** A partial delivery is the normal case: the first
outsourced rewrite of the semantic layer produced 27 useful ``term.binding`` values and 453 useful
``table.grain`` values inside a corpus whose 713 rewritten summaries measured null, so the whole
delivery was neither acceptable nor discardable. Salvaging it by hand is how the delivery itself
went wrong -- ``_nuclear_dense_plus_prefix.py`` copied "everything except grain" and silently
reverted every authored summary in the process, because a copy defined by what it *excludes*
cannot be read for what it *includes*.

So this copies an explicit allowlist of field paths and reports a count per field. Anything not
named stays as the base corpus has it. There is no ``--all``.

``summary`` is refused outright. It is the only indexed text, so grafting it is not a field copy
but a corpus swap wearing one -- and that is exactly the operation that needs a measurement rather
than a flag.
"""

from __future__ import annotations

import argparse
import collections
import pathlib
import shutil
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

import yaml  # noqa: E402

from governed_bi.corpus.store import _LOADER, load  # noqa: E402

#: Field paths this tool will graft, as ``asset_type.field``. Adding one is a deliberate act.
#:
#: Both entries here are *structured* fields rather than prose: a binding is a mapping to an asset
#: id that either resolves or does not, and a grain is a one-line statement of what a row is.
#: Neither can be evaluated by a retrieval metric, and neither is indexed -- which is why they were
#: worth keeping from a delivery whose indexed text was not.
GRAFTABLE: frozenset[str] = frozenset({"term.binding", "table.grain"})

#: Never graftable, with the reason attached so the refusal is readable at the call site.
REFUSED: dict[str, str] = {
    "summary": (
        "summary is the only indexed text (ADR 0005 I1), so copying it between corpora is a "
        "corpus swap and not a field graft. Point --base at the corpus whose summaries you want"
    ),
    "governance": (
        "governance.excluded is human-only and there is no tool that writes it (ADR 0005 §1.5)"
    ),
    "reliability": (
        "a decoy caveat is evidence-based and belongs to the corpus that introspected the decoys; "
        "a softened warning is worse than none"
    ),
    "physical_name": "a physical name is the live identifier SQL emits, not an authored field",
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="graft_corpus_fields", description=__doc__)
    parser.add_argument("--base", required=True, help="the corpus whose summaries are kept")
    parser.add_argument("--donor", required=True, help="the corpus whose named fields are taken")
    parser.add_argument("--out", required=True)
    parser.add_argument(
        "--field",
        action="append",
        default=None,
        metavar="ASSET_TYPE.FIELD",
        help=f"repeatable. Default: every entry of {sorted(GRAFTABLE)}",
    )
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)

    fields = frozenset(args.field or GRAFTABLE)
    for path in sorted(fields):
        head, _, tail = path.partition(".")
        if tail in REFUSED:
            print(f"refusing --field {path}: {REFUSED[tail]}", file=sys.stderr)
            return 2
        if path not in GRAFTABLE:
            print(
                f"refusing --field {path}: not in GRAFTABLE. Add it to the allowlist in this "
                "file, with the reason, rather than passing it through",
                file=sys.stderr,
            )
            return 2

    base, donor, dest = REPO / args.base, REPO / args.donor, REPO / args.out
    for label, root in (("--base", base), ("--donor", donor)):
        if not root.is_dir():
            print(f"{label} is not a directory: {root}", file=sys.stderr)
            return 2
    if dest.exists():
        if not args.force:
            print(f"{dest} exists; pass --force", file=sys.stderr)
            return 2
        shutil.rmtree(dest)

    # The donor is read through `load`, so a field only transfers if the asset it belongs to
    # actually parsed. A YAML-level copy would happily carry a value off a broken asset.
    donor_assets, donor_problems = load(donor)
    if donor_problems:
        print(f"donor has {len(donor_problems)} load problems; refusing to graft from it")
        for problem in donor_problems[:5]:
            print(f"  {problem}")
        return 1
    donor_by_id = {a.id: a for a in donor_assets}

    shutil.copytree(base, dest)
    grafted: collections.Counter[str] = collections.Counter()
    absent: collections.Counter[str] = collections.Counter()

    for path in sorted(dest.rglob("*.yaml")):
        raw = path.read_text(encoding="utf-8-sig")
        doc = yaml.load(raw, Loader=_LOADER)
        if not isinstance(doc, dict):
            continue
        touched = False
        for entry in sorted(fields):
            kind, _, field = entry.partition(".")
            if doc.get("asset_type") != kind:
                continue
            asset = donor_by_id.get(str(doc.get("id") or ""))
            if asset is None:
                absent[entry] += 1
                continue
            value = getattr(asset, field, None)
            if value is None or value == () or value == "":
                continue
            # Dataclass values (a Binding) round-trip through the parser's own mapping form, so
            # the written YAML is the shape `load` reads back rather than a repr of an object.
            if hasattr(value, "__dataclass_fields__"):
                value = {
                    f: (getattr(getattr(value, f), "value", getattr(value, f)))
                    for f in value.__dataclass_fields__
                }
            if doc.get(field) == value:
                continue
            doc[field] = value
            touched = True
            grafted[entry] += 1
        if touched:
            path.write_text(
                yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=100),
                encoding="utf-8",
            )

    print(f"grafted into {dest.relative_to(REPO)}:")
    for entry in sorted(fields):
        print(f"  {entry:<20} {grafted[entry]:>5} copied, {absent[entry]:>5} donor asset absent")

    assets, problems = load(dest)
    print(f"loads: {len(assets)} assets, {len(problems)} problems")
    for problem in problems[:5]:
        print(f"  {problem}")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
