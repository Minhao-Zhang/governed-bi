"""A tool's directory beside a corpus is not corpus content, and must not move the digest.

Measured on ``../BIRD-corpus`` on 2026-08-23: with an untracked ``.conformance-pins.txt`` at the
root the tree hashed ``8bb37531cff9155a…``; with that one file not counted it hashed
``6e5c7b4be83d5682…``, the value ``docs/adr/0015-the-return-path.md`` records for the checked-out
tip. A lint's bookkeeping file had already moved the treatment identity every measured number is
pinned to, merely by sitting in the directory.

The same mechanism says a corpus repository cannot be given CI: adding ``.github/workflows/*.yml``
would change the hash, so the treatment identity would move when a workflow is edited. That is not
reproducible, and it is why ``.github`` is excluded rather than argued about.

Directory-based and not filename-based. ``_is_tooling`` already checks path *parts*, so a tool that
keeps state under ``.conformance/`` needs no new mechanism and no further edit to the exclusion set
— the next tool gets a subdirectory, not another line here.

The narrowness stays pinned in the other direction too: a root ``README.md`` still counts. Whether a
corpus's own description is part of it is a caller's judgement, and the caller already has
``schemas`` to say "the assets alone".
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
    """A corpus with one asset and one prose file, which is the whole treatment here."""
    (root / "beer_factory").mkdir(parents=True)
    (root / "beer_factory" / "t.yaml").write_text(_TABLE, encoding="utf-8")
    (root / "README.md").write_text("# corpus\n", encoding="utf-8")
    return root


def test_a_workflow_file_does_not_move_the_treatment_identity(tmp_path: Path) -> None:
    """CI configuration is not corpus content. A corpus whose identity changes when you edit a
    workflow cannot be given CI at all, which is the concrete thing this unblocks."""
    root = _corpus(tmp_path / "corpus")
    before = corpus_content_hash(root)

    (root / ".github" / "workflows").mkdir(parents=True)
    (root / ".github" / "workflows" / "conformance.yml").write_text(
        "on: [push]\njobs: {}\n", encoding="utf-8"
    )
    assert corpus_content_hash(root) == before, "adding CI must not restate the corpus"

    # …and editing it must not either, or the exclusion only covers the empty tree.
    (root / ".github" / "workflows" / "conformance.yml").write_text(
        "on: [push, pull_request]\njobs: {}\n", encoding="utf-8"
    )
    assert corpus_content_hash(root) == before


def test_a_lints_state_directory_does_not_move_the_treatment_identity(tmp_path: Path) -> None:
    """``.conformance/pins.txt`` is what ``tools/check_ratchet.py`` reads. The pins are a
    property of the tree, so they live beside it; they are a record *about* the corpus and
    counting them would make every ratchet update a new treatment."""
    root = _corpus(tmp_path / "corpus")
    before = corpus_content_hash(root)

    (root / ".conformance").mkdir()
    (root / ".conformance" / "pins.txt").write_text("V17a\tx.yaml:metric_a\t3\n", encoding="utf-8")
    assert corpus_content_hash(root) == before

    (root / ".conformance" / "pins.txt").write_text("V17a\tx.yaml:metric_a\t2\n", encoding="utf-8")
    assert corpus_content_hash(root) == before, "re-pinning is not a corpus edit"


def test_a_corpus_repositorys_readme_still_moves_it(tmp_path: Path) -> None:
    """The control. The exclusion set is narrow on purpose, and a test that only asserts what is
    dropped would also pass for a digest that had stopped reading anything but ``.yaml``."""
    root = tmp_path / "corpus"
    (root / "beer_factory").mkdir(parents=True)
    (root / "beer_factory" / "t.yaml").write_text(_TABLE, encoding="utf-8")

    before = corpus_content_hash(root)
    (root / "README.md").write_text("# corpus\n", encoding="utf-8")
    assert corpus_content_hash(root) != before, "prose at the root is corpus content"


def test_the_exclusion_is_on_parts_so_it_reaches_into_a_schema_subtree(tmp_path: Path) -> None:
    """``_is_tooling`` tests every component of the relative path, not the first one. A schema
    subtree vendored from its own repository brings its own ``.github`` with it, and an exclusion
    anchored at the root would count that while ignoring the identical directory one level up."""
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
    """Not "the hashes match" — the paths are absent from ``corpus_files``.

    The digest is one caller. ``store.read`` and ``snapshot`` walk the same function, and a
    bookkeeping file that reached the loader would be a parse failure rather than a hash drift, so
    the exclusion has to hold at the walk and not at the digest.
    """
    root = _corpus(tmp_path / "corpus")
    (root / ".github" / "workflows").mkdir(parents=True)
    (root / ".github" / "workflows" / "conformance.yml").write_text("on: [push]\n", encoding="utf-8")
    (root / ".conformance").mkdir()
    (root / ".conformance" / "pins.txt").write_text("V17a\ta\t1\n", encoding="utf-8")
    (root / "beer_factory" / ".github").mkdir()
    (root / "beer_factory" / ".github" / "CODEOWNERS").write_text("* @nobody\n", encoding="utf-8")

    found = {p.relative_to(root).as_posix() for p in corpus_files(root)}
    assert found == {"README.md", "beer_factory/t.yaml"}, found
