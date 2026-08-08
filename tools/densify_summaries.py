"""The mechanical floor for a summary rewrite: move `body`'s domain vocabulary into `summary`.

Only ``summary`` is indexed (ADR 0005 I1, ``retrieve/index.py:41``) and the gold layer's domain
prose all sits in ``body``, so the indexed text is an identifier list with a function-word ratio
of 0.00. This drops stopwords and duplicates from ``body`` and puts the leading content words in
front of that list. No model call, no judgement.

Measured through the real graph on 114 questions (2 per schema), embedder on, facet rewriters off:

.. code-block:: text

                                      baseline    dense     delta
    all gold tables licensed  top_n=3    0.632    0.693    +6.1 pp
    all gold tables licensed  top_n=1    0.509    0.588    +7.9 pp
    schema recall@1                      0.632    0.693    +6.1 pp
    mean tables licensed      top_n=3     13.7     13.1       -0.6

+6.1 pp is the acceptance bar for a model-authored rewrite, and keeping this floor runnable is
what makes that bar checkable. Two things it deliberately does not do, both measured: replacing
the identifier list with prose loses on both channels (recall@3 0.851 -> 0.825 embedded, 0.640 ->
0.632 lexical-only), because under obfuscation those English table and column meanings are the
only English in a routing document; and raising the 250-character cap scored worse than this.
Density, not length.
"""

from __future__ import annotations

import argparse
import pathlib
import re
import shutil
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

import yaml  # noqa: E402

from governed_bi.corpus.store import _LOADER, load  # noqa: E402
from governed_bi.register.knobs import knob_default  # noqa: E402

#: Words that cost characters and buy no discrimination. Not a general English stopword list --
#: it also drops the catalogue vocabulary all 57 schemas share (``database``, ``table``,
#: ``record``), since a term fitting twenty schemas cannot separate them and BM25 charges for it.
#:
#: Extended by measuring which extracted words appear in the most schemas, not by taste. Over the
#: 57 gold bodies only four reached 6 or more: ``tracks`` (15), ``catalog`` (11), ``reference``
#: (7), ``sales`` (6). Only ``tracks`` is added, as a verb like ``holds``; the other three are
#: nouns carrying real meaning, and stopping nouns erodes the signal this exists to concentrate.
STOP = frozenset(
    "a an the of and or in on for to with by is are was were be been from as at that this it its "
    "which who whom what when where how many much per each all any not no one row rows table "
    "tables database holds record records carries plus their its also every tracks "
    "about into over such they them these those other both via within across along whose "
    "including includes contain contains containing store stores stored".split()
)


def content_words(text: str, *, limit: int) -> list[str]:
    """Domain terms from prose: order preserved, deduplicated, stopwords and 1-2 char tokens gone.

    Order preserved rather than frequency-sorted: the opening clause is where the curator put the
    discriminating nouns, and a frequency ranking would promote whatever the sentence repeated.
    """
    out: list[str] = []
    seen: set[str] = set()
    for word in re.findall(r"[A-Za-z][A-Za-z0-9_/-]*", text):
        folded = word.lower()
        if folded in STOP or len(folded) < 3 or folded in seen:
            continue
        seen.add(folded)
        out.append(word)
        if len(out) >= limit:
            break
    return out


#: Truncation markers inherited from the source corpus. Gold's own producer (``corpus/seed.py``)
#: builds an identifier list and cuts it with ``[:250]``, so 3 schema and 26 table summaries
#: arrive already truncated — one of them mid-identifier.
_ELLIPSIS = ("…", "...")


def _untruncated(text: str) -> str:
    """The source summary with an inherited truncation marker and its partial term removed.

    ``corpus/validate.py`` forbids truncation. This tool used to carry gold's markers straight
    through, leaving a token that names nothing in the one field the index reads.
    """
    out = text.rstrip()
    for marker in _ELLIPSIS:
        if out.endswith(marker):
            out = out[: -len(marker)].rstrip()
            # The term before the marker is itself a fragment — gold's `Goali…` is not an
            # identifier. Drop back to the last complete comma-separated entry.
            if "," in out:
                out = out.rsplit(",", 1)[0]
            break
    return out.rstrip(" ,;")


def _fit(prefix: str, added: str, tail: str, cap: int, *, joiner: str) -> tuple[str, bool]:
    """``prefix + added + joiner + tail``, shortened by dropping whole tail entries.

    Returns ``(text, trimmed)``. The added vocabulary must survive and the tail gives way: the
    previous ``f"...{nouns}"[:cap]`` composition discarded the nouns for any summary already at
    the cap, silently leaving 26 tables — the widest in the corpus — unchanged. Entries are
    dropped whole, since a mid-word cut puts a fragment in the index.
    """
    entries = [e.strip() for e in tail.split(",") if e.strip()]
    trimmed = False
    while True:
        candidate = f"{prefix}{added}"
        if entries:
            candidate = f"{candidate}{joiner}{', '.join(entries)}"
        if len(candidate) <= cap or not entries:
            return candidate.rstrip(" ,;"), trimmed
        entries.pop()
        trimmed = True


def dense_schema(doc: dict, cap: int) -> tuple[str, bool]:
    """``{name}: <domain terms>. <the table-meaning list that was already there>``."""
    body = str(doc.get("body") or "").strip()
    summary = str(doc["summary"])
    if not body:
        return summary, False
    tail = _untruncated(summary).split(": ", 1)[-1]
    return _fit(f"{doc['name']}: ", " ".join(content_words(body, limit=14)), tail, cap, joiner=". ")


def dense_table(doc: dict, cap: int) -> tuple[str, bool]:
    """The existing summary, then the domain terms. The column-meaning list leads because it is
    the more discriminating half — English glosses of obfuscated identifiers.

    Composed head-first so ``physical_name``, which ``corpus/validate.py`` requires in
    ``summary``, cannot be trimmed away.
    """
    body = str(doc.get("body") or "").strip()
    summary = _untruncated(str(doc["summary"]))
    if not body:
        return summary, False
    head, _, columns = summary.partition(": ")
    nouns = " ".join(content_words(body, limit=10))
    if not columns:
        return f"{summary} — {nouns}"[:cap].rstrip(" ,;"), False
    # The nouns go before the column list so the list is what gives way under the cap.
    return _fit(f"{head}: {nouns}", "", columns, cap, joiner=" — ")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="densify_summaries", description=__doc__)
    parser.add_argument("--source", default="corpora/gold-semantic-layer-20260804")
    parser.add_argument("--out", default="corpora/_variant-dense-20260805")
    parser.add_argument(
        "--force", action="store_true", help="replace --out if it already exists"
    )
    args = parser.parse_args(argv)

    source = REPO / args.source
    dest = REPO / args.out
    if not source.is_dir():
        print(f"no corpus at {source}", file=sys.stderr)
        return 2
    if dest.exists():
        if not args.force:
            print(f"{dest} exists; pass --force to replace it", file=sys.stderr)
            return 2
        shutil.rmtree(dest)

    # The cap is read, never assumed: `corpus/validate.py` enforces it from the same knob, and a
    # second literal here is how this repository acquired two thresholds with different operators.
    cap = int(knob_default("summary_max_chars"))

    # A YAML round-trip of only the affected documents, not `store.write()` per asset: inline
    # columns nest under their table on disk but are separate assets in memory, so writing assets
    # back would flatten 5 947 columns into their own files and change the corpus shape.
    shutil.copytree(source, dest)
    changed = 0
    unchanged: list[str] = []
    trimmed_tails = 0
    for path in sorted(dest.rglob("*.yaml")):
        doc = yaml.load(path.read_text(encoding="utf-8-sig"), Loader=_LOADER)
        if not isinstance(doc, dict) or "summary" not in doc:
            continue
        kind = doc.get("asset_type")
        if kind == "schema":
            new, trimmed = dense_schema(doc, cap)
        elif kind == "table":
            new, trimmed = dense_table(doc, cap)
        else:
            continue
        trimmed_tails += 1 if trimmed else 0
        if new == doc["summary"]:
            # Reported, not skipped: 26 tables previously landed here in silence, so the count
            # said 687 rewritten and nothing said 26 were not.
            unchanged.append(str(path.relative_to(dest).as_posix()))
            continue
        doc["summary"] = new
        path.write_text(
            yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=100), encoding="utf-8"
        )
        changed += 1

    # Loaded before anything measures it. `load()` never raises for a bad item, so a broken file
    # makes the corpus silently *smaller*, which is how this project published a wrong number.
    assets, problems = load(dest)
    print(f"rewrote {changed} summaries into {dest.relative_to(REPO)}")
    print(f"  {trimmed_tails} needed the identifier tail trimmed to fit the {cap}-char cap")
    print(f"  {len(unchanged)} schema/table summaries were left as they were")
    for where in unchanged[:5]:
        print(f"    {where}")
    ellipsis = [
        a.id
        for a in assets
        if getattr(a, "summary", "").rstrip().endswith(("…", "..."))
        and a.asset_type.value in {"schema", "table"}
    ]
    print(f"  {len(ellipsis)} schema/table summaries still end in an ellipsis")
    print(f"loads: {len(assets)} assets, {len(problems)} problems")
    for problem in problems[:5]:
        print(f"  {problem}")
    return 1 if problems or ellipsis else 0


if __name__ == "__main__":
    raise SystemExit(main())
