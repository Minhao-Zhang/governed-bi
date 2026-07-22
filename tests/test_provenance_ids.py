"""Exit-gate tests for M1 / X1 shared provenance foundation."""

from __future__ import annotations

import subprocess
import sys
from dataclasses import replace
from pathlib import Path

from governed_bi.config import DataSourceConfig, Environment, Settings, _repo_root
from governed_bi.provenance import (
    DataSplit,
    Producer,
    corpus_release_hash,
    export_allow,
    new_run_id,
    serve_config_hash,
    turn_id,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_turn_id_matches_graph_app_clarify_thread_formula():
    """Same formula as ``clarify_thread`` in ``api/graph_app.py``."""
    thread_id = "abc-thread"
    n_human = 3
    assert turn_id(thread_id, n_human) == f"{thread_id}:{n_human}"
    assert turn_id(thread_id, n_human) == "abc-thread:3"


def test_new_run_id_unique():
    ids = {new_run_id() for _ in range(50)}
    assert len(ids) == 50
    assert all(len(i) == 32 for i in ids)  # uuid4 hex


def test_serve_config_hash_stable_and_sensitive():
    a = Settings.for_env(Environment.dev)
    b = Settings.for_env(Environment.dev)
    assert serve_config_hash(a) == serve_config_hash(b)

    changed_top_k = replace(a, schema_route_top_k=a.schema_route_top_k + 1)
    assert serve_config_hash(changed_top_k) != serve_config_hash(a)

    changed_gate = replace(a, cache_hit_cosine_gate=0.5)
    assert serve_config_hash(changed_gate) != serve_config_hash(a)

    # Memory / acceptance flags are in the curated set (review feedback #3).
    changed_mem = replace(a, episodic_memory=True)
    assert serve_config_hash(changed_mem) != serve_config_hash(a)
    changed_accept = replace(a, auto_accept_corpus=False)
    assert serve_config_hash(changed_accept) != serve_config_hash(a)

    with_knobs = serve_config_hash(a, routing_knobs={"rrf_weights": [1.0, 0.5]})
    assert with_knobs != serve_config_hash(a)
    assert with_knobs != serve_config_hash(a, routing_knobs={"rrf_weights": [1.0, 1.0]})


def test_serve_config_hash_rejects_non_json_native_knobs():
    a = Settings.for_env(Environment.dev)
    try:
        serve_config_hash(a, routing_knobs={"bad": {1, 2}})
    except TypeError:
        pass
    else:
        raise AssertionError("expected TypeError for set in routing_knobs")


def test_corpus_release_hash_nonempty():
    h = corpus_release_hash()
    assert isinstance(h, str)
    assert len(h) > 0
    # In this repo we expect a real git SHA; tolerate unknown in odd checkouts.
    assert h == "unknown" or len(h) >= 40


def test_corpus_release_hash_unknown_without_git(tmp_path):
    assert corpus_release_hash(repo_root=tmp_path) == "unknown"


def test_corpus_release_hash_never_raises_on_binary_git_head(tmp_path):
    git = tmp_path / ".git"
    git.mkdir()
    (git / "HEAD").write_bytes(b"\xff\xfe\x00 binary ref\x00")
    assert corpus_release_hash(repo_root=tmp_path) == "unknown"


def test_producer_data_split_export_allow():
    assert set(Producer) == {
        Producer.serve,
        Producer.curator,
        Producer.sme,
        Producer.eval,
    }
    assert export_allow(DataSplit.train) is True
    assert export_allow(DataSplit.prod) is True
    assert export_allow(DataSplit.holdout) is False
    # JSON-reloaded plain string must fail closed (not identity check).
    assert export_allow("holdout") is False
    assert export_allow("train") is True


def test_datasource_db_default():
    assert DataSourceConfig().db == "main"
    assert Settings.for_env(Environment.dev).datasource.db == "main"


def test_checkpointer_defaults_on_settings():
    s = Settings.for_env(Environment.dev)
    assert s.conversation_checkpointer_kind == "sqlite"
    assert s.conversation_checkpointer_path == "data/checkpoints/conversations.sqlite"
    assert s.conversation_checkpointer_dsn_env is None


def test_provenance_imports_without_cycle_with_analyst_corpus_curator():
    """provenance must stay importable alongside analyst/corpus/curator."""
    # Fresh interpreter so we don't rely on already-loaded modules in this process.
    script = (
        "import governed_bi.analyst, governed_bi.corpus, governed_bi.curator; "
        "from governed_bi.provenance import turn_id, serve_config_hash; "
        "import governed_bi.provenance as p; "
        "import governed_bi.analyst, governed_bi.corpus, governed_bi.curator; "
        "assert turn_id('t', 1) == 't:1'"
    )
    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout


def test_provenance_import_before_heavy_packages():
    """Reverse order: provenance first, then analyst/corpus/curator."""
    script = (
        "from governed_bi.provenance import Producer, corpus_release_hash; "
        "import governed_bi.analyst, governed_bi.corpus, governed_bi.curator; "
        "assert Producer.serve.value == 'serve'"
    )
    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout


def test_repo_root_helper_aligns():
    # Sanity: corpus_release_hash uses the same root the package resolves.
    assert _repo_root() == REPO_ROOT or (_repo_root() / "pyproject.toml").is_file()
