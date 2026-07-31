"""Schema-bump guard: MANIFEST_KNOBS may not change shape under a frozen version.

Pins the exact knob names that shipped with ``MANIFEST_SCHEMA_VERSION == 1``. If a
future edit adds, removes, or renames a knob, the current set stops matching this
frozen snapshot — and the version MUST have moved past 1, or `comparable()`'s
"``None`` on both sides counts as agreement" guarantee silently applies to a
manifest shape the version stamp still claims is the original one.

``_V1_KNOB_NAMES`` is a historical snapshot and must never be updated to match a
later change: doing so would defeat the guard it exists to be.
"""

from governed_bi.eval import metrics

_V1_KNOB_NAMES = frozenset(
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
)


def test_manifest_schema_version_bumps_when_knobs_change():
    current = frozenset(m.name for m in metrics.MANIFEST_KNOBS)
    if current == _V1_KNOB_NAMES:
        assert metrics.MANIFEST_SCHEMA_VERSION >= 1
    else:
        assert metrics.MANIFEST_SCHEMA_VERSION > 1, (
            "MANIFEST_KNOBS diverged from the version-1 snapshot but "
            "MANIFEST_SCHEMA_VERSION did not bump past 1 — comparable() would "
            "keep treating this shape as the presence-guaranteed original"
        )
