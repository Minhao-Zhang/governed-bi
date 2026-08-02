"""Schema-bump guard: MANIFEST_KNOBS must match the snapshot for the declared version.

Each entry in ``_SNAPSHOTS`` is the exact knob-name set that shipped with that
``MANIFEST_SCHEMA_VERSION``. Historical entries (e.g. version 1) must never be
edited to match a later change — that would defeat the guard. A knob add /
remove / rename has only two legal outs:

1. Update the snapshot for the *current* version (reviewer sees it; wrong if
   the change already shipped under that version), or
2. Bump ``MANIFEST_SCHEMA_VERSION`` and add a new snapshot entry.

A ``KeyError`` on the lookup catches "bumped the version but forgot to register
a snapshot." Equality against the versioned snapshot catches "changed knobs and
forgot to bump" for every version, not only v1→v2.
"""

from governed_bi.eval import metrics

# Version → frozen knob names at the moment that version shipped.
# Never rewrite an older entry to match a later shape.
_SNAPSHOTS: dict[int, frozenset[str]] = {
    1: frozenset(
        {
            "split",
            "model",
            "llm_temperature",
            "prompt_variants",
            "prompt_set_hash",
            "corpus_content_hash",
            "question_pool_hash",
            "git_sha",
            "route_top_k",
            "route_llm_pick",
            "schema_pick_max_columns",
            "use_embedder",
            "skip_agent",
            "grade_semantic_failures",
            "always_note_global_max",
            "always_note_char_max",
            "pin_triggers_enabled",
            "pin_require_certified",
            "pin_max",
        }
    ),
    2: frozenset(
        {
            "split",
            "model",
            "llm_temperature",
            "prompt_variants",
            "prompt_set_hash",
            "corpus_content_hash",
            "question_pool_hash",
            "git_sha",
            "route_top_k",
            "route_llm_pick",
            "schema_pick_max_columns",
            "use_embedder",
            "grade_semantic_failures",
            "always_note_global_max",
            "always_note_char_max",
            "pin_triggers_enabled",
            "pin_require_certified",
            "pin_max",
        }
    ),
    3: frozenset(
        {
            "split",
            "model",
            "llm_temperature",
            "llm_reasoning_effort",
            "embedding_model",
            "embedding_dimensions",
            "prompt_variants",
            "prompt_set_hash",
            "corpus_content_hash",
            "question_pool_hash",
            "git_sha",
            "route_top_k",
            "route_llm_pick",
            "schema_pick_max_columns",
            "use_embedder",
            "grade_semantic_failures",
            "always_note_global_max",
            "always_note_char_max",
            "pin_triggers_enabled",
            "pin_require_certified",
            "pin_max",
        }
    ),
    # 4 adds ``question_subset``: the identity of an explicit --questions id list.
    # A knob and not scope because it must gate BETWEEN runs — a probe set chosen for
    # a reason is a biased sample of the split, so its headline and the split's are
    # different quantities and must never be quoted in one sentence.
    4: frozenset(
        {
            "split",
            "model",
            "llm_temperature",
            "llm_reasoning_effort",
            "embedding_model",
            "embedding_dimensions",
            "prompt_variants",
            "prompt_set_hash",
            "corpus_content_hash",
            "question_pool_hash",
            "question_subset",
            "git_sha",
            "route_top_k",
            "route_llm_pick",
            "schema_pick_max_columns",
            "use_embedder",
            "grade_semantic_failures",
            "always_note_global_max",
            "always_note_char_max",
            "pin_triggers_enabled",
            "pin_require_certified",
            "pin_max",
        }
    ),
}


def test_manifest_knob_set_matches_its_declared_version():
    current = frozenset(m.name for m in metrics.MANIFEST_KNOBS)
    version = metrics.MANIFEST_SCHEMA_VERSION
    assert current == _SNAPSHOTS[version], (
        f"MANIFEST_KNOBS diverged from the version-{version} snapshot — "
        f"either bump MANIFEST_SCHEMA_VERSION and register a new snapshot, "
        f"or (if this version has not shipped) update _SNAPSHOTS[{version}]. "
        f"extra={sorted(current - _SNAPSHOTS[version])} "
        f"missing={sorted(_SNAPSHOTS[version] - current)}"
    )
