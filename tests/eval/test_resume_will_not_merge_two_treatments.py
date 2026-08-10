"""``--resume`` refuses an artifact that was measured under a different treatment.

The artifact filename carries ``--model``, ``--effort``, ``--top-n``, ``--embed``, the provider
and ``--prompt-variant``, so a renamed tag already aborts. It carries **no** corpus, and an
explicit ``--out`` bypasses the tag entirely. Pull ``../BIRD-corpus``, resume, and one artifact
holds two corpora: every gate passes and the driver prints ``ALL GATES PASS -- these numbers are
quotable as a single arm``. Both hashes were on every row the whole time and nothing read them
back.

These tests drive ``resume_identity_problem`` rather than ``main``, because the resume decision
is the only part of ``main`` that does not need a corpus, a dataset, a database and a model.
"""

from __future__ import annotations

import collections
import importlib.util
import pathlib

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]


def _driver():
    """The driver loaded by path, which is how it is run."""
    path = REPO / "tools" / "run_datalake_eval.py"
    spec = importlib.util.spec_from_file_location("_run_datalake_eval_under_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def check():
    return _driver().resume_identity_problem


def _seen(corpus, prompt):
    return {
        "corpus_content_hash": collections.Counter(corpus),
        "prompt_set_hash": collections.Counter(prompt),
    }


def test_an_artifact_from_the_same_treatment_resumes(check):
    refusal, warnings = check(
        _seen({"corpus-a": 5}, {"prompt-p": 5}),
        {"q1", "q2"},
        corpus_content_hash="corpus-a",
        prompt_set_hash="prompt-p",
        question_ids={"q1", "q2", "q3"},
    )
    assert refusal == ""
    assert warnings == []


def test_a_second_corpus_in_the_artifact_refuses_the_resume(check):
    """The failure this guard exists for: ``git pull`` in the corpus, then resume."""
    refusal, _ = check(
        _seen({"corpus-a": 900, "corpus-b": 451}, {"prompt-p": 1351}),
        {"q1"},
        corpus_content_hash="corpus-a",
        prompt_set_hash="prompt-p",
        question_ids={"q1"},
    )
    assert refusal != ""
    assert "corpus_content_hash" in refusal
    # The row count matters: it tells the reader how much of the artifact is the other corpus.
    assert "corpus-b" in refusal and "451" in refusal


def test_a_second_prompt_set_in_the_artifact_refuses_the_resume(check):
    refusal, _ = check(
        _seen({"corpus-a": 10}, {"prompt-p": 6, "prompt-q": 4}),
        {"q1"},
        corpus_content_hash="corpus-a",
        prompt_set_hash="prompt-p",
        question_ids={"q1"},
    )
    assert "prompt_set_hash" in refusal
    assert "prompt-q" in refusal


def test_a_row_predating_the_field_warns_but_does_not_refuse(check):
    """``None`` means "written before this field existed", not "written elsewhere".

    Refusing on it would strand every artifact produced before the treatment hashes landed,
    which is a different and much less likely failure than the one being guarded.
    """
    refusal, warnings = check(
        _seen({None: 4}, {None: 4}),
        {"q1"},
        corpus_content_hash="corpus-a",
        prompt_set_hash="prompt-p",
        question_ids={"q1"},
    )
    assert refusal == ""
    assert len(warnings) == 2
    assert all("cannot prove" in w for w in warnings)


def test_an_artifact_naming_questions_this_run_does_not_cover_refuses(check):
    """A changed ``--dataset`` or a narrowed scope; the driver names both rather than guessing."""
    refusal, _ = check(
        _seen({"corpus-a": 2}, {"prompt-p": 2}),
        {"q1", "gone"},
        corpus_content_hash="corpus-a",
        prompt_set_hash="prompt-p",
        question_ids={"q1"},
    )
    assert "gone" in refusal
    assert "--dataset" in refusal and "--limit" in refusal


def test_a_run_covering_more_questions_than_the_artifact_still_resumes(check):
    """The ordinary case: the artifact is a prefix of this run, which is what resume is for."""
    refusal, warnings = check(
        _seen({"corpus-a": 2}, {"prompt-p": 2}),
        {"q1", "q2"},
        corpus_content_hash="corpus-a",
        prompt_set_hash="prompt-p",
        question_ids={"q1", "q2", "q3", "q4"},
    )
    assert refusal == ""
    assert warnings == []
