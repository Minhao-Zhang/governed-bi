"""``build_facts_corpus``: the layer-1 (no-AI) corpus generator.

Profiles the vendored beer_factory SQLite fixture into a facts-only corpus and
confirms the Inference tier is empty (no LLM ran) and the assets round-trip
through ``load_corpus``.
"""

from pathlib import Path

from governed_bi.config import _repo_root
from governed_bi.corpus import load_corpus
from governed_bi.curator import build_facts_corpus
from governed_bi.curator.build import main
from governed_bi.gateway.connectors.sqlite import SqliteConnector

DB = "beer_factory"


def _sqlite_path() -> Path:
    return _repo_root() / "data" / "bird" / "beer_factory.sqlite"


def test_build_facts_writes_facts_only_corpus(tmp_path):
    conn = SqliteConnector(_sqlite_path())
    try:
        written = build_facts_corpus(conn, DB, tmp_path)
    finally:
        conn.close()

    assert written, "expected at least one asset file written"
    assert all(p.suffix == ".yaml" for p in written)
    assert (tmp_path / DB / "tables").is_dir()

    corpus = load_corpus(tmp_path, db=DB)
    tables = corpus.assets
    assert len(tables) >= 2
    assert "customers" in {t.physical_name for t in tables}  # a known beer_factory table

    # Facts present; Inference tier empty because no AI ran.
    for t in tables:
        assert t.description is None
        assert t.columns, f"{t.physical_name} has no columns"
        for c in t.columns:
            assert c.physical_name and c.physical_type
            assert c.logical_type is not None
            assert c.description is None
            assert c.role is None


def test_cli_main_writes(tmp_path):
    out = tmp_path / "corpus"
    rc = main(["--db", DB, "--sqlite", str(_sqlite_path()), "--out", str(out)])
    assert rc == 0
    assert list((out / DB / "tables").glob("*.yaml"))
