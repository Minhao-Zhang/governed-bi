"""``GET /examples`` returns few-shot questions. A question that exists only outside those assets stays out."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[2]
CORPUS = REPO.parent / "BIRD-corpus"
HELD_OUT_FILE = REPO.parent / "BIRD-Data-Obfuscation" / "eval_dataset" / "test_final.jsonl"

#: Not a few-shot. It is indexed, so a search that forgot the type filter would return it first.
HELD_OUT = "How many held-out widgets were sold in the secret quarter?"

_QUESTIONS = {
    "fs_alpha": ("alpha", "How many widgets shipped last week?", "SELECT alpha_secret"),
    "fs_alpha_2": ("alpha", "Which product sold best?", "SELECT alpha_two_secret"),
    "fs_beta": ("beta", "Which warehouse holds the most stock?", "SELECT beta_secret"),
    "fs_mu": ("mu", "What is the oldest brand on record?", "SELECT mu_secret"),
    "fs_zeta": ("zeta", "How many customers are there?", "SELECT zeta_secret"),
}


@pytest.fixture(scope="module")
def client() -> Any:
    from fastapi.testclient import TestClient

    from governed_bi.api.routes import make_app
    from governed_bi.corpus.schema import FewShotAsset, SchemaAsset
    from governed_bi.govern.policy import GovernancePolicy
    from governed_bi.serve.session import from_assets

    assets: list[Any] = [
        SchemaAsset(id="secret", name="secret", summary=HELD_OUT),
    ]
    for asset_id, (db_id, question, sql) in _QUESTIONS.items():
        assets.append(
            FewShotAsset(
                id=asset_id,
                schema=db_id,
                sql=sql,
                summary=question,
            )
        )
    session = from_assets(
        assets,
        connector=None,
        policy=GovernancePolicy(guard_rules_enabled={}),
        db_id="alpha",
        corpus_content_hash_="examples-under-test",
    )
    assert not session.fatal_problems, [str(p) for p in session.fatal_problems]
    return TestClient(make_app(session, object()))


def test_limit_takes_one_database_at_a_time_in_a_stable_order(client: Any) -> None:
    response = client.get("/examples", params={"limit": 3})
    assert response.status_code == 200, response.text
    rows = response.json()["examples"]
    assert rows == [
        {"question": "How many widgets shipped last week?", "db_id": "alpha"},
        {"question": "Which warehouse holds the most stock?", "db_id": "beta"},
        {"question": "What is the oldest brand on record?", "db_id": "mu"},
    ]
    assert client.get("/examples").json()["examples"] == rows
    assert client.get("/examples", params={"near": "  ", "limit": 3}).json()["examples"] == rows
    assert "SELECT" not in response.text
    assert HELD_OUT not in response.text


def test_near_ranks_the_question_that_shares_words_first(client: Any) -> None:
    response = client.get("/examples", params={"near": "widgets shipped", "limit": 3})
    assert response.status_code == 200, response.text
    rows = response.json()["examples"]
    assert rows[0] == {"question": "How many widgets shipped last week?", "db_id": "alpha"}
    assert all(set(row) == {"question", "db_id"} for row in rows)
    assert "SELECT" not in response.text
    assert HELD_OUT not in response.text


def test_limit_outside_1_to_10_is_rejected(client: Any) -> None:
    assert client.get("/examples", params={"limit": 0}).status_code == 422
    assert client.get("/examples", params={"limit": 11}).status_code == 422


def test_a_question_outside_the_few_shots_never_comes_back(client: Any) -> None:
    few_shots = {question for _db, question, _sql in _QUESTIONS.values()}
    for params in ({"limit": 10}, {"near": HELD_OUT, "limit": 10}):
        response = client.get("/examples", params=params)
        assert response.status_code == 200, response.text
        questions = [row["question"] for row in response.json()["examples"]]
        assert questions
        assert set(questions) <= few_shots
        assert HELD_OUT not in questions


def test_the_real_corpus_returns_no_held_out_question() -> None:
    """Skipped, with the reason printed, when the sibling corpus or the held-out file is absent."""
    if not CORPUS.is_dir() or not HELD_OUT_FILE.is_file():
        reason = (
            f"absent: corpus {CORPUS} exists={CORPUS.is_dir()}; "
            f"held-out {HELD_OUT_FILE} exists={HELD_OUT_FILE.is_file()}"
        )
        print(reason)
        pytest.skip(reason)

    from fastapi.testclient import TestClient

    from governed_bi.api.routes import make_app
    from governed_bi.corpus.schema import AssetType
    from governed_bi.govern.policy import GovernancePolicy
    from governed_bi.serve.session import from_corpus_dir

    held_out: set[str] = set()
    near_texts: list[str] = []
    for line in HELD_OUT_FILE.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        question = str(json.loads(line)["question"]).strip()
        held_out.add(question)
        if len(near_texts) < 3:
            near_texts.append(question)
    assert held_out and near_texts

    session = from_corpus_dir(
        CORPUS,
        connector=None,
        policy=GovernancePolicy(guard_rules_enabled={}),
    )
    few_shots = {
        asset.summary.strip()
        for asset in session.assets_by_id.values()
        if asset.asset_type is AssetType.few_shot
    }
    assert few_shots
    http = TestClient(make_app(session, object()))
    calls: list[dict[str, Any]] = [{"limit": 10}, *({"near": text, "limit": 10} for text in near_texts)]
    for params in calls:
        response = http.get("/examples", params=params)
        assert response.status_code == 200, response.text
        rows = response.json()["examples"]
        assert len(rows) == 10
        questions = [row["question"] for row in rows]
        assert set(questions) <= few_shots
        leaked = set(questions) & held_out
        assert not leaked, leaked
        assert all(set(row) == {"question", "db_id"} and row["db_id"] for row in rows)
