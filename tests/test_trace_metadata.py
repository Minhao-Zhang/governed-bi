"""What a LangSmith trace can be filtered by.

Until 2026-08-02 every trace of a four-arm ladder carried exactly one tag,
``governed-bi``: ``obs.tracing_config`` built its tags from ``ctx.arm`` /
``ctx.schema`` and ``eval.arms.agent_solver`` never passed either. The arm is the
single most important axis in a ladder, so the traces of a $16-4,065 run were
undifferentiated.

The corpus side was worse than missing. ``corpus_pin`` WAS in the metadata and
reads like a corpus identity, but ``config.py`` documents it as "default corpus
schema subtree / BIRD db_id" and every pooled run carries the literal string
``"datalake"``. The manifest's ``corpus_content_hash`` — which does differ per
corpus and does match across runs that shared one — reached no trace at all.

These tests fail if either stops being attached, at both seams that can drop it:
the metadata builder, and the driver -> solver threading that feeds it.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from governed_bi.config import Environment, Settings
from governed_bi.eval.arms import agent_solver
from governed_bi.obs import RunContext, tracing_config

# --------------------------------------------------------------------------- #
# Seam 1: the metadata builder
# --------------------------------------------------------------------------- #


def test_arm_is_a_tag_and_metadata():
    ctx = RunContext(run_id="r", arm="curated_sme", schema="restaurant")
    cfg = tracing_config(ctx)
    assert "curated_sme" in cfg["tags"], "arm is the ladder's main filter axis"
    assert "restaurant" in cfg["tags"]
    assert "governed-bi" in cfg["tags"]
    assert cfg["metadata"]["arm"] == "curated_sme"
    assert cfg["metadata"]["schema"] == "restaurant"


def test_corpus_content_hash_reaches_the_metadata():
    ctx = RunContext(run_id="r", corpus_pin="datalake", corpus_content_hash="sha256:abc")
    meta = tracing_config(ctx)["metadata"]
    assert meta["corpus_content_hash"] == "sha256:abc", (
        "the treatment's identity; corpus_pin is a mode label and not a substitute"
    )
    assert meta["corpus_pin"] == "datalake"


def test_identity_never_reaches_a_trace():
    ctx = RunContext(run_id="r", identity="alice@example.com")
    cfg = tracing_config(ctx)
    assert "identity" not in cfg["metadata"]
    assert "alice@example.com" not in cfg["tags"]


def test_no_langfuse_keys_remain_in_the_metadata():
    """Langfuse was removed 2026-08-02; LangSmith does not read these and shows
    them as noise in its UI."""
    ctx = RunContext(run_id="r", arm="baseline", schema="restaurant")
    meta = tracing_config(ctx)["metadata"]
    assert not [k for k in meta if k.startswith("langfuse")], meta


def test_unset_fields_are_absent_rather_than_null():
    meta = tracing_config(RunContext(run_id="r"))["metadata"]
    assert list(meta) == ["run_id"]


# --------------------------------------------------------------------------- #
# Seam 2: driver -> solver -> graph.invoke config
# --------------------------------------------------------------------------- #


@pytest.fixture
def invoke_config(monkeypatch):
    """Return the RunnableConfig one ``agent_solver`` question actually invokes with."""

    def _build(**solver_kwargs):
        seen: list[dict] = []
        answer = SimpleNamespace(
            sql="SELECT 1",
            provenance={},
            tier=SimpleNamespace(value="governed"),
            semantic_assurance=SimpleNamespace(value="unflagged"),
            safety_clearance=True,
        )

        def _invoke(state, config=None):
            seen.append(config or {})
            return {"answer": answer}

        monkeypatch.setattr(
            "governed_bi.analyst.agent.build_serve_rails",
            lambda **kwargs: SimpleNamespace(invoke=_invoke),
        )
        solver = agent_solver(
            corpus=None,
            gateway=None,
            settings=Settings.for_env(Environment.dev),
            identity=None,
            model=None,
            **solver_kwargs,
        )
        solver.solve_with_meta("how many customers?")
        assert seen, "the graph was never invoked"
        return seen[0]

    return _build


def test_agent_solver_threads_arm_and_corpus_hash_into_the_invoke_config(invoke_config):
    """The parameters exist to be passed. ``run_datalake`` has both in scope and
    passed neither, which is the whole reason this file exists."""
    cfg = invoke_config(arm="curated", corpus_content_hash="sha256:deadbeef")
    assert "curated" in cfg["tags"]
    assert cfg["metadata"]["arm"] == "curated"
    assert cfg["metadata"]["corpus_content_hash"] == "sha256:deadbeef"


def test_agent_solver_without_them_still_traces_the_run_id(invoke_config):
    cfg = invoke_config()
    assert cfg["metadata"]["run_id"]
    assert "arm" not in cfg["metadata"]


# --------------------------------------------------------------------------- #
# Seam 3: the pooled driver's worker factory, the caller that dropped them
# --------------------------------------------------------------------------- #


class _EchoConn:
    def close(self):
        pass


def _capture_solver_kwargs(monkeypatch):
    from governed_bi.eval import run_datalake as mod

    seen: list[dict] = []
    monkeypatch.setattr(
        mod, "agent_solver", lambda *a, **kw: seen.append(kw) or object()
    )
    monkeypatch.setattr(
        mod,
        "oracle_solver",
        lambda rung, *a, **kw: seen.append({"rung": rung, **kw}) or object(),
    )
    monkeypatch.setattr(mod, "PostgresConnector", lambda dsn, schema=None: _EchoConn())
    monkeypatch.setattr(mod, "Gateway", lambda conn, **kw: object())
    return mod, seen


def test_worker_factory_passes_the_arms_own_corpus_hash(monkeypatch):
    mod, seen = _capture_solver_kwargs(monkeypatch)
    bindings = mod.ServeBindings(
        corpora_serve={"curated": object(), "baseline": object()},
        pg_dsn="postgresql://x/y",
        settings=object(),
        identity=object(),
        model=object(),
        embedder=None,
        gold=object(),
        corpus_hash_by_arm={"curated": "sha256:cur", "baseline": "sha256:base"},
    )
    plan = mod.plan_arm_serving(
        rung=None,
        source_arm="curated",
        oracle_base=None,
        effective_workers=4,
        has_model=True,
    )
    mod.arm_worker_factory(plan, bindings, serve_arm="curated")(0)
    assert seen[0]["arm"] == "curated"
    assert seen[0]["corpus_content_hash"] == "sha256:cur", (
        "the hash must be the served arm's, not the run-wide observed digest"
    )


def test_replicate_keeps_its_own_arm_tag_but_its_sources_corpus_hash(monkeypatch):
    """A replicate serves the source arm's corpus under a different name. The
    corpus hash must describe what was served; the tag must not collapse the two,
    because their disagreement IS the noise measurement."""
    mod, seen = _capture_solver_kwargs(monkeypatch)
    bindings = mod.ServeBindings(
        corpora_serve={"curated": object()},
        pg_dsn="postgresql://x/y",
        settings=object(),
        identity=object(),
        model=object(),
        embedder=None,
        gold=object(),
        corpus_hash_by_arm={"curated": "sha256:cur"},
    )
    plan = mod.plan_arm_serving(
        rung=None,
        source_arm="curated",
        oracle_base=None,
        effective_workers=4,
        has_model=True,
    )
    mod.arm_worker_factory(plan, bindings, serve_arm="curated__replicate")(0)
    assert seen[0]["arm"] == "curated__replicate"
    assert seen[0]["corpus_content_hash"] == "sha256:cur"


def test_oracle_rung_traces_under_its_rung_name(monkeypatch):
    """An untagged oracle trace is indistinguishable from a real serve turn, and
    the rung read the answer key."""
    from governed_bi.eval.oracle import OracleRung

    mod, seen = _capture_solver_kwargs(monkeypatch)
    bindings = mod.ServeBindings(
        corpora_serve={"curated": object()},
        pg_dsn="postgresql://x/y",
        settings=object(),
        identity=object(),
        model=object(),
        embedder=None,
        gold=object(),
        corpus_hash_by_arm={"curated": "sha256:cur"},
    )
    plan = mod.plan_arm_serving(
        rung=OracleRung.schema,
        source_arm="oracle_schema",
        oracle_base="curated",
        effective_workers=4,
        has_model=True,
    )
    mod.arm_worker_factory(plan, bindings, serve_arm="oracle_schema")(0)
    assert seen[0]["arm"] == "oracle_schema"
    assert seen[0]["corpus_content_hash"] == "sha256:cur"
