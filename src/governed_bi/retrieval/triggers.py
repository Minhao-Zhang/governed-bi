"""Keyword trigger PIN for notes (ADR 0003 Phase 4 / M4 R7).

PIN only — never blended into RRF. Cap ≤ ``pin_max``. Regex-over-question is
deferred (keyword triggers only).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..corpus.schemas import NoteAsset, ProvenanceStatus

if TYPE_CHECKING:
    from ..corpus import Corpus
    from ..config import Settings


def fire_triggers(
    corpus: "Corpus",
    question: str,
    *,
    settings: "Settings | None" = None,
    require_certified: bool | None = None,
    pin_max: int = 3,
) -> list[str]:
    """Return note ids whose keyword triggers match ``question`` (capped).

    When ``require_certified`` is True (prod default), only
    ``publication_status=certified`` notes may PIN. Tiebreak: certified first,
    then confidence descending, then id.
    """
    if settings is not None:
        if not settings.pin_triggers_enabled:
            return []
        if require_certified is None:
            require_certified = settings.pin_require_certified
        pin_max = settings.pin_max
    if require_certified is None:
        require_certified = True

    q = question.casefold()
    hits: list[NoteAsset] = []
    for asset in corpus.assets:
        if not isinstance(asset, NoteAsset):
            continue
        if getattr(asset.governance, "excluded", False):
            continue
        pub = asset.publication_status
        pub_v = pub.value if hasattr(pub, "value") else pub
        if require_certified and pub_v != ProvenanceStatus.certified.value:
            continue
        for trig in asset.triggers:
            kind = trig.kind if isinstance(trig.kind, str) else str(trig.kind)
            if kind != "keyword":
                continue  # regex deferred
            if trig.value.casefold() in q:
                hits.append(asset)
                break

    def _key(n: NoteAsset) -> tuple:
        pub = n.publication_status
        pub_v = pub.value if hasattr(pub, "value") else str(pub)
        rank = 0 if pub_v == "certified" else 1 if pub_v == "draft" else 2
        conf = n.confidence if isinstance(n.confidence, (int, float)) else 0.0
        return (rank, -float(conf), n.id)

    hits.sort(key=_key)
    # Dedupe preserving order
    seen: set[str] = set()
    out: list[str] = []
    for n in hits:
        if n.id in seen:
            continue
        seen.add(n.id)
        out.append(n.id)
        if len(out) >= pin_max:
            break
    return out
