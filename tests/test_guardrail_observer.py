"""The guardrail layer observer must never influence a verdict, and never go quiet.

Two properties, both load-bearing for different reasons. The observer sits inside the
safety-critical ``check()`` path, so a raising counter must not be able to turn a
governed answer into an error — that is why the exception is swallowed. But a counter
that raises on every query would leave the layer histogram permanently empty while
every run looked healthy, which is the exact failure shape the instrumentation was
added to end — that is why it must still warn.
"""

from __future__ import annotations

import logging

import pytest

from governed_bi import gateway as gateway_pkg
from governed_bi.gateway import GuardrailLayer, check
from governed_bi.gateway import guardrails as gr

GOOD = 'SELECT "a" FROM "s"."t"'
ALLOWED_COLUMNS = frozenset({"s.t.a"})
ALLOWED_TABLES = frozenset({"s.t"})


@pytest.fixture(autouse=True)
def _reset_warn_once():
    # The warn-once latch is process-global by design; tests must not inherit it.
    gr._observer_failed = False
    yield
    gr._observer_failed = False


def _check(sql=GOOD, **kw):
    return check(
        sql,
        allowed_columns=ALLOWED_COLUMNS,
        allowed_tables=ALLOWED_TABLES,
        hard_block_suspect=False,
        default_schema="s",
        **kw,
    )


def test_a_passing_query_reports_every_layer_that_ran():
    seen: list[tuple[str, bool]] = []
    verdict = _check(on_layer=lambda layer, passed: seen.append((layer.value, passed)))
    assert verdict.passed
    # L4 only runs when the caller supplies a table scope, which it did here.
    assert [s for s, _ in seen] == [
        GuardrailLayer.syntax.value,
        GuardrailLayer.policy_blacklist.value,
        GuardrailLayer.ast_column_allowlist.value,
        GuardrailLayer.term_semantics.value,
        GuardrailLayer.cost_estimate.value,
    ]
    assert all(passed for _, passed in seen)


def test_layers_after_the_failure_never_report():
    # A layer with no report did not run — a different fact from one that blocked
    # nothing, and the distinction is what makes the histogram readable.
    seen: list[tuple[str, bool]] = []
    verdict = _check(
        sql="DELETE FROM t", on_layer=lambda layer, passed: seen.append((layer.value, passed))
    )
    assert not verdict.passed
    assert seen[-1][1] is False
    assert GuardrailLayer.cost_estimate.value not in [s for s, _ in seen]


def test_an_observer_that_raises_cannot_change_the_verdict():
    def boom(layer, passed):
        raise RuntimeError("counter is broken")

    verdict = _check(on_layer=boom)
    assert verdict.passed, "a broken counter must not fail a legitimate query closed"


def test_a_raising_observer_does_not_mask_a_real_block():
    def boom(layer, passed):
        raise RuntimeError("counter is broken")

    verdict = _check(sql="DROP TABLE t", on_layer=boom)
    assert not verdict.passed, "a broken counter must not open the gate either"


def test_a_raising_observer_warns_once_not_silently(caplog):
    def boom(layer, passed):
        raise RuntimeError("counter is broken")

    with caplog.at_level(logging.WARNING, logger="governed_bi.gateway"):
        _check(on_layer=boom)
        _check(on_layer=boom)

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1, "expected exactly one warning across many layer calls"
    assert "layer counts will be incomplete" in warnings[0].message
    assert warnings[0].exc_info is not None, "the traceback is what makes it actionable"


def test_no_observer_means_no_behaviour_change():
    # The default path must be untouched: this is what lets the observer ship without
    # re-validating every guardrail test.
    assert _check().passed
    assert not _check(sql="DELETE FROM t").passed
