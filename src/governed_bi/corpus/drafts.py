"""Submit a model-authored candidate fact to the corpus, and let a human certify it.

**Why this exists, and why it is not upstream.** v2 deletes the HTTP corpus-write surface
(ADR 0005 §1.6: "the corpus is trusted, the incoming question is not") and has no ``curator/``
layer yet — see ``utku-ai-v2-porting-spec.md``. UtkuAI's mistake-memory and Enhancer features
both need *some* write path, so this module builds the minimal safe one, reusing v2's own
security-critical primitives rather than reimplementing them:

* :func:`~governed_bi.corpus.provenance.restamp_model_authored` strips any forged
  ``governance``/certified ``audit`` and stamps the write ``proposed`` — code, not a prompt
  instruction.
* :func:`~governed_bi.corpus.store.write` validates the path component and the asset id and
  raises on anything unsafe.
* :func:`~governed_bi.corpus.analyst.for_analyst` (patched alongside this module) is what
  keeps a ``proposed`` asset out of live retrieval until :func:`approve_draft` runs.

Nothing here re-derives any of those three guarantees.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping, TypeVar

import yaml

from .identity import corpus_files
from .parse import to_mapping
from .provenance import restamp_model_authored
from .schema import Asset, Governance, ProvenanceStatus
from .store import SUFFIX, load_file, write
from .validate import problems_with

__all__ = [
    "submit_draft",
    "approve_draft",
    "resolve_conflict",
    "DraftNotFound",
    "DraftNotPending",
    "ConflictNotFound",
    "ConflictAlreadyResolved",
]

A = TypeVar("A", bound=Asset)


class DraftNotFound(LookupError):
    """No asset with this id exists anywhere under the corpus root."""


class DraftNotPending(ValueError):
    """The asset exists but its provenance status is not ``proposed`` (already certified,
    or was never a model-authored candidate — e.g. a seeded asset with no audit trail)."""


class ConflictNotFound(LookupError):
    """No asset with this id carries ``audit.extra["conflict_with"]`` — either the id names
    no asset at all, or it names one that was never flagged as a conflict."""


class ConflictAlreadyResolved(ValueError):
    """This candidate's ``audit.extra`` already carries a ``conflict_resolution``. v1's
    behaviour for a second resolve call is preserved: an error, not a silent no-op."""


def submit_draft(
    root: Path | str,
    asset: A,
    *,
    namespace: str | None = None,
    model: str | None = None,
    extra: Mapping[str, Any] | None = None,
) -> Path:
    """Restamp ``asset`` as a ``proposed`` model-authored candidate and write it.

    Thin composition, deliberately: :func:`restamp_model_authored` and :func:`write` already
    carry the guarantees this needs, so this function adds none of its own. ``namespace`` is
    forwarded unchanged — required for the asset types that declare no ``schema`` field
    (``JoinAsset``, ``MetricAsset``, ``TermAsset``); see :func:`~governed_bi.corpus.store.write`.

    ``extra`` is merged into ``audit.extra`` **after** restamping — restamp rebuilds ``audit``
    from scratch, so this is the one hook for a caller (``curator/enhancer.py``'s conflict flag)
    to attach a reason without it being silently dropped. It is data, not a governance field:
    it cannot set ``excluded`` or a provenance status, both of which stay code-controlled.
    """
    restamped = restamp_model_authored(asset, model=model)
    if extra:
        restamped = replace(restamped, audit=replace(restamped.audit, extra={**restamped.audit.extra, **extra}))
    return write(root, restamped, namespace=namespace)


def _find(root: Path, asset_id: str) -> tuple[Path, Asset]:
    """Linear scan for the file holding ``asset_id``. Not indexed: approval is an admin,
    off-hot-path action, and building an id index for one lookup would be the "flexibility
    nobody asked for" this project's own coding guidelines warn against."""
    for path in corpus_files(root):
        if path.suffix.lower() != SUFFIX:
            continue
        found, problems = load_file(path)
        if problems:
            continue
        for asset in found:
            if asset.id == asset_id:
                return path, asset
    raise DraftNotFound(f"no asset {asset_id!r} under {root}")


def _validated_write(path: Path, asset: Asset) -> Asset:
    """Validate, then persist ``asset`` at its existing ``path``.

    The shared tail of every in-place rewrite this module does — :func:`approve_draft` and
    the two conflict-resolution helpers below — so the validate-then-write order and the YAML
    dump options cannot drift between them the way three independent copies eventually would.
    """
    reasons = problems_with(asset)
    if reasons:
        raise ValueError("; ".join(reasons))
    path.write_text(
        yaml.safe_dump(to_mapping(asset), sort_keys=False, allow_unicode=True), encoding="utf-8"
    )
    return asset


def approve_draft(
    root: Path | str, asset_id: str, *, by: str | None = None, extra: Mapping[str, Any] | None = None
) -> Asset:
    """Flip one ``proposed`` asset to ``certified``, in place, at its existing path.

    Rewrites the same file :func:`submit_draft` created rather than routing back through
    :func:`~governed_bi.corpus.store.write`'s namespace-derivation — the file already exists
    at the right path, and re-deriving its namespace from the asset content is exactly the
    "guessed directory" :func:`~governed_bi.corpus.store.write` itself refuses to do for
    ``JoinAsset``/``MetricAsset``/``TermAsset``.

    ``extra`` merges into ``audit.extra`` the same way ``by``'s ``"approved_by"`` already did —
    generalised so :func:`resolve_conflict`'s ``replace`` path can stamp
    ``conflict_resolution="replaced"`` on certification without a second write. ``by`` is kept
    as its own keyword (not folded into ``extra`` by every caller) because it predates this
    generalisation and every existing call site already passes it positionally-by-name.
    """
    root = Path(root)
    path, asset = _find(root, asset_id)
    provenance = getattr(asset.audit, "provenance", None) if asset.audit is not None else None
    if provenance is None or provenance.status is not ProvenanceStatus.proposed:
        status = provenance.status.value if provenance is not None else None
        raise DraftNotPending(f"asset {asset_id!r} is not a pending draft (status={status!r})")

    certified_provenance = replace(provenance, status=ProvenanceStatus.certified)
    certified = replace(asset, audit=replace(asset.audit, provenance=certified_provenance))
    extra_update: dict[str, Any] = dict(extra) if extra else {}
    if by:
        extra_update["approved_by"] = by
    if extra_update:
        certified = replace(
            certified,
            audit=replace(certified.audit, extra={**certified.audit.extra, **extra_update}),
        )
    return _validated_write(path, certified)


def _find_conflict_candidate(root: Path, asset_id: str) -> tuple[Path, Asset]:
    """``_find`` plus the two conflict-resolution preconditions, so both are checked once.

    Raises :class:`ConflictNotFound` when ``asset_id`` names no asset, or an asset that was
    never flagged (no ``conflict_with``) — same 404 either way, since a caller does not need
    to distinguish "no such id" from "that id is not a conflict". Raises
    :class:`ConflictAlreadyResolved` when a ``conflict_resolution`` is already recorded — v1's
    behaviour for a second resolve call, preserved.
    """
    try:
        path, asset = _find(root, asset_id)
    except DraftNotFound as exc:
        raise ConflictNotFound(str(exc)) from exc
    extra = asset.audit.extra if asset.audit is not None else {}
    if "conflict_with" not in extra:
        raise ConflictNotFound(
            f"asset {asset_id!r} carries no conflict_with; it was never a flagged conflict"
        )
    if "conflict_resolution" in extra:
        raise ConflictAlreadyResolved(
            f"asset {asset_id!r} was already resolved ({extra['conflict_resolution']!r})"
        )
    return path, asset


def _exclude_asset(root: Path, asset_id: str, *, reason: str, by: str | None = None) -> Asset:
    """Set ``governance.excluded=True`` on an arbitrary existing asset by id.

    The "supersede the existing definition" half of ``resolve_conflict``'s ``replace`` path.
    Nothing here deletes the file — exclusion is the same human-only, code-enforced override
    :class:`~governed_bi.corpus.schema.Governance` already carries for any other reason a
    person removes an asset from what the analyst sees.
    """
    path, asset = _find(root, asset_id)
    updated = replace(asset, governance=Governance(excluded=True, reason=reason, by=by))
    return _validated_write(path, updated)


def resolve_conflict(
    root: Path | str, asset_id: str, resolution: str, *, by: str | None = None
) -> tuple[Asset, Asset | None]:
    """Resolve one flagged conflict candidate. Returns ``(candidate, existing_or_None)``.

    ``"keep_existing"``: the candidate stays ``proposed`` forever — never certified, which
    alone keeps it permanently invisible to :func:`~governed_bi.corpus.analyst.for_analyst`,
    so no separate exclusion mechanism is needed. Only
    ``audit.extra["conflict_resolution"] = "kept_existing"`` is written; the asset named by
    ``conflict_with`` is untouched (``existing`` in the return is ``None``).

    ``"replace"``: the candidate is certified via :func:`approve_draft` (reused, not
    reimplemented) with ``conflict_resolution="replaced"`` folded into its ``audit.extra``,
    and the asset its ``conflict_with`` names is excluded via :func:`_exclude_asset` with
    ``reason=f"superseded by {asset_id}"``.

    Raises ``ValueError`` immediately for any ``resolution`` other than the two named above —
    checked before the lookup below, so a bad ``resolution`` reports as a bad request
    regardless of whether ``asset_id`` also happens to be wrong. Raises
    :class:`ConflictNotFound` / :class:`ConflictAlreadyResolved` (see
    :func:`_find_conflict_candidate`) before anything is written.
    """
    if resolution not in ("keep_existing", "replace"):
        raise ValueError(f"resolution={resolution!r} is not one of: keep_existing, replace")
    root = Path(root)
    path, asset = _find_conflict_candidate(root, asset_id)
    if resolution == "keep_existing":
        updated = replace(
            asset,
            audit=replace(
                asset.audit, extra={**asset.audit.extra, "conflict_resolution": "kept_existing"}
            ),
        )
        return _validated_write(path, updated), None
    existing_id = asset.audit.extra["conflict_with"]
    certified = approve_draft(root, asset_id, by=by, extra={"conflict_resolution": "replaced"})
    excluded = _exclude_asset(root, existing_id, reason=f"superseded by {asset_id}", by=by)
    return certified, excluded
