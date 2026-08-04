"""The corpus on disk: YAML in, typed assets out, one bad item at a time.

**The contract that shapes every line of this file:** :func:`load` returns
``(assets, problems)`` and **never raises for a bad item.** v1's loader raised on
the first unparseable file, and because the pooled driver loads every schema of
every arm through one call inside a ``try/finally`` with no ``except``, **one
truncated YAML discarded a fully paid 69-schema build with no clue why.**

The opposite failure is equally real and this project has already published a result
on top of it: a *silent* skip turns "a corpus that lost half its assets" into "a
corpus that merely looks small". So both halves of the tuple are load-bearing, and
a problem a reader cannot act on is a silent skip with extra steps -- every
:class:`~.validate.Problem` names the file and the rule.

**One file, at most one problem.** A file that breaks three rules reports one entry
listing all three, rather than three entries. A caller counting problems is counting
*items it lost*, and that is the number worth having.

Three environment facts this module exists to absorb, all from
``docs/lessons-from-v1.md`` Appendix B:

* **YAML 1.1 resolves ``on:`` as boolean ``True``** -- and ``JoinAsset`` has a field
  named ``on``. The loader below removes the ``y/n/yes/no/on/off`` bool spellings
  from the resolver, so ``on`` stays a string key. Nothing in the asset schema wants
  YAML 1.1's bool aliases, and losing them costs nothing.
* **83 of 597 v1 description CSVs began with a BOM**, which lands inside the first
  key and silently empties a whole file. Read with ``utf-8-sig``.
* **``UnicodeDecodeError`` is a ``ValueError``, not an ``OSError``.** A file saved as
  cp1252 does not raise anything an ``except OSError`` would catch, which is how a
  non-UTF-8 file took down an import in v1. The per-file guard here catches
  ``Exception`` for exactly that reason.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Sequence

import yaml

from .identity import (
    corpus_files,
    derive_column_id,
    validate_asset_id,
    validate_path_component,
)
from .parse import from_mapping, to_mapping
from .schema import Asset, SchemaAsset, TableAsset, class_for
from .validate import Problem, problems_with

__all__ = ["SUFFIX", "load", "load_file", "write"]

#: The one suffix a corpus asset file has.
SUFFIX = ".yaml"

#: Suffixes :func:`load` looks at. ``.yml`` is included **so that it can be
#: reported**: it is the one near-miss that is almost certainly a typo rather than a
#: deliberately different file, and a file the loader ignores in silence is an asset
#: the corpus lost. Everything else under the tree -- the markdown D9 keeps beside
#: the YAML -- is not an asset file and is not reported as a missing one.
_LOOKED_AT: tuple[str, ...] = (SUFFIX, ".yml")


def _loader_class() -> type:
    """A YAML loader with YAML 1.1's bool aliases removed.

    ``on``, ``off``, ``yes``, ``no``, ``y`` and ``n`` stay strings. ``JoinAsset.on``
    is why: under the default resolver a mapping key ``on:`` arrives as ``True``,
    the field looks absent, and the ON clause -- which ADR 0005 §1.2 makes part of a
    join's identity -- vanishes without an error.

    ``CSafeLoader`` when available: it is roughly 7x faster and YAML parsing was
    measured at ~23% of an offline run's wall clock. It shares the Python resolver,
    so the fix applies to both.
    """
    try:
        base: type = yaml.CSafeLoader  # type: ignore[attr-defined]
    except AttributeError:  # pragma: no cover - depends on the libyaml build
        base = yaml.SafeLoader

    loader = type("_CorpusLoader", (base,), {})
    bool_tag = "tag:yaml.org,2002:bool"
    loader.yaml_implicit_resolvers = {  # type: ignore[attr-defined]
        first: [(tag, rx) for tag, rx in mappings if tag != bool_tag]
        for first, mappings in base.yaml_implicit_resolvers.items()  # type: ignore[attr-defined]
    }
    loader.add_implicit_resolver(  # type: ignore[attr-defined]
        bool_tag,
        re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"),
        list("tTfF"),
    )
    return loader


_LOADER = _loader_class()


def load(
    root: Path | str, *, schemas: Sequence[str] | None = None
) -> tuple[list[Asset], list[Problem]]:
    """Every asset under ``root``, plus one problem per item that could not load.

    ``schemas`` is the **manifest**. Given one, only those subtrees (plus
    ``_shared``) are read and a manifest entry with no directory is reported -- v1's
    shared corpus root was a cross-run contamination channel, because a schema
    dropped from one attempt left its YAML behind and then competed as a router
    candidate for *every other schema's questions*, silently changing the routing
    problem's difficulty between two runs of the same set.

    ``schemas=None`` means **the tree is the manifest**, which is correct for a
    single-schema tree and for a test, and is the un-guarded form for a pooled root.

    Returns assets in sorted-path order. An empty directory yields ``([], [])``:
    zero assets from an empty tree is a correct answer, and it is the manifest --
    not a per-item problem -- that turns "the corpus lost half its assets" back into
    something a reader can see.
    """
    base = Path(root)
    problems: list[Problem] = []
    if not base.is_dir():
        return [], [Problem(str(base), "the corpus root does not exist or is not a directory")]

    if schemas is not None:
        for name in schemas:
            if not (base / name).is_dir():
                problems.append(
                    Problem(
                        str(base / name),
                        f"the manifest names schema {name!r} and there is no directory for "
                        "it, so every asset of that schema is missing from this load",
                    )
                )

    assets: list[Asset] = []
    for path in corpus_files(base, schemas=schemas, suffixes=_LOOKED_AT):
        if path.suffix.lower() != SUFFIX:
            problems.append(
                Problem(
                    path.relative_to(base).as_posix(),
                    f"has suffix {path.suffix!r} and was not loaded; asset files are "
                    f"{SUFFIX}. Reported rather than skipped: a file the loader ignores "
                    "in silence is an asset the corpus lost",
                )
            )
            continue
        found, trouble = load_file(path, where=path.relative_to(base).as_posix())
        assets.extend(found)
        problems.extend(trouble)
    return assets, problems


def load_file(path: Path, *, where: str | None = None) -> tuple[list[Asset], list[Problem]]:
    """One file. Never raises.

    ``where`` overrides the label in a problem, so a caller can report a path
    relative to the corpus root rather than an absolute one that differs per machine
    -- and a digest or a diff of the reported problems stays comparable between runs.
    """
    label = where or str(path)
    try:
        text = path.read_text(encoding="utf-8-sig")
        document = yaml.load(text, Loader=_LOADER)  # noqa: S506 - _LOADER derives from SafeLoader
    except Exception as err:  # UnicodeDecodeError is a ValueError; YAML errors are their own
        return [], [Problem(label, f"could not be read as YAML: {type(err).__name__}: {err}")]

    if document is None:
        return [], [Problem(label, "is empty. An empty asset file is a truncated write, not an asset")]

    raw_items = document if isinstance(document, list) else [document]
    assets: list[Asset] = []
    problems: list[Problem] = []
    for index, raw in enumerate(raw_items):
        position = label if len(raw_items) == 1 else f"{label}[{index}]"
        found, reasons = _one(raw, where=position)
        assets.extend(found)
        if reasons:
            problems.append(Problem(position, "; ".join(reasons)))
    return assets, problems


def _one(raw: Any, *, where: str) -> tuple[list[Asset], list[str]]:
    """One raw mapping into its asset, plus any inline columns.

    Order matters: **construct, then validate.** Two steps, not three. There was a
    sanitize step ahead of both; it is gone, and ADR 0005 §1.6 is where the reasoning
    lives. In short: the corpus is trusted, the incoming question is not, so injection
    is checked once at the analyst's input by ``govern.guard`` rather than by editing
    what a data engineer wrote. Deleting it also closed a defect -- sanitizing on
    ``load`` changed what reached the model while ``corpus_content_hash``, computed
    over the files on disk, did not move.

    Validation therefore now checks exactly the text that enters the index: the
    250-character bound is a bound on the value as written, with nothing in between
    rewriting it.
    """
    if not isinstance(raw, Mapping):
        return [], [f"expected a mapping, got {type(raw).__name__}"]
    if "asset_type" not in raw:
        return [], ["no asset_type: the discriminator decides which of the eight this is"]

    try:
        class_for(raw["asset_type"])  # loud on an unknown type before anything else runs
        parent, columns, reasons = _split_inline_columns(dict(raw))
        if reasons:
            return [], reasons
        built = [from_mapping(item) for item in [parent, *columns]]
    except Exception as err:
        return [], [f"{type(err).__name__}: {err}"]

    broken = [reason for asset in built for reason in problems_with(asset)]
    if broken:
        return [], broken
    for asset in built:
        try:
            validate_asset_id(asset.id)
        except Exception as err:
            return [], [str(err)]
    return built, []


def _split_inline_columns(
    raw: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
    """A table's inline columns into their own raw mappings, with derived ids.

    On disk a column lives inline under its table; in memory it is its own asset
    with its own index entry, because v1 concatenated a table and all its columns
    into one document and columns were never ranked at all. ``columns`` on the
    parent becomes the derived ids, so a column has exactly one representation.

    **Identity is derived, never read.** ADR 0005 §1.2: columns carry no ``id`` in
    YAML. A file that supplies one anyway, or a ``parent_table``/``schema`` that
    disagrees with the table it is stored under, is a problem rather than an
    override -- two spellings of one fact is the shape this package exists to
    avoid.
    """
    if raw.get("asset_type") != "table" or "columns" not in raw:
        return raw, [], []
    inline = raw.get("columns")
    if not isinstance(inline, list) or not all(isinstance(c, Mapping) for c in inline):
        return raw, [], []  # already a list of ids; let the coercion judge it

    table_id, schema = raw.get("id"), raw.get("schema")
    physical = raw.get("physical_name")
    if not isinstance(table_id, str) or not isinstance(physical, str):
        return raw, [], ["a table with inline columns needs its own id and physical_name first"]

    columns: list[dict[str, Any]] = []
    reasons: list[str] = []
    for entry in inline:
        column = dict(entry)
        name = column.get("physical_name")
        if not isinstance(name, str) or not name:
            reasons.append("an inline column has no physical_name, so no id can be derived for it")
            continue
        derived = {
            "asset_type": "column",
            "id": derive_column_id(table_id, name),
            "schema": schema,
            # The table's **id**, not its bare physical name. ADR 0008 D4: a reference
            # field names an asset, exactly, and a bare physical name is only
            # unambiguous inside one schema -- which is the defect that cost 25% of the
            # corpus's joins when 57 schemas were pooled (decision #47). This was the
            # last bare table reference in the asset set, and it survived only because
            # `_bind` is handed the column's own `schema` as a scope.
            "parent_table": table_id,
        }
        clashes = sorted(k for k, v in derived.items() if k in column and column[k] != v)
        if clashes:
            reasons.append(
                f"inline column {name!r} states {clashes} that disagree with the table it "
                "is stored under; a column's identity is derived from its position, and "
                "carrying it twice is two answers to one fact"
            )
            continue
        columns.append({**column, **derived})

    parent = {**raw, "columns": [c["id"] for c in columns]}
    return parent, columns, reasons


def write(root: Path | str, asset: Asset, *, namespace: str | None = None) -> Path:
    """Persist one asset, and **raise** on anything unsafe or invalid.

    Unlike :func:`load` this is not error-isolated, and the asymmetry is the point:
    a bad item on the read path is a degradation the caller must survive, while an
    unsafe path component on the write path is a security control. v1's
    ``asset.schema`` escaped the corpus root because the write directory was derived
    from it while the only validator in the area guarded the asset *id*.

    ``namespace`` is required for the three types that declare no ``schema`` field
    (``join``, ``metric``, ``term``): each one's namespace is a fact held by another
    asset -- a join's is its left endpoint's, a metric's its base table's, a term's
    its binding target's -- and ADR 0005 does not say where such a file lives. So
    this refuses to guess rather than inventing a default that then has to be
    reconciled with the tag rule. Pass
    :data:`~governed_bi.corpus.identity.SHARED_NAMESPACE` for one that really is
    system-wide.
    """
    reasons = problems_with(asset)
    if reasons:
        raise ValueError("; ".join(reasons))

    directory = namespace if namespace is not None else _namespace(asset)
    if directory is None:
        raise ValueError(
            f"{type(asset).__name__} declares no `schema` field, so write() needs an "
            "explicit namespace= rather than a guessed directory"
        )
    validate_path_component(directory, what="namespace")
    validate_asset_id(asset.id)

    target = Path(root) / directory / f"{asset.id}{SUFFIX}"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        yaml.safe_dump(to_mapping(asset), sort_keys=False, allow_unicode=True), encoding="utf-8"
    )
    return target


def _namespace(asset: Asset) -> str | None:
    """The subtree this asset belongs in, or ``None`` when its type declares none.

    Written as two explicit cases rather than "the first string-ish attribute that
    looks like a name". ``MetricAsset`` and ``TermAsset`` both have a ``name``, and
    it is a business name -- a metric called ``revenue`` would land in a directory
    called ``revenue``, which is a plausible-looking wrong answer of exactly the kind
    an attribute sweep produces.
    """
    if isinstance(asset, SchemaAsset):
        return asset.name or None
    value = getattr(asset, "schema", None)
    return value if isinstance(value, str) and value else None


def _assert_column_ids_are_derived_the_same_way() -> None:
    """Import-time guard on the one identity this module computes.

    :func:`_split_inline_columns` and the seed both mint column ids, and if they ever
    disagree the retrieval index, ``resolve``'s closure and the per-type budget all
    key on different strings for one column -- with nothing anywhere raising. Both go
    through :func:`~governed_bi.corpus.identity.derive_column_id`; this asserts the
    shape that function produces is the shape the loader relies on.
    """
    probe = derive_column_id("beer_factory.customers", "email")
    if not probe.startswith("beer_factory.customers") or probe.rsplit(".", 1)[-1] != "email":
        raise AssertionError(  # pragma: no cover - import-time guard
            f"derive_column_id no longer extends the table id with the bare column "
            f"name ({probe!r}); the loader's inline-column expansion assumes it does"
        )
    if "columns" not in {f.name for f in TableAsset.__dataclass_fields__.values()}:
        raise AssertionError(  # pragma: no cover - import-time guard
            "TableAsset has no `columns` field, so inline-column expansion has nowhere "
            "to record the ids it derived and every loaded column would be orphaned"
        )


_assert_column_ids_are_derived_the_same_way()
