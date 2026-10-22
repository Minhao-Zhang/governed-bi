"""A copied McNemar line leaves the quotability flag behind, so the line itself says when it is not quotable."""
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
    """The check passing must not decorate the line. Asserted first, so the marker means one
    thing: a check said no."""
    a, b = _pair()
    result = mcnemar(a, b, "correct")

    assert _rendered_mcnemar(result, quotable=True) == result.render()
    assert MARKER not in _rendered_mcnemar(result, quotable=True)


def test_a_non_quotable_delta_carries_the_result_in_the_string() -> None:
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
    """The shipped pair is non-quotable and its delta is null; both have to travel with the number."""
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
    configs = {
        config: [
            json.loads(line)
            for line in (root / f).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        for config, f in names.items()
    }
    comparison = summarise(configs, pair=("v3_fold", "v4"))["comparison"]

    assert comparison["quotable"] is False
    assert MARKER in comparison["mcnemar"], (
        "the one pair configs.toml declares renders as a bare delta while a check says it cannot "
        "be quoted"
    )
