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
from .schema import Asset, ProvenanceStatus
from .store import SUFFIX, load_file, write
from .validate import problems_with

__all__ = ["submit_draft", "approve_draft", "DraftNotFound", "DraftNotPending"]

A = TypeVar("A", bound=Asset)


class DraftNotFound(LookupError):
    """No asset with this id exists anywhere under the corpus root."""


class DraftNotPending(ValueError):
    """The asset exists but its provenance status is not ``proposed`` (already certified,
    or was never a model-authored candidate — e.g. a seeded asset with no audit trail)."""


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


def approve_draft(root: Path | str, asset_id: str, *, by: str | None = None) -> Asset:
    """Flip one ``proposed`` asset to ``certified``, in place, at its existing path.

    Rewrites the same file :func:`submit_draft` created rather than routing back through
    :func:`~governed_bi.corpus.store.write`'s namespace-derivation — the file already exists
    at the right path, and re-deriving its namespace from the asset content is exactly the
    "guessed directory" :func:`~governed_bi.corpus.store.write` itself refuses to do for
    ``JoinAsset``/``MetricAsset``/``TermAsset``.
    """
    root = Path(root)
    path, asset = _find(root, asset_id)
    provenance = getattr(asset.audit, "provenance", None) if asset.audit is not None else None
    if provenance is None or provenance.status is not ProvenanceStatus.proposed:
        status = provenance.status.value if provenance is not None else None
        raise DraftNotPending(f"asset {asset_id!r} is not a pending draft (status={status!r})")

    certified_provenance = replace(provenance, status=ProvenanceStatus.certified)
    certified = replace(asset, audit=replace(asset.audit, provenance=certified_provenance))
    if by:
        certified = replace(
            certified,
            audit=replace(certified.audit, extra={**certified.audit.extra, "approved_by": by}),
        )

    reasons = problems_with(certified)
    if reasons:
        raise ValueError("; ".join(reasons))

    path.write_text(
        yaml.safe_dump(to_mapping(certified), sort_keys=False, allow_unicode=True), encoding="utf-8"
    )
    return certified
