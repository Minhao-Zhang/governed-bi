"""Tool directories beside a corpus are not content, so they must not move the digest.

Exclusion is by directory component, not by filename. A root README still counts.
"""

from __future__ import annotations

from pathlib import Path

from governed_bi.corpus.hash import corpus_content_hash
from governed_bi.corpus.identity import corpus_files

_TABLE = (
    "asset_type: table\n"
    "id: beer_factory.customers\n"
    "schema: beer_factory\n"
    "physical_name: customers\n"
    "summary: customers - one row per registered buyer\n"
)


def _corpus(root: Path) -> Path:
    (root / "beer_factory").mkdir(parents=True)
    (root / "beer_factory" / "t.yaml").write_text(_TABLE, encoding="utf-8")
    (root / "README.md").write_text("# corpus\n", encoding="utf-8")
    return root


def test_a_workflow_file_does_not_move_the_under_test_identity(tmp_path: Path) -> None:
    """A workflow edit must not move the digest, or the corpus cannot have CI."""
    root = _corpus(tmp_path / "corpus")
    before = corpus_content_hash(root)

    (root / ".github" / "workflows").mkdir(parents=True)
    (root / ".github" / "workflows" / "conformance.yml").write_text(
        "on: [push]\njobs: {}\n", encoding="utf-8"
    )
    assert corpus_content_hash(root) == before, "adding CI must not restate the corpus"

    (root / ".github" / "workflows" / "conformance.yml").write_text(
        "on: [push, pull_request]\njobs: {}\n", encoding="utf-8"
    )
    assert corpus_content_hash(root) == before


def test_a_lints_state_directory_does_not_move_the_under_test_identity(tmp_path: Path) -> None:
    root = _corpus(tmp_path / "corpus")
    before = corpus_content_hash(root)

    (root / ".conformance").mkdir()
    (root / ".conformance" / "pins.txt").write_text("V17a\tx.yaml:metric_a\t3\n", encoding="utf-8")
    assert corpus_content_hash(root) == before

    (root / ".conformance" / "pins.txt").write_text("V17a\tx.yaml:metric_a\t2\n", encoding="utf-8")
    assert corpus_content_hash(root) == before, "re-pinning is not a corpus edit"


def test_a_corpus_repositorys_readme_still_moves_it(tmp_path: Path) -> None:
    """A test that only checks exclusions would also pass if the digest read nothing but yaml."""
    root = tmp_path / "corpus"
    (root / "beer_factory").mkdir(parents=True)
    (root / "beer_factory" / "t.yaml").write_text(_TABLE, encoding="utf-8")

    before = corpus_content_hash(root)
    (root / "README.md").write_text("# corpus\n", encoding="utf-8")
    assert corpus_content_hash(root) != before, "prose at the root is corpus content"


def test_the_exclusion_is_on_parts_so_it_reaches_into_a_schema_subtree(tmp_path: Path) -> None:
    """Every path component counts, so a tool directory inside a schema is excluded too."""
    root = _corpus(tmp_path / "corpus")
    before = corpus_content_hash(root)

    (root / "beer_factory" / ".github").mkdir()
    (root / "beer_factory" / ".github" / "CODEOWNERS").write_text("* @nobody\n", encoding="utf-8")
    (root / "beer_factory" / ".conformance").mkdir()
    (root / "beer_factory" / ".conformance" / "pins.txt").write_text("V17a\ta\t1\n", encoding="utf-8")

    assert corpus_content_hash(root) == before
    assert corpus_content_hash(root, schemas=["beer_factory"]) == corpus_content_hash(
        root, schemas=["beer_factory"]
    )


def test_the_walk_does_not_return_the_excluded_paths_at_all(tmp_path: Path) -> None:
    """The paths are absent from the walk, not only from the digest."""
    root = _corpus(tmp_path / "corpus")
    (root / ".github" / "workflows").mkdir(parents=True)
    (root / ".github" / "workflows" / "conformance.yml").write_text("on: [push]\n", encoding="utf-8")
    (root / ".conformance").mkdir()
    (root / ".conformance" / "pins.txt").write_text("V17a\ta\t1\n", encoding="utf-8")
    (root / "beer_factory" / ".github").mkdir()
    (root / "beer_factory" / ".github" / "CODEOWNERS").write_text("* @nobody\n", encoding="utf-8")

    found = {p.relative_to(root).as_posix() for p in corpus_files(root)}
    assert found == {"README.md", "beer_factory/t.yaml"}, found
