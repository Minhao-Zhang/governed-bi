"""``prompt_set_hash`` must cover every prompt the engine sends (``register/prompts.py``).

**The defect this file exists to prevent has a measured precedent.** ``prompt_set_hash`` was
``_digest(SYSTEM_PROMPT)`` — one prompt — and the engine is gaining a guard gate, five facet
rewriters and a narrator. Two runs whose guard prompt differed would then have reported the same
hash, and comparability would have cleared the pair the second run existed to isolate. That is
not hypothetical: ``register/knobs.py`` records two v1 ladders that differed **only** in
``llm_reasoning_effort``, which was recorded nowhere, and effort moved the baseline arm +2.5pp
against a 2.3pp detection threshold.

So the assertion that matters is :func:`test_the_hash_moves_when_any_prompt_moves` — every
registered prompt, one at a time. A registry with a prompt the hash does not reach fails there.
"""

from __future__ import annotations

import pytest

from governed_bi.register import prompts as prompts_mod
from governed_bi.register.prompts import (
    DEFAULT_VARIANTS,
    PROMPT_REGISTRY,
    Prompt,
    prompt_set_hash,
    prompt_text,
    select,
    unknown_prompts,
)
from governed_bi.register.stages import Stage


def test_every_prompt_names_a_declared_stage() -> None:
    """``prompts.py`` holds ``stage`` as a plain string so it need not import ``stages`` — both
    registries must stay importable in a bare interpreter. This is the check that pays for it."""
    declared = {m.value for m in Stage}
    wrong = {n: p.stage for n, p in PROMPT_REGISTRY.items() if p.stage not in declared}
    assert wrong == {}, f"{wrong} name stages that register/stages.py does not declare"


def test_every_prompt_states_why_it_exists() -> None:
    """A prompt whose purpose nobody wrote down is one nobody can write a second variant of,
    and the variants mapping exists because somebody will."""
    for name, prompt in PROMPT_REGISTRY.items():
        assert prompt.why.strip(), f"{name} has no stated purpose"
        assert prompt.default in prompt.variants, f"{name}'s default variant is not declared"
        for variant, text in prompt.variants.items():
            assert text.strip(), f"{name}/{variant} is empty"


def test_the_hash_moves_when_any_prompt_moves(monkeypatch: pytest.MonkeyPatch) -> None:
    """**The load-bearing test.** Every registered prompt, one at a time.

    A prompt the hash does not reach is a treatment the run cannot report, which is the whole
    failure. Parametrising over the live registry rather than a hand-written list is deliberate
    here — unlike the stage vocabulary, where a hand-written list is what catches the *code*
    being wrong, this property is about coverage of whatever happens to be registered.
    """
    baseline = prompt_set_hash()
    for name, prompt in PROMPT_REGISTRY.items():
        edited = Prompt(
            name=prompt.name,
            stage=prompt.stage,
            why=prompt.why,
            variants={**prompt.variants, prompt.default: prompt.variants[prompt.default] + " ."},
            default=prompt.default,
        )
        patched = {**PROMPT_REGISTRY, name: edited}
        monkeypatch.setattr(prompts_mod, "PROMPT_REGISTRY", patched)
        assert prompt_set_hash() != baseline, (
            f"editing {name}'s text left prompt_set_hash unchanged — that prompt is a treatment "
            "the run does not record"
        )
        monkeypatch.undo()


def test_the_hash_covers_the_variant_not_only_the_text(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two variants that happen to be identical today must still be distinguishable.

    Hashing only text would collapse them and then silently diverge, so a run could not say
    which one it asked for. Hashing only names would make an edit to ``v1``'s wording invisible.
    The hash does both.
    """
    twin = Prompt(
        name="analyst",
        stage="agent_core",
        why="two variants, identical text",
        variants={"v1": "same words", "v2": "same words"},
        default="v1",
    )
    monkeypatch.setattr(prompts_mod, "PROMPT_REGISTRY", {"analyst": twin})
    monkeypatch.setattr(prompts_mod, "DEFAULT_VARIANTS", {"analyst": "v1"})
    assert prompt_set_hash() != prompt_set_hash({"analyst": "v2"})


def test_the_hash_is_stable_and_order_independent() -> None:
    """A comparability key that moved when somebody reordered a literal would be useless."""
    assert prompt_set_hash() == prompt_set_hash()
    assert prompt_set_hash() == prompt_set_hash(dict(DEFAULT_VARIANTS))


def test_select_is_total_over_the_registry() -> None:
    """A run that overrides one prompt and a run that overrides none must differ in one entry,
    not in the number of entries."""
    assert set(select()) == set(PROMPT_REGISTRY)
    assert set(select({})) == set(PROMPT_REGISTRY)


def test_an_unknown_prompt_or_variant_raises_at_selection() -> None:
    """Loudly, and at the point of selection — not three stages later when a node asks for text.

    Falling back to the default would make the run report the hash of a prompt it did not send.
    """
    with pytest.raises(KeyError):
        prompt_text("no_such_prompt")
    with pytest.raises(KeyError):
        select({"no_such_prompt": "v1"})
    with pytest.raises(KeyError):
        select({"analyst": "no_such_variant"})


def test_unknown_prompts_reports_instead_of_raising() -> None:
    """The eval driver reads variant selections from a config file, where a typo should name
    itself rather than end the run."""
    assert unknown_prompts({"analyst": "v1"}) == []
    assert unknown_prompts({"analyst": "v1", "narrater": "v1"}) == ["narrater"]
    assert unknown_prompts(None) == []


def test_the_engine_sends_the_registered_text() -> None:
    """One text. A second copy beside the caller is how the hash and the sent prompt drift."""
    from governed_bi.serve.tools import SYSTEM_PROMPT

    assert SYSTEM_PROMPT == prompt_text("analyst")


def test_the_session_reports_the_registry_hash() -> None:
    """``session.py`` computed ``_digest(SYSTEM_PROMPT)``. If it drifts back, this catches it."""
    import inspect

    from governed_bi.serve import session as session_mod

    source = inspect.getsource(session_mod)
    assert "prompt_set_hash=prompt_set_hash()" in source, (
        "the session must publish the registry's hash, not a digest of one prompt"
    )
