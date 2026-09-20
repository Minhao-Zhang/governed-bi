"""A rendered comparison carries its own quotability, because a copied line leaves the flag.

``report.py`` renders the paired McNemar whenever the population is non-empty, gate or no
gate, and that is deliberate — the comment beside it says computing it for diagnostics
whatever ``quotable`` says is that line's job, and it is right. ``summary["comparison"]
["quotable"]`` carries the verdict one key away.

One key away is the problem. The rendered line is the thing a person copies into a message,
a commit or a doc, and the boolean does not travel with it. **Every real pair in
``runs/eval/`` is non-quotable today** — ``knobs_comparable`` returns ``cannot_evaluate``
because ~50 of the 51 comparability knobs are absent from those rows — so every line that
function has ever produced was a diagnostic that read exactly like a result:

    v4 - v3_fold: 0.0118 (p=0.1812, n=1351, discordant=126, MDE=0.0233)

The gate was not removed and the render was not suppressed. Only the string changed.

``McNemarResult.render`` itself is untouched: it is a statement about the statistic, and a
gate verdict is not part of the arithmetic. The annotation is added where the gate's answer
is known.
"""

from __future__ import annotations

from governed_bi.eval.report import _rendered_mcnemar
from governed_bi.measure.population import Population
from governed_bi.measure.stats import mcnemar

MARKER = "[NOT QUOTABLE"


def _pair() -> tuple[Population, Population]:
    return (
        Population.of("a", [{"question_id": str(i), "correct": i % 3 == 0} for i in range(30)]),
        Population.of("b", [{"question_id": str(i), "correct": i % 2 == 0} for i in range(30)]),
    )


def test_a_quotable_delta_renders_unchanged() -> None:
    """The gate passing must not decorate the line. Asserted first, so the marker means one
    thing: a gate said no."""
    a, b = _pair()
    result = mcnemar(a, b, "correct")

    assert _rendered_mcnemar(result, quotable=True) == result.render()
    assert MARKER not in _rendered_mcnemar(result, quotable=True)


def test_a_non_quotable_delta_carries_the_verdict_in_the_string() -> None:
    """The line has to survive being copied out of the report it came from."""
    a, b = _pair()
    result = mcnemar(a, b, "correct")

    rendered = _rendered_mcnemar(result, quotable=False)
    assert rendered.startswith(result.render()), (
        "the statistic itself must still be readable; the marker is an addition, not a "
        "replacement"
    )
    assert MARKER in rendered


def test_the_real_pair_on_disk_is_marked() -> None:
    """The case this exists for, through ``summarise`` rather than the helper.

    ``arms.toml`` declares ``v4.compare_to = "v3_fold"``, so this pair is computed on every
    report. It is non-quotable and its delta is null (p=0.18) — the two facts a reader needs
    together, and until now only one of them travelled with the number.
    """
    import json
    import pathlib

    from governed_bi.eval.report import summarise

    root = pathlib.Path("runs/eval")
    names = {
        "v3_fold": "proxy_v3_fold_opus_high_corpus30872d3.jsonl",
        "v4": "proxy_v4_corpus30872d3.jsonl",
    }
    if not all((root / f).exists() for f in names.values()):
        import pytest

        pytest.skip("the proxy artifacts are not on this machine; runs/ is local-only")

    # `read_text` and not `open(...)` in a comprehension: `pyproject.toml` turns
    # `ResourceWarning` into an error, after 131 of them traced to one unclosed-handle bug.
    arms = {
        arm: [
            json.loads(line)
            for line in (root / f).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        for arm, f in names.items()
    }
    comparison = summarise(arms, pair=("v3_fold", "v4"))["comparison"]

    assert comparison["quotable"] is False
    assert MARKER in comparison["mcnemar"], (
        "the one pair arms.toml declares renders as a bare delta while a gate says it cannot "
        "be quoted"
    )
