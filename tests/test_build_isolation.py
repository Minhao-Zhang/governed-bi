"""Concurrent per-db corpus builds must not share a directory.

The curator writes its five sidecars (``clarifications.jsonl``,
``validate_findings.jsonl``, ``adversary_findings.jsonl``, ``sme_clarifications.jsonl``,
``run_manifest.json``) at the *arm root*, not per schema, and points the deep agent's
``FilesystemBackend`` at that same root. Serially that is fine — each build's sidecars
are relocated into ``<root>/<db>/_build/`` before the next starts. Concurrently it is
data corruption, and for the SME arm it is one schema's clarification text leaking
into another schema's corpus.

The fix keeps every path relationship *inside* a build byte-identical to the serial
case and gives each build a private staging root, promoted on success. These tests pin
that: the same files land in the same places, an interleaved pair does not mix, and a
failure cleans up after itself without taking the run down.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import threading
from pathlib import Path

import pytest

from governed_bi.eval.run_datalake import (
    _BUILD_COMPLETE_MARKER,
    _SIDECARS,
    _mark_build_complete,
    _promote_build,
    _relocate_sidecars,
)


def _fake_build(root: Path, db_id: str, *, marker: str) -> None:
    """Write what a real curator build writes.

    Three classes of artifact, and the third is the one an earlier version of this
    helper omitted — which let the parity test below pass while the real concurrent
    path was deleting the file:

    1. per-schema YAML under ``<root>/<db_id>/`` (``AssetBag.write``);
    2. the five root-level sidecars in ``_SIDECARS``;
    3. the durable ``BUILD_COMPLETE.json`` marker (resume/promote completeness).
    The curator used to also drop ``agent_checkpoints_<schema>.sqlite`` here; it no
    longer creates one, so every file this helper writes is a diagnostic whose loss
    matters.
    """
    schema_dir = root / db_id / "tables"
    schema_dir.mkdir(parents=True, exist_ok=True)
    (schema_dir / "t.yaml").write_text(f"id: tbl_{db_id}_t\nmarker: {marker}\n", encoding="utf-8")
    for name in _SIDECARS:
        (root / name).write_text(
            json.dumps({"db": db_id, "marker": marker}) + "\n", encoding="utf-8"
        )
    _mark_build_complete(root, db_id)


# --------------------------------------------------------------------------- #
# _promote_build
# --------------------------------------------------------------------------- #


def test_promotion_lands_yaml_and_sidecars_where_the_serial_path_put_them(tmp_path: Path):
    shared = tmp_path / "arm"
    shared.mkdir()
    staged = tmp_path / "staging" / "arm__beer"
    staged.mkdir(parents=True)
    _fake_build(staged, "beer", marker="a")

    _promote_build(staged, shared, "beer")

    assert (shared / "beer" / "tables" / "t.yaml").exists()
    for name in _SIDECARS:
        assert (shared / "beer" / "_build" / name).exists(), name
    # No sidecar left at the shared root, which is what the next db would clobber.
    for name in _SIDECARS:
        assert not (shared / name).exists()
    assert not staged.exists(), "the staging root is discarded once promoted"


def test_promotion_matches_the_serial_relocate_layout(tmp_path: Path):
    """Parallel and serial must produce the same tree, or a run's layout depends on
    a concurrency knob."""
    serial = tmp_path / "serial"
    serial.mkdir()
    _fake_build(serial, "beer", marker="a")
    _relocate_sidecars(serial, "beer")

    shared = tmp_path / "parallel"
    shared.mkdir()
    staged = tmp_path / "stage" / "p"
    staged.mkdir(parents=True)
    _fake_build(staged, "beer", marker="a")
    _promote_build(staged, shared, "beer")

    def _tree(root: Path) -> set[str]:
        return {str(p.relative_to(root)).replace("\\", "/") for p in root.rglob("*")}

    assert _tree(serial) == _tree(shared)


def test_two_builds_promoted_from_private_roots_do_not_mix(tmp_path: Path):
    """The corruption this exists to prevent, stated as data."""
    shared = tmp_path / "arm"
    shared.mkdir()
    for db, marker in (("beer", "A"), ("movies", "B")):
        staged = tmp_path / "staging" / f"arm__{db}"
        staged.mkdir(parents=True)
        _fake_build(staged, db, marker=marker)
        _promote_build(staged, shared, db)

    beer = json.loads((shared / "beer" / "_build" / "clarifications.jsonl").read_text())
    movies = json.loads((shared / "movies" / "_build" / "clarifications.jsonl").read_text())
    assert beer == {"db": "beer", "marker": "A"}
    assert movies == {"db": "movies", "marker": "B"}


def test_a_shared_root_really_would_have_clobbered(tmp_path: Path):
    """The counterfactual, so the isolation test above is not just describing itself.

    Two builds writing to one root leave ONE clarifications.jsonl holding the second
    db's content. Under the SME arm that content is folded into the corpus.
    """
    shared = tmp_path / "arm"
    shared.mkdir()
    _fake_build(shared, "beer", marker="A")
    _fake_build(shared, "movies", marker="B")  # no relocate in between
    survivor = json.loads((shared / "clarifications.jsonl").read_text())
    assert survivor == {"db": "movies", "marker": "B"}, (
        "one root, one sidecar: beer's clarifications are gone"
    )


def test_promotion_replaces_a_previous_build_of_the_same_db(tmp_path: Path):
    """A --no-resume rebuild must not merge into the stale tree."""
    shared = tmp_path / "arm"
    (shared / "beer" / "tables").mkdir(parents=True)
    (shared / "beer" / "tables" / "stale.yaml").write_text("old\n", encoding="utf-8")

    staged = tmp_path / "stage"
    staged.mkdir()
    _fake_build(staged, "beer", marker="new")
    _promote_build(staged, shared, "beer")

    assert not (shared / "beer" / "tables" / "stale.yaml").exists()
    assert (shared / "beer" / "tables" / "t.yaml").exists()


def test_promotion_refuses_an_incomplete_staged_tree(tmp_path: Path):
    """Partial YAML without BUILD_COMPLETE must not become the live corpus."""
    shared = tmp_path / "arm"
    shared.mkdir()
    staged = tmp_path / "stage"
    (staged / "beer" / "tables").mkdir(parents=True)
    (staged / "beer" / "tables" / "half.yaml").write_text("x: 1\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="incomplete"):
        _promote_build(staged, shared, "beer")
    assert not (shared / "beer").exists()


def test_promotion_failure_after_moving_aside_restores_the_old_corpus(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A kill between renaming the old tree aside and installing the new one must
    leave the old corpus, not an empty destination."""
    shared = tmp_path / "arm"
    shared.mkdir()
    _fake_build(shared, "beer", marker="old")
    old_yaml = (shared / "beer" / "tables" / "t.yaml").read_text(encoding="utf-8")

    staged = tmp_path / "stage"
    staged.mkdir()
    _fake_build(staged, "beer", marker="new")

    real_rename = Path.rename

    def flaky_rename(self: Path, target):
        # First rename in the swap path is dest -> previous; second is incoming -> dest.
        # Fail the incoming install so the old tree must be restored.
        if self.name.startswith(".beer.incoming"):
            raise OSError("injected promote failure")
        return real_rename(self, target)

    monkeypatch.setattr(Path, "rename", flaky_rename)

    with pytest.raises(OSError, match="injected promote failure"):
        _promote_build(staged, shared, "beer")

    assert (shared / "beer" / "tables" / "t.yaml").exists(), (
        "destination was left empty after a failed promote"
    )
    assert (shared / "beer" / "tables" / "t.yaml").read_text(encoding="utf-8") == old_yaml


def test_promotion_heals_missing_dest_from_previous_before_installing(
    tmp_path: Path,
):
    """Prior-crash leftovers must not be deleted while live dest is missing.

    Simulate a death after ``dest → .previous`` (and with a stale ``.incoming``):
    the next promote must restore ``.previous`` (or complete ``.incoming``) before
    clearing anything, then install the new staged tree.
    """
    shared = tmp_path / "arm"
    shared.mkdir()
    previous = shared / ".beer.previous"
    incoming_leftover = shared / ".beer.incoming"
    _fake_build(previous.parent / "_tmp_prev", "beer", marker="old-good")
    # Place the recovered tree under the leftover name the healer looks for.
    (previous.parent / "_tmp_prev" / "beer").rename(previous)
    shutil.rmtree(previous.parent / "_tmp_prev")
    old_yaml = (previous / "tables" / "t.yaml").read_text(encoding="utf-8")

    # Stale incoming from the crashed attempt — must not win over previous, and
    # must not be deleted while dest is still missing.
    incoming_leftover.mkdir()
    (incoming_leftover / "tables").mkdir()
    (incoming_leftover / "tables" / "t.yaml").write_text(
        "id: tbl_beer_t\nmarker: stale-incoming\n", encoding="utf-8"
    )
    assert not (shared / "beer").exists()

    staged = tmp_path / "stage"
    staged.mkdir()
    _fake_build(staged, "beer", marker="fresh")
    _promote_build(staged, shared, "beer")

    live = shared / "beer"
    assert live.is_dir()
    assert (live / "tables" / "t.yaml").read_text(encoding="utf-8") == (
        "id: tbl_beer_t\nmarker: fresh\n"
    )
    assert not previous.exists()
    assert not incoming_leftover.exists()
    # The recoverable old corpus was not the final live tree, but it had to exist
    # long enough for the swap — prove we did not start from an empty hole by
    # checking the old content was distinct from the fresh install.
    assert old_yaml == "id: tbl_beer_t\nmarker: old-good\n"


def test_promotion_heals_missing_dest_from_incoming_when_no_previous(
    tmp_path: Path,
):
    """If only ``.incoming`` survives a crash, complete it to live before installing."""
    shared = tmp_path / "arm"
    shared.mkdir()
    incoming = shared / ".beer.incoming"
    _fake_build(shared / "_tmp", "beer", marker="incoming-only")
    (shared / "_tmp" / "beer").rename(incoming)
    shutil.rmtree(shared / "_tmp")
    assert not (shared / "beer").exists()

    staged = tmp_path / "stage"
    staged.mkdir()
    _fake_build(staged, "beer", marker="fresh")
    _promote_build(staged, shared, "beer")

    assert (shared / "beer" / "tables" / "t.yaml").read_text(encoding="utf-8") == (
        "id: tbl_beer_t\nmarker: fresh\n"
    )
    assert not incoming.exists()


def test_promotion_heal_then_injected_failure_keeps_a_live_corpus(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Heal leftovers, then fail mid-swap: a valid live dest must still remain."""
    shared = tmp_path / "arm"
    shared.mkdir()
    previous = shared / ".beer.previous"
    _fake_build(shared / "_tmp", "beer", marker="recoverable")
    (shared / "_tmp" / "beer").rename(previous)
    shutil.rmtree(shared / "_tmp")
    recoverable = (previous / "tables" / "t.yaml").read_text(encoding="utf-8")
    assert not (shared / "beer").exists()

    staged = tmp_path / "stage"
    staged.mkdir()
    _fake_build(staged, "beer", marker="fresh")

    real_rename = Path.rename

    def flaky_rename(self: Path, target):
        # After heal, dest exists (restored from previous). Fail installing the
        # new incoming so rollback must leave a live corpus.
        if self.name.startswith(".beer.incoming"):
            raise OSError("injected promote failure after heal")
        return real_rename(self, target)

    monkeypatch.setattr(Path, "rename", flaky_rename)

    with pytest.raises(OSError, match="injected promote failure after heal"):
        _promote_build(staged, shared, "beer")

    live = shared / "beer" / "tables" / "t.yaml"
    assert live.exists(), "heal + failed install left no live corpus"
    assert live.read_text(encoding="utf-8") == recoverable


def test_promotion_of_a_build_that_wrote_no_schema_dir_still_lifts_sidecars(tmp_path: Path):
    """A build that failed after writing findings but before writing YAML still has
    diagnostics worth keeping."""
    shared = tmp_path / "arm"
    shared.mkdir()
    staged = tmp_path / "stage"
    staged.mkdir()
    (staged / "validate_findings.jsonl").write_text('{"x":1}\n', encoding="utf-8")

    _promote_build(staged, shared, "beer")

    assert (shared / "beer" / "_build" / "validate_findings.jsonl").exists()


# --------------------------------------------------------------------------- #
# The loop: isolation under real threads
# --------------------------------------------------------------------------- #


def test_concurrent_builds_keep_their_sidecars_apart_under_real_threads(tmp_path: Path):
    """Interleave two builds on purpose and assert neither sees the other's writes.

    Each 'build' writes its sidecar, waits for the other to have written its own, then
    reads back. On a shared root the read-back returns the other db's content; on
    private roots it cannot.
    """
    both_written = threading.Barrier(2, timeout=5)
    seen: dict[str, dict] = {}
    shared = tmp_path / "arm"
    shared.mkdir()

    def _build(db: str, marker: str) -> None:
        staged = tmp_path / "staging" / f"arm__{db}"
        staged.mkdir(parents=True)
        _fake_build(staged, db, marker=marker)
        both_written.wait()
        seen[db] = json.loads((staged / "clarifications.jsonl").read_text())
        _promote_build(staged, shared, db)

    threads = [
        threading.Thread(target=_build, args=("beer", "A")),
        threading.Thread(target=_build, args=("movies", "B")),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert seen["beer"] == {"db": "beer", "marker": "A"}
    assert seen["movies"] == {"db": "movies", "marker": "B"}
    assert (shared / "beer" / "tables" / "t.yaml").exists()
    assert (shared / "movies" / "tables" / "t.yaml").exists()


# --------------------------------------------------------------------------- #
# Worker resolution
# --------------------------------------------------------------------------- #


def test_build_worker_count_falls_back_to_the_single_workers_knob():
    from dataclasses import replace

    from governed_bi.config import Environment, Settings

    s = Settings.for_env(Environment.dev)
    assert s.build_worker_count() == 1, "serial is the default"

    assert replace(s, eval_workers=4).build_worker_count() == 4
    # The split override wins, and is independent of the serve-loop width: a build
    # worker holds a connection AND a deep-agent conversation.
    both = replace(s, eval_workers=4, eval_build_workers=2, eval_serve_workers=8)
    assert both.build_worker_count() == 2
    assert both.serve_worker_count() == 8


def test_build_workers_is_recorded_in_the_manifest_but_is_not_a_resume_knob():
    """It changes how long a run takes, never what a row means — and per-build
    isolation makes resuming at a different width safe."""
    from governed_bi.eval.run_datalake import _RESUME_KNOBS, _build_manifest

    m = _build_manifest(
        bird_dir=Path("/bird"),
        split="test",
        model_name="m",
        llm_reasoning_effort=None,
        embedding_model=None,
        embedding_dimensions=None,
        prompt_variants={"agent_core": "v1"},
        route_top_k=10,
        route_llm_pick=True,
        schema_pick_max_columns=12,
        use_embedder=True,
        serve_workers=4,
        question_pool_hash="pool0000",
        always_note_global_max=8,
        always_note_char_max=2000,
        pin_triggers_enabled=False,
        pin_require_certified=True,
        pin_max=3,
        grade_semantic_failures=True,
        build_workers=6,
    )
    assert m["build_workers"] == 6
    assert "build_workers" not in _RESUME_KNOBS
    assert "serve_workers" not in _RESUME_KNOBS
def test_the_worker_count_never_outruns_the_factory(tmp_path: Path):
    """`--workers 2 --oracle <rung>` used to abort the whole run.

    The factory was dropped for oracle rungs but the worker COUNT was not, and
    `_run_pool_arm` raises when asked for more than one worker without a factory. It
    died at the rung, after the fair arms had spent their model calls and before
    `summary.json` was written.

    Asserted on `plan_arm_serving`, which is what the driver applies, rather than on
    the source text of `run_datalake` — a reformat broke the old version and a
    semantically identical rewrite defeated it.
    """
    from governed_bi.eval.oracle import OracleRung
    from governed_bi.eval.run_datalake import plan_arm_serving

    for rung in (None, *OracleRung):
        for workers in (1, 2, 8):
            for has_model in (True, False):
                plan = plan_arm_serving(
                    rung=rung,
                    source_arm="curated",
                    oracle_base="curated",
                    effective_workers=workers,
                    has_model=has_model,
                )
                if plan.n_workers > 1:
                    assert plan.needs_factory, (rung, workers, has_model)


def test_an_oracle_rung_is_served_at_full_width_under_its_own_rung(tmp_path: Path):
    """The wiring no offline command can reach, so nothing but this pins it.

    `--skip-agent` rejects every rung but `oracle_sql`, and `effective_workers` is
    forced to 1 without a model — so "oracle rung at width > 1" is first executed by
    the paid run. A mutation dropping `rung` on the way to the worker factory left the
    whole suite green while making every rung serve as an ordinary arm under a rung's
    name, silently replacing the headroom bounds the runbook reads everything against.
    """
    from governed_bi.eval.oracle import OracleRung
    from governed_bi.eval.run_datalake import plan_arm_serving

    plan = plan_arm_serving(
        rung=OracleRung.schema,
        source_arm="oracle_schema",  # the rung's own name — never a corpus key
        oracle_base="curated",
        effective_workers=8,
        has_model=True,
    )
    assert plan.rung is OracleRung.schema, "the rung was dropped on the way to serving"
    assert plan.corpus_arm == "curated", "a rung narrows its BASE arm's corpus"
    assert plan.n_workers == 8
    assert plan.needs_factory

    # A fair arm is unaffected and keyed on itself.
    fair = plan_arm_serving(
        rung=None,
        source_arm="curated",
        oracle_base="curated",
        effective_workers=8,
        has_model=True,
    )
    assert fair.rung is None
    assert fair.corpus_arm == "curated"

    # Without a model only `oracle_sql` can serve, so only it may fan out.
    for rung, expected in ((OracleRung.sql, True), (OracleRung.schema, False)):
        offline = plan_arm_serving(
            rung=rung,
            source_arm="baseline",
            oracle_base="baseline",
            effective_workers=8,
            has_model=False,
        )
        assert offline.needs_factory is expected, rung

    # A rung with no base arm is a wiring bug, not a KeyError three layers down.
    with pytest.raises(ValueError, match="no base arm"):
        plan_arm_serving(
            rung=OracleRung.tables,
            source_arm="baseline",
            oracle_base=None,
            effective_workers=1,
            has_model=True,
        )


def test_run_pool_arm_still_refuses_workers_without_a_factory(tmp_path: Path):
    """The guard the fix above respects must stay — it is what catches the next
    caller that forgets."""
    from types import SimpleNamespace

    from governed_bi.eval.run_datalake import _run_pool_arm

    item = SimpleNamespace(question_id="q1", question="q?", sql="SELECT 1", difficulty=None)
    with pytest.raises(ValueError, match="worker_factory"):
        _run_pool_arm(
            arm="oracle_tables",
            solver=object(),
            pairs=[(item, "db")],
            gold_hashes={},
            gateway=object(),
            identity=object(),
            bird_dir=Path("."),
            suspect_by_db={},
            arm_corpus=None,
            dialect="postgres",
            twin_ids=frozenset(),
            ungradeable_ids=frozenset(),
            out_path=tmp_path / "unused.jsonl",
            serve_workers=2,
            worker_factory=None,
        )


# --------------------------------------------------------------------------- #
# A leaked file handle must not be able to discard a corpus that built fine.
# --------------------------------------------------------------------------- #


def test_closing_the_curator_checkpointer_releases_its_file_handle(tmp_path: Path):
    """The sqlite saver holds an open handle. On Windows that makes the file
    unmovable, so relocating it raised PermissionError, the exception propagated out
    of the build, the db was dropped — and since it fired on every db, a paid run
    ended in "every db failed to build" with no summary.json."""
    from dataclasses import replace as dc_replace

    from governed_bi.analyst.run_log import close_checkpointer, make_durable_checkpointer
    from governed_bi.config import Environment, Settings

    settings = dc_replace(
        Settings.for_env(Environment.dev), conversation_checkpointer_kind="sqlite"
    )
    ckpt_path = tmp_path / "agent_checkpoints_beer.sqlite"
    saver = make_durable_checkpointer(settings, path=str(ckpt_path))
    assert ckpt_path.exists()

    close_checkpointer(saver)
    # The operation that used to fail. On POSIX this always worked; on Windows it is
    # the whole finding.
    ckpt_path.replace(tmp_path / "moved.sqlite")
    assert (tmp_path / "moved.sqlite").exists()


def test_close_checkpointer_tolerates_anything(tmp_path: Path):
    """It runs on the way out of a build that already succeeded, so it must never be
    what discards the corpus."""
    from governed_bi.analyst.run_log import close_checkpointer

    class _Angry:
        @property
        def conn(self):
            raise RuntimeError("boom")

    class _BadClose:
        def close(self):
            raise RuntimeError("boom")

    close_checkpointer(None)
    close_checkpointer(object())
    close_checkpointer(_Angry())
    close_checkpointer(_BadClose())
def test_a_missing_run_manifest_reads_as_no_curator_error():
    """The downstream behaviour that makes losing the file dangerous, stated as a
    test so the two halves cannot drift apart."""
    from governed_bi.eval.harness import _collect_curator_errors

    assert _collect_curator_errors({"curated": Path("nonexistent")}) == {}


def test_a_copied_sidecar_does_not_stay_behind_to_poison_the_next_db(tmp_path: Path, monkeypatch):
    """When the move fails and the copy succeeds, the original must go. Left at the
    arm root in serial mode it is the NEXT db's build that overwrites it — the exact
    cross-db clobber this relocation exists to prevent."""
    root = tmp_path / "arm"
    root.mkdir()
    _fake_build(root, "beer", marker="A")

    real_replace = Path.replace

    def _no_move(self, target):
        if self.name == "run_manifest.json":
            raise OSError("simulated lock")
        return real_replace(self, target)

    monkeypatch.setattr(Path, "replace", _no_move)
    stuck = _relocate_sidecars(root, "beer")

    assert stuck == [], "the copy fallback should have placed it"
    assert (root / "beer" / "_build" / "run_manifest.json").exists()
    assert not (root / "run_manifest.json").exists(), (
        "the original was left at the arm root, where the next db overwrites it"
    )
def test_relocation_is_deferred_until_every_arm_of_the_db_is_built():
    import inspect

    from governed_bi.eval import run_datalake

    src = inspect.getsource(run_datalake._build_db_corpora)
    body, _, teardown = src.partition("finally:")
    assert "_relocate_sidecars(" not in body, (
        "an arm's sidecars are relocated mid-build; a later arm that reads one of "
        "them (the SME reads the curated clarification ledger) will find it gone"
    )
    assert "_relocate_sidecars(" in teardown, "relocation must still happen"
    assert "pending_relocations" in body


def test_the_sme_reads_a_ledger_that_relocation_would_have_hidden(tmp_path: Path):
    """The behaviour, not just the ordering: a ledger at the curated arm root is
    visible; the same ledger relocated is not."""
    from governed_bi.curator.clarifications import (
        ClarificationRecord,
        clarifications_path,
        load_clarifications,
        write_clarifications,
    )

    root = tmp_path / "corpus_curated"
    (root / "beer").mkdir(parents=True)
    write_clarifications(
        clarifications_path(root),
        [ClarificationRecord(id="q1", scope="table:x", question="what is x?", raised_by=["t1"])],
    )
    assert len(load_clarifications(clarifications_path(root))) == 1

    _relocate_sidecars(root, "beer")

    assert len(load_clarifications(clarifications_path(root))) == 0, (
        "this is what the SME arm saw: an empty ledger, so it folded nothing"
    )
    assert (root / "beer" / "_build" / "clarifications.jsonl").exists()


# --------------------------------------------------------------------------- #
# An unknown curator verdict must survive as a FACT about the run, not as a file
# in a directory the harness itself clears.
#
# Two earlier attempts tried to preserve the file. Both failed: the staging root
# "kept for inspection" is cleared at the start of the next build, so a `--resume`
# erased it and then promoted cleanly (scoring the db as healthy); and serial mode
# has no staging root at all, so the file sat at the arm root until the next db
# overwrote it. The marker goes where the promoted artifacts go, in both modes.
# --------------------------------------------------------------------------- #


def _block(*names):
    """Make `replace` and `copy2` fail for the named files."""
    import shutil as _sh

    real_replace, real_copy = Path.replace, _sh.copy2

    def _no_move(self, target):
        if self.name in names:
            raise OSError("simulated lock")
        return real_replace(self, target)

    def _no_copy(src, dst, *a, **k):
        if Path(src).name in names:
            raise OSError("simulated lock")
        return real_copy(src, dst, *a, **k)

    return _no_move, _no_copy


def test_an_unpromotable_diagnostic_is_recorded_in_the_shared_root(tmp_path, monkeypatch):
    shared = tmp_path / "arm"
    shared.mkdir()
    staged = tmp_path / "stage"
    staged.mkdir()
    _fake_build(staged, "beer", marker="A")
    mv, cp = _block("run_manifest.json")
    monkeypatch.setattr(Path, "replace", mv)
    monkeypatch.setattr("shutil.copy2", cp)

    _promote_build(staged, shared, "beer")  # must not raise

    marker = shared / "beer" / "_build" / "UNPROMOTED_SIDECARS.json"
    assert marker.exists(), "the unknown verdict left no durable trace"
    assert "run_manifest.json" in json.loads(marker.read_text())["unpromoted"]


def test_the_marker_survives_the_staging_wipe_that_defeated_the_last_fix(
    tmp_path, monkeypatch
):
    """The resume case. Staging is cleared at the start of every build, so anything
    kept there is gone by the time an operator looks."""
    shared = tmp_path / "arm"
    shared.mkdir()
    staged = tmp_path / "stage"
    staged.mkdir()
    _fake_build(staged, "beer", marker="A")
    mv, cp = _block("run_manifest.json")
    monkeypatch.setattr(Path, "replace", mv)
    monkeypatch.setattr("shutil.copy2", cp)
    _promote_build(staged, shared, "beer")

    # Simulate the next invocation clearing staging, exactly as `_build_one` does.
    import shutil as _sh

    _sh.rmtree(staged, ignore_errors=True)

    assert (shared / "beer" / "_build" / "UNPROMOTED_SIDECARS.json").exists()


def test_serial_mode_records_the_same_marker(tmp_path, monkeypatch):
    """`--build-workers` defaults to 1, so serial IS the default path. It never went
    through `_promote_build`, and therefore never got the previous fix at all."""
    root = tmp_path / "arm"
    root.mkdir()
    _fake_build(root, "beer", marker="A")
    mv, cp = _block("run_manifest.json")
    monkeypatch.setattr(Path, "replace", mv)
    monkeypatch.setattr("shutil.copy2", cp)

    _relocate_sidecars(root, "beer")  # serial call site: dest_root=None

    assert (root / "beer" / "_build" / "UNPROMOTED_SIDECARS.json").exists()


def test_the_marker_makes_the_run_unquotable(tmp_path):
    """It has to reach the gate, or it is just another file nobody reads."""
    from governed_bi.eval.harness import _collect_curator_errors
    from governed_bi.eval.index import quotable

    build = tmp_path / "curated" / "beer" / "_build"
    build.mkdir(parents=True)
    (build / "UNPROMOTED_SIDECARS.json").write_text(
        json.dumps({"db_id": "beer", "unpromoted": ["run_manifest.json"]}),
        encoding="utf-8",
    )

    errs = _collect_curator_errors({"curated": build})
    assert "curated" in errs
    assert "verdict is unknown" in errs["curated"]["error"]

    ok, reasons = quotable({"curator_error_keys": sorted(errs)})
    assert ok is False
    assert any("curator build errors" in r for r in reasons)
def test_every_arm_that_ran_is_checked_for_curator_errors():
    """The list was hardcoded to `curated`/`curated_sme`, which was complete until
    `seeded` was added — after which a swallowed curator
    error on either was invisible to `summary.json` and therefore to `quotable()`."""
    import inspect

    from governed_bi.eval import run_datalake

    src = inspect.getsource(run_datalake.run_datalake)
    call = src.split("_collect_curator_errors(", 1)[1].split(")", 1)[0]
    assert "for arm in arms" in call, f"arm set is not derived from what ran: {call!r}"
    assert '"curated", "curated_sme"' not in call


def test_a_marker_and_a_real_curator_error_are_both_reported(tmp_path):
    """Returning early on the marker dropped a recorded curator crash in favour of a
    note about a different missing file — losing the more specific finding."""
    from governed_bi.eval.harness import _collect_curator_errors

    d = tmp_path / "_build"
    d.mkdir()
    (d / "UNPROMOTED_SIDECARS.json").write_text(
        json.dumps({"db_id": "beer", "unpromoted": ["validate_findings.jsonl"]}),
        encoding="utf-8",
    )
    (d / "run_manifest.json").write_text(
        json.dumps({"error": "KeyError: 'restaurant'\nsecond line"}), encoding="utf-8"
    )

    errs = _collect_curator_errors({"curated": d})
    assert "verdict is unknown" in errs["curated"]["error"]
    assert "KeyError" in errs["curated"]["error"], "the real crash was dropped"


def test_a_marker_alone_is_still_reported_when_no_manifest_exists(tmp_path):
    from governed_bi.eval.harness import _collect_curator_errors

    d = tmp_path / "_build"
    d.mkdir()
    (d / "UNPROMOTED_SIDECARS.json").write_text(
        json.dumps({"db_id": "beer", "unpromoted": ["run_manifest.json"]}),
        encoding="utf-8",
    )
    errs = _collect_curator_errors({"curated": d})
    assert "verdict is unknown" in errs["curated"]["error"]


def test_a_clean_arm_reports_nothing(tmp_path):
    from governed_bi.eval.harness import _collect_curator_errors

    d = tmp_path / "_build"
    d.mkdir()
    (d / "run_manifest.json").write_text(json.dumps({"phase": "A"}), encoding="utf-8")
    assert _collect_curator_errors({"curated": d}) == {}
def test_a_real_curator_agent_build_creates_no_checkpoint_file(tmp_path: Path):
    """deepagents needs a checkpointer only for `interrupt_on`, which the curator does
    not use. The sqlite saver it used to create was written, closed, relocated and
    never read back — and an open sqlite handle is unmovable on Windows, which aborted
    every curated build. Removing it deletes the failure class instead of guarding it.
    """
    pytest.importorskip("deepagents")
    import pathlib
    import sqlite3

    from langchain_core.messages import AIMessage

    from governed_bi.config import load_settings
    from governed_bi.curator.pipeline import build_curated_corpus
    from governed_bi.eval.dataset import EvalItem
    from governed_bi.gateway import Gateway, SqliteConnector
    from test_curator_agent_behavior import ScriptedToolModel  # type: ignore

    db = tmp_path / "t.sqlite"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE customers (id INTEGER, name TEXT)")
    con.execute("INSERT INTO customers VALUES (1, 'a')")
    con.commit()
    con.close()

    connector = SqliteConnector(db)
    out_root = tmp_path / "corpus_curated"
    try:
        build_curated_corpus(
            connector,
            Gateway(connector),
            "main",
            [EvalItem(question="how many?", sql="SELECT COUNT(*) FROM customers", question_id="t1")],
            out_root,
            model=ScriptedToolModel(responses=[AIMessage(content="done"), AIMessage(content="done")]),
            dialect="sqlite",
            run_agent=True,
            settings=load_settings(),
        )
    finally:
        connector.close()

    assert list(out_root.rglob("agent_checkpoints_*")) == []

    # The agent must have actually RUN and succeeded. Without this the test passes
    # even when `checkpointer=None` breaks invocation outright: `_invoke_agent`
    # swallows the exception into the manifest's `error` field, and `bag.write` runs
    # from the seed data regardless — so "some yaml exists" is satisfied by a total
    # agent failure. That is the false pass this assertion closes.
    manifest = json.loads((out_root / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["error"] is None, f"the agent failed: {manifest['error']}"
    assert manifest.get("agent_ran") is True

    # No absolute path in a durable artifact. It was the one field that differed
    # between two byte-identical builds, because it embedded the run's own output
    # directory — which made "did these two runs produce the same corpus?"
    # un-answerable with a plain diff, and leaked a machine-local path.
    recorded = manifest["clarifications_path"]
    assert recorded == pathlib.Path(recorded).name, recorded
    assert str(out_root) not in recorded

    assert any(out_root.rglob("*.yaml"))
    # ...and everything it did write is promotable, with no marker.
    _relocate_sidecars(out_root, "main")
    assert not (out_root / "main" / "_build" / "UNPROMOTED_SIDECARS.json").exists()
def test_the_serve_paths_clarify_checkpointer_is_untouched():
    """That one IS used — `ask_user` interrupts need it — so the removal must not
    have reached it."""
    from governed_bi.analyst.run_log import (
        close_checkpointer,
        make_clarify_checkpointer,
        make_durable_checkpointer,
    )

    assert callable(make_clarify_checkpointer)
    assert callable(make_durable_checkpointer)
    assert callable(close_checkpointer)


# --------------------------------------------------------------------------- #
# Staging setup: the two things that decide whether a parallel build means the
# same as a serial one.
#
# The promotion half is covered above. What was not: what a staging root starts out
# holding, and whether `--resume` can still see what is already on disk. Both live
# in `_stage_roots`, which the build worker calls once per db.
# --------------------------------------------------------------------------- #


def test_staging_roots_are_cleared_not_merely_created(tmp_path):
    """Debris from a killed build must not survive into the next one.

    `_corpus_complete` requires ``BUILD_COMPLETE.json``, so a half-written
    corpus left in staging would still be cleared — and even if it were not,
    resume would refuse to treat YAML alone as finished.
    """
    from governed_bi.eval.run_datalake import _stage_roots

    staging, roots = tmp_path / "_staging", {
        "baseline": tmp_path / "corpus_baseline",
        "curated": tmp_path / "corpus_curated",
    }
    for r in roots.values():
        r.mkdir()

    # Debris from a previous, killed attempt at the same db.
    debris = staging / "curated__beer_factory" / "beer_factory" / "tables"
    debris.mkdir(parents=True)
    (debris / "half_written.yaml").write_text("physical_name: cust", encoding="utf-8")

    staged = _stage_roots(staging, roots, "beer_factory", resume=False)

    assert set(staged) == {"baseline", "curated"}
    assert not list(staged["curated"].rglob("*.yaml")), (
        "debris from a killed build survived into the next one"
    )
    for path in staged.values():
        assert path.is_dir(), "the root must exist even though it is empty"


def test_each_db_and_arm_gets_its_own_staging_root(tmp_path):
    """Two dbs building concurrently must not be handed the same directory — that
    is the whole basis of build isolation."""
    from governed_bi.eval.run_datalake import _stage_roots

    staging = tmp_path / "_staging"
    roots = {a: tmp_path / f"corpus_{a}" for a in ("baseline", "curated")}
    for r in roots.values():
        r.mkdir()

    a = _stage_roots(staging, roots, "beer_factory", resume=False)
    b = _stage_roots(staging, roots, "restaurant", resume=False)

    everything = [*a.values(), *b.values()]
    assert len(set(everything)) == len(everything), "a staging path was reused"
    # And staging a second db must not disturb the first, which may be mid-build.
    assert all(p.is_dir() for p in a.values())


def test_resume_seeds_staging_from_what_is_already_built(tmp_path):
    """A resumed parallel build decides what to skip by looking at its build root, so
    the root has to start out holding what the shared root already has. Without the
    seeding the output is still correct — every arm is simply rebuilt — which is why
    this is worth a test: the failure is silent and costs a full rebuild.

    Only a *complete* prior (``BUILD_COMPLETE.json``) is seeded. Partial YAML on the
    shared root is discarded, not adopted.
    """
    from governed_bi.eval.run_datalake import _mark_build_complete, _stage_roots

    staging = tmp_path / "_staging"
    roots = {a: tmp_path / f"corpus_{a}" for a in ("baseline", "curated")}
    for r in roots.values():
        r.mkdir()
    # `baseline` is already built for this db; `curated` is not.
    prior = roots["baseline"] / "beer_factory" / "tables"
    prior.mkdir(parents=True)
    (prior / "customers.yaml").write_text("physical_name: customers\n", encoding="utf-8")
    _mark_build_complete(roots["baseline"], "beer_factory")

    staged = _stage_roots(staging, roots, "beer_factory", resume=True)

    seeded = staged["baseline"] / "beer_factory" / "tables" / "customers.yaml"
    assert seeded.is_file(), "resume cannot see the arm that is already built"
    assert seeded.read_text(encoding="utf-8") == "physical_name: customers\n"
    # The unbuilt arm stays empty, or the build would skip an arm it never built.
    assert not list(staged["curated"].rglob("*.yaml"))


def test_resume_staging_discards_shared_root_partial_yaml(tmp_path):
    """Kill debris in the shared arm root must not be seeded into staging on resume."""
    from governed_bi.eval.run_datalake import _stage_roots

    staging = tmp_path / "_staging"
    roots = {"curated": tmp_path / "corpus_curated"}
    roots["curated"].mkdir()
    partial = roots["curated"] / "beer_factory" / "tables"
    partial.mkdir(parents=True)
    (partial / "half.yaml").write_text("physical_name: half\n", encoding="utf-8")
    assert not (
        roots["curated"] / "beer_factory" / "_build" / _BUILD_COMPLETE_MARKER
    ).exists()

    staged = _stage_roots(staging, roots, "beer_factory", resume=True)

    assert not list(staged["curated"].rglob("*.yaml")), (
        "partial shared-root YAML was seeded as if complete"
    )
    assert not (roots["curated"] / "beer_factory").exists(), (
        "incomplete shared tree should be discarded, not left for a later skip"
    )


def test_without_resume_staging_starts_empty_even_when_the_shared_root_is_full(tmp_path):
    """A fresh run must rebuild. Seeding from the shared root without `--resume`
    would make a re-run silently adopt the previous run's corpus, and its numbers
    would describe a corpus this run never built."""
    from governed_bi.eval.run_datalake import _stage_roots

    staging = tmp_path / "_staging"
    roots = {"curated": tmp_path / "corpus_curated"}
    prior = roots["curated"] / "beer_factory" / "tables"
    prior.mkdir(parents=True)
    (prior / "customers.yaml").write_text("physical_name: customers\n", encoding="utf-8")

    staged = _stage_roots(staging, roots, "beer_factory", resume=False)
    assert not list(staged["curated"].rglob("*.yaml"))


# --------------------------------------------------------------------------- #
# The composition: does `--build-workers N` produce the same corpora as 1?
#
# The mechanics underneath each had tests — staging clearing, per-(arm, db)
# isolation, resume seeding, promotion layout. What had none was the dispatch that
# composes them, because it was a closure inside `run_datalake()` and could only be
# reached by driving the whole harness (Postgres, gold, serve loop). That is exactly
# where a parallel build would silently diverge from a serial one, so it is now a
# module-level function taking the build as an argument.
# --------------------------------------------------------------------------- #


def _fingerprint_tree(root):
    """Content hash of every YAML under `root`, path-relative so absolute staging
    paths cannot leak into the comparison."""
    h = hashlib.sha256()
    for path in sorted(root.rglob("*.yaml")):
        h.update(path.relative_to(root).as_posix().encode())
        h.update(path.read_bytes())
    return h.hexdigest()


def _deterministic_build(db, build_roots):
    """Stand-in for `_build_db_corpora`: writes per-arm YAML derived only from the db
    name, so any difference between a serial and a parallel run is the dispatch's
    doing and not the build's."""
    from governed_bi.eval.run_datalake import _mark_build_complete

    for arm, root in build_roots.items():
        d = root / db / "tables"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{db}_t.yaml").write_text(
            f"physical_name: {db}_t\narm: {arm}\ndescription: built for {db}\n",
            encoding="utf-8",
        )
        (root / db / "_build").mkdir(parents=True, exist_ok=True)
        (root / db / "_build" / "run_manifest.json").write_text(
            json.dumps({"db": db, "arm": arm}), encoding="utf-8"
        )
        _mark_build_complete(root, db)


def _run_phase(tmp_path, dbs, workers, *, build=_deterministic_build, resume=False):
    from governed_bi.eval.run_datalake import run_build_phase

    out = tmp_path / f"w{workers}"
    roots = {a: out / f"corpus_{a}" for a in ("baseline", "seeded", "curated")}
    for r in roots.values():
        r.mkdir(parents=True)
    errors: dict[str, str] = {}
    built = run_build_phase(
        dbs,
        roots=roots,
        staging_root=out / "_staging",
        build_workers=workers,
        resume=resume,
        build_errors=errors,
        build_lock=threading.Lock(),
        build_one_db=build,
    )
    return built, errors, roots


def test_a_parallel_build_produces_byte_identical_corpora_to_a_serial_one(tmp_path):
    dbs = [f"db_{i}" for i in range(8)]

    serial_built, serial_errors, serial_roots = _run_phase(tmp_path, dbs, 1)
    par_built, par_errors, par_roots = _run_phase(tmp_path, dbs, 4)

    assert serial_errors == par_errors == {}
    # Completion order may differ under concurrency; membership must not.
    assert sorted(serial_built) == sorted(par_built) == dbs

    for arm in serial_roots:
        assert _fingerprint_tree(serial_roots[arm]) == _fingerprint_tree(par_roots[arm]), (
            f"arm {arm} differs between build_workers=1 and build_workers=4"
        )

    # And nothing is left behind in staging that a later --resume could adopt as a
    # finished corpus.
    leftover = list((tmp_path / "w4" / "_staging").rglob("*.yaml"))
    assert leftover == [], f"staging still holds YAML after promotion: {leftover}"


def test_the_fingerprint_would_actually_notice_a_difference(tmp_path):
    """Guards the test above from passing vacuously. If `_fingerprint_tree` were
    insensitive — hashing only paths, say — the equality assertion would hold for two
    genuinely different corpora."""
    dbs = ["db_0", "db_1"]
    _b, _e, a_roots = _run_phase(tmp_path, dbs, 1)

    def _different(db, build_roots):
        _deterministic_build(db, build_roots)
        for root in build_roots.values():
            p = root / db / "tables" / f"{db}_t.yaml"
            p.write_text(p.read_text(encoding="utf-8") + "extra: 1\n", encoding="utf-8")

    _b2, _e2, b_roots = _run_phase(tmp_path, dbs, 4, build=_different)
    assert _fingerprint_tree(a_roots["curated"]) != _fingerprint_tree(b_roots["curated"])


def test_one_failing_db_does_not_lose_the_others_at_either_width(tmp_path):
    """A scale run must not lose 68 schemas to one bad one, and the surviving set has
    to be the same whichever width was used — otherwise the pool being scored depends
    on a concurrency knob."""
    dbs = [f"db_{i}" for i in range(6)]

    def _one_bad(db, build_roots):
        if db == "db_3":
            raise RuntimeError("connection refused")
        _deterministic_build(db, build_roots)

    serial_built, serial_errors, serial_roots = _run_phase(tmp_path, dbs, 1, build=_one_bad)
    par_built, par_errors, par_roots = _run_phase(tmp_path, dbs, 4, build=_one_bad)

    assert sorted(serial_built) == sorted(par_built)
    assert "db_3" not in serial_built and "db_3" not in par_built
    assert set(serial_errors) == set(par_errors) == {"db_3"}
    assert "connection refused" in serial_errors["db_3"]
    for arm in serial_roots:
        assert _fingerprint_tree(serial_roots[arm]) == _fingerprint_tree(par_roots[arm])
    # The failed db's staging is kept as the only evidence of why it failed.
    kept = list((tmp_path / "w4" / "_staging").glob("*db_3*"))
    assert kept, "the failed build's staging root was deleted"


def test_every_db_failing_returns_empty_rather_than_a_partial_pool(tmp_path):
    """`run_datalake` raises on an empty result. That check is only meaningful if the
    phase reports empty rather than a subset."""
    def _all_bad(db, build_roots):
        raise RuntimeError("nope")

    built, errors, _roots = _run_phase(tmp_path, ["a", "b", "c"], 4, build=_all_bad)
    assert built == []
    assert set(errors) == {"a", "b", "c"}


def test_a_single_db_at_high_worker_count_still_matches_the_serial_tree(tmp_path):
    """`build_workers > 1 and len(wanted) > 1` gates the *thread pool*, so one db at
    `--build-workers 6` runs without one. It still stages and promotes, because that
    branch is keyed on `build_workers` alone — the earlier name claimed it skipped
    staging, which is wrong. What matters is that the width does not change the output.
    """
    _b1, _e1, serial_roots = _run_phase(tmp_path, ["only"], 1)
    built, errors, roots = _run_phase(tmp_path, ["only"], 6)
    assert built == ["only"] and errors == {}
    for arm in roots:
        assert _fingerprint_tree(roots[arm]) == _fingerprint_tree(serial_roots[arm])


def test_progress_is_reported_as_each_build_finishes_not_all_at_the_end(tmp_path):
    """`Executor.map` submits eagerly but yields lazily, and `pool.__exit__` calls
    `shutdown(wait=True)`. Draining the iterator after the `with` block therefore
    prints nothing until every schema has finished, then everything at once — hours of
    silence on the 69-schema run this feature exists for, and no log of what completed
    if the process dies partway.

    No final-state test can catch that: `built` and `build_errors` are correct either
    way. So this compares *when* the first progress line is written against when the
    last build finishes. Streaming means the former precedes the latter; batching means
    every line lands after it.
    """
    import io
    import sys
    import time

    from governed_bi.eval import run_datalake as rd

    # Three waves, not two: with 6 builds over 3 workers the first line and the last
    # build land within milliseconds of each other and the assertion could flake. Nine
    # over three puts roughly half a second between them.
    dbs = [f"db_{i}" for i in range(9)]
    t0 = time.perf_counter()
    progress_writes: list[float] = []
    build_finishes: list[float] = []

    class _Stamping(io.TextIOBase):
        def write(self, text):
            if "built corpora:" in text:
                progress_writes.append(time.perf_counter() - t0)
            return len(text)

    def _slow(db, build_roots):
        time.sleep(0.25)
        _deterministic_build(db, build_roots)
        build_finishes.append(time.perf_counter() - t0)

    out = tmp_path / "w"
    roots = {"baseline": out / "corpus_baseline"}
    roots["baseline"].mkdir(parents=True)
    errors: dict[str, str] = {}

    real_stdout = sys.stdout
    sys.stdout = _Stamping()
    try:
        built = rd.run_build_phase(
            dbs,
            roots=roots,
            staging_root=out / "_staging",
            build_workers=3,
            resume=False,
            build_errors=errors,
            build_lock=threading.Lock(),
            build_one_db=_slow,
        )
    finally:
        sys.stdout = real_stdout

    assert sorted(built) == dbs and errors == {}
    assert len(progress_writes) == 9, progress_writes
    assert len(build_finishes) == 9

    # The invariant: reporting overlaps building. Batched output puts every write
    # after the final build, so this is the assertion that separates the two.
    assert progress_writes[0] < build_finishes[-1], (
        f"the first progress line was written at {progress_writes[0]:.3f}s, after the "
        f"last build finished at {build_finishes[-1]:.3f}s — output is batched, not "
        "streamed"
    )


def test_a_staging_failure_does_not_name_the_shared_roots_as_debris(tmp_path, capsys):
    """When `_stage_roots` itself raises, `build_roots` is still the shared arm roots —
    which by then hold other schemas' promoted corpora. Reporting those as this build's
    disposable staging debris would invite an operator to delete live data."""
    from governed_bi.eval import run_datalake as rd

    out = tmp_path / "w"
    roots = {a: out / f"corpus_{a}" for a in ("baseline", "curated")}
    for r in roots.values():
        r.mkdir(parents=True)
    # Pre-populate the shared roots, as a successful earlier build would have.
    for r in roots.values():
        (r / "db_ok" / "tables").mkdir(parents=True)
        (r / "db_ok" / "tables" / "t.yaml").write_text("physical_name: t\n", encoding="utf-8")

    def _boom(*_a, **_k):
        raise OSError("no space left on device")

    errors: dict[str, str] = {}
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(rd, "_stage_roots", _boom)
        built = rd.run_build_phase(
            ["db_a", "db_b"],
            roots=roots,
            staging_root=out / "_staging",
            build_workers=2,
            resume=False,
            build_errors=errors,
            build_lock=threading.Lock(),
            build_one_db=_deterministic_build,
        )

    assert built == []
    assert set(errors) == {"db_a", "db_b"}
    assert all("no space left" in v for v in errors.values())
    printed = capsys.readouterr().out
    assert "staging kept for inspection" not in printed, (
        "a staging failure named the shared arm roots as failure debris"
    )
    # The earlier build's data is untouched, which is the thing that matters.
    for r in roots.values():
        assert (r / "db_ok" / "tables" / "t.yaml").is_file()
