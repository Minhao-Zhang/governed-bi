"""Behavioral tests for the curator deep agent (scripted tool-calling model).

FakeListChatModel cannot ``bind_tools`` (NotImplementedError). These tests use a
minimal BaseChatModel that returns scripted AIMessages with tool_calls so the
ReAct loop actually runs offline.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("deepagents")

from langchain_core.language_models.chat_models import BaseChatModel  # noqa: E402
from langchain_core.messages import AIMessage  # noqa: E402
from langchain_core.outputs import ChatGeneration, ChatResult  # noqa: E402

from governed_bi.curator.asset_bag import AssetBag  # noqa: E402
from governed_bi.curator.clarifications import (  # noqa: E402
    ClarificationRecord,
    ClarificationRecordStatus,
    StaticResponder,
    load_clarifications,
    write_clarifications,
)
from governed_bi.curator.deep_agent import build_curator_agent  # noqa: E402
from governed_bi.curator.pipeline import (  # noqa: E402
    build_curated_corpus,
    build_curated_corpus_with_sme,
)
from governed_bi.curator.profile import profile_database  # noqa: E402
from governed_bi.eval.dataset import EvalItem  # noqa: E402
from governed_bi.gateway import Gateway, SqliteConnector  # noqa: E402

BIRD_DB = Path(__file__).resolve().parents[1] / "data" / "bird" / "beer_factory.sqlite"


class ScriptedToolModel(BaseChatModel):
    """Offline chat model that supports bind_tools and plays back AIMessages."""

    responses: list
    i: int = 0

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        msg = self.responses[min(self.i, len(self.responses) - 1)]
        object.__setattr__(self, "i", self.i + 1)
        return ChatResult(generations=[ChatGeneration(message=msg)])

    @property
    def _llm_type(self) -> str:
        return "scripted-tool-model"

    def bind_tools(self, tools, **kwargs):
        return self


def _tc(name: str, args: dict, id_: str) -> dict:
    return {"name": name, "args": args, "id": id_, "type": "tool_call"}


@pytest.fixture
def bird_connector():
    if not BIRD_DB.exists():
        pytest.skip("vendored beer_factory.sqlite not present")
    conn = SqliteConnector(BIRD_DB)
    yield conn
    conn.close()


def test_phase_a_agent_authors_ledger_and_annotates(bird_connector, tmp_path: Path):
    """Scripted Phase A: annotate + write_file /clarifications.jsonl (same disk path)."""
    gateway = Gateway(bird_connector)
    tables = profile_database(bird_connector, schema="beer_factory")
    bag = AssetBag.from_tables("beer_factory", tables)
    run_dir = tmp_path / "corpus_a2"
    run_dir.mkdir()

    line = (
        '{"id":"q001","scope":"table:customers","question":"Who are the customers?",'
        '"status":"open","raised_by":["t1"],"answer":null,"answered_by":null}\n'
    )
    model = ScriptedToolModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[_tc("read_corpus", {"table": "customers"}, "1")],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    _tc(
                        "annotate_table",
                        {"table": "customers", "description": "Beer customers"},
                        "2",
                    )
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    _tc(
                        "write_file",
                        {"file_path": "/clarifications.jsonl", "content": line},
                        "3",
                    )
                ],
            ),
            AIMessage(content="Phase A done"),
        ]
    )
    agent = build_curator_agent(
        model,
        connector=bird_connector,
        schema="beer_factory",
        gateway=gateway,
        bag=bag,
        run_dir=run_dir,
    )
    result = agent.invoke(
        {"messages": [{"role": "user", "content": "curate"}]},
        config={"recursion_limit": 40},
    )
    assert bag.tables["customers"].description == "Beer customers"
    ledger_path = run_dir / "clarifications.jsonl"
    assert ledger_path.exists()
    records = load_clarifications(ledger_path)
    assert len(records) == 1
    assert records[0].id == "q001"
    assert records[0].status is ClarificationRecordStatus.open
    # Tool calls visible in trajectory
    names = []
    for msg in result["messages"]:
        for tc in getattr(msg, "tool_calls", None) or []:
            names.append(tc["name"] if isinstance(tc, dict) else tc["name"])
    assert "annotate_table" in names
    assert "write_file" in names


def test_phase_a_agent_edit_broadens_same_id(bird_connector, tmp_path: Path):
    """Acceptance (b) via file tools: edit_file keeps q001, broadens question."""
    gateway = Gateway(bird_connector)
    bag = AssetBag.from_tables(
        "beer_factory", profile_database(bird_connector, schema="beer_factory")
    )
    run_dir = tmp_path / "corpus_a2"
    run_dir.mkdir()
    old = (
        '{"id":"q001","scope":"table:customers","question":"Who are customers?",'
        '"status":"open","raised_by":["t1"],"answer":null,"answered_by":null}\n'
    )
    new = (
        '{"id":"q001","scope":"table:customers",'
        '"question":"Who are customers — also: what is the grain?",'
        '"status":"open","raised_by":["t1","t2"],"answer":null,"answered_by":null}\n'
    )
    (run_dir / "clarifications.jsonl").write_text(old, encoding="utf-8")

    model = ScriptedToolModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    _tc(
                        "edit_file",
                        {
                            "file_path": "/clarifications.jsonl",
                            "old_string": old.strip(),
                            "new_string": new.strip(),
                        },
                        "e1",
                    )
                ],
            ),
            AIMessage(content="broadened"),
        ]
    )
    agent = build_curator_agent(
        model,
        connector=bird_connector,
        schema="beer_factory",
        gateway=gateway,
        bag=bag,
        run_dir=run_dir,
    )
    agent.invoke(
        {"messages": [{"role": "user", "content": "broaden"}]},
        config={"recursion_limit": 20},
    )
    records = load_clarifications(run_dir / "clarifications.jsonl")
    assert len(records) == 1
    assert records[0].id == "q001"
    assert records[0].raised_by == ["t1", "t2"]
    assert "grain" in records[0].question


def test_phase_b_agent_ingests_with_certified_provenance(bird_connector, tmp_path: Path):
    """Scripted Phase B: ingest agent annotates with certified=true (not deterministic)."""
    gateway = Gateway(bird_connector)
    train = [
        EvalItem(
            question="How many customers?",
            sql="SELECT COUNT(*) FROM customers",
            question_id="t1",
        )
    ]
    a2 = build_curated_corpus(
        bird_connector,
        gateway,
        "beer_factory",
        train,
        tmp_path / "corpus_a2",
        run_agent=False,
        dialect="sqlite",
    )
    write_clarifications(
        a2 / "clarifications.jsonl",
        [
            ClarificationRecord(
                id="q001",
                scope="table:customers",
                question="Who are the customers?",
                raised_by=["t1"],
            )
        ],
    )

    model = ScriptedToolModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    _tc(
                        "annotate_table",
                        {
                            "table": "customers",
                            "description": "Customers who bought root beer.",
                            "certified": True,
                            "answered_by": "sme",
                            "confidence": 0.9,
                        },
                        "b1",
                    )
                ],
            ),
            AIMessage(content="ingested"),
        ]
    )
    a3 = build_curated_corpus_with_sme(
        bird_connector,
        gateway,
        "beer_factory",
        train,
        tmp_path / "corpus_a3",
        responder=StaticResponder(default="Customers who bought root beer."),
        a2_root=a2,
        model=model,
        run_agent_repass=True,
        seed_ledger_if_empty=False,
    )
    import json

    from governed_bi.corpus import load_corpus
    from governed_bi.corpus.schemas import ProvenanceSource, ProvenanceStatus

    manifest = json.loads((a3 / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["fold_mode"] == "agent"
    assert manifest["agent_ran"] is True
    assert manifest["ledger_source"] == "agent"
    assert manifest["tool_calls"]["write_total"] >= 1

    corpus = load_corpus(a3, schema="beer_factory")
    customers = next(t for t in corpus.tables() if t.physical_name == "customers")
    assert customers.description == "Customers who bought root beer."
    assert customers.audit is not None
    assert customers.audit.provenance.source is ProvenanceSource.human
    assert customers.audit.provenance.status is ProvenanceStatus.certified


def test_phase_b_empty_ledger_is_noop_not_failure(bird_connector, tmp_path: Path):
    """An empty ledger (agent resolved everything, asked nothing) is acceptable:
    Phase B no-ops and A3 == A2 rather than raising. Zero SME questions is OK."""
    import json

    gateway = Gateway(bird_connector)
    train = [
        EvalItem(
            question="How many customers?",
            sql="SELECT COUNT(*) FROM customers",
            question_id="t1",
        )
    ]
    a2 = build_curated_corpus(
        bird_connector,
        gateway,
        "beer_factory",
        train,
        tmp_path / "corpus_a2",
        run_agent=False,
        dialect="sqlite",
    )
    a3 = build_curated_corpus_with_sme(
        bird_connector,
        gateway,
        "beer_factory",
        train,
        tmp_path / "corpus_a3",
        responder=StaticResponder(default="x"),
        a2_root=a2,
        model=None,
        seed_ledger_if_empty=False,
    )
    assert (a3 / "beer_factory" / "tables").exists()
    manifest = json.loads((a3 / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["fold_mode"] == "none"
    assert manifest["clarifications_applied"] == 0


def test_phase_a_manifest_marks_missing_ledger(bird_connector, tmp_path: Path):
    import json

    gateway = Gateway(bird_connector)
    train = [
        EvalItem(
            question="How many customers?",
            sql="SELECT COUNT(*) FROM customers",
            question_id="t1",
        )
    ]
    root = build_curated_corpus(
        bird_connector,
        gateway,
        "beer_factory",
        train,
        tmp_path / "corpus_a2",
        run_agent=False,
        dialect="sqlite",
    )
    manifest = json.loads((root / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["ledger_source"] == "missing"
    assert manifest["clarification_count"] == 0
    assert manifest["agent_ran"] is False
    assert "write" in manifest["tool_calls"]
    assert "read" in manifest["tool_calls"]


def test_sme_runs_as_deep_agent_with_probe(bird_connector):
    """SimulatedSme with a live model + gateway is a read-only deep agent that can
    call run_probe_query, then answer — a real multi-turn ReAct loop, not single-shot."""
    from governed_bi.curator.sme import SimulatedSme

    gateway = Gateway(bird_connector)
    scripted = ScriptedToolModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[_tc("run_probe_query", {"sql": "SELECT COUNT(*) FROM customers"}, "p1")],
            ),
            AIMessage(content="The customers table holds one row per beer customer."),
        ]
    )

    class _ModelChat:  # SimulatedSme reads chat.model to build the agent
        def __init__(self, model):
            self.model = model

    sme = SimulatedSme(_ModelChat(scripted), "You are an SME for beer_factory.", gateway=gateway)
    ans = sme.answer("What does the customers table represent?")
    assert "customer" in ans.lower()
    assert scripted.i >= 2, "expected a probe turn then an answer turn (real ReAct loop)"


def test_last_message_text_strips_reasoning_parts():
    """A reasoning model's content is a list of typed parts; the reasoning part
    carries encrypted CoT and no "text" key. It must be dropped, not stringified
    into the SME answer (which becomes a rule statement)."""
    from governed_bi.curator.sme import _last_message_text

    reasoning_part = {
        "id": "rs_0cd149",
        "type": "reasoning",
        "summary": [],
        "content": [],
        "encrypted_content": "gAAAAABsecretcothatmustnotleak",
    }
    text_part = {"type": "text", "text": "Use `review > 2` to match the gold interpretation."}
    result = {"messages": [AIMessage(content=[reasoning_part, text_part])]}

    out = _last_message_text(result)
    assert out == "Use `review > 2` to match the gold interpretation."
    assert "encrypted_content" not in out
    assert "gAAAAAB" not in out


def test_pair_scoped_clarification_becomes_rule(bird_connector, tmp_path: Path):
    """A pair:/query:-scoped answered clarification (trap/annotation-error finding)
    must land as a governance RuleAsset in the served corpus, not die in the ledger."""
    from governed_bi.corpus import load_corpus
    from governed_bi.curator.clarifications import clarifications_path

    gateway = Gateway(bird_connector)
    train = [
        EvalItem(
            question="How many customers?",
            sql="SELECT COUNT(*) FROM customers",
            question_id="t1",
        )
    ]
    a2 = build_curated_corpus(
        bird_connector, gateway, "beer_factory", train,
        tmp_path / "corpus_a2", run_agent=False, dialect="sqlite",
    )
    rec = ClarificationRecord(
        id="q001",
        scope="pair:t1",
        question="The question and gold SQL disagree — which is intended?",
        status=ClarificationRecordStatus.open,
        raised_by=["t1"],
    )
    write_clarifications(clarifications_path(a2), [rec])

    a3 = build_curated_corpus_with_sme(
        bird_connector, gateway, "beer_factory", train,
        tmp_path / "corpus_a3",
        responder=StaticResponder(default="This pair is mislabeled; treat as an annotation error."),
        a2_root=a2, model=None,
    )
    corpus = load_corpus(a3, schema="beer_factory")
    rules = [a for a in corpus.assets if a.asset_type == "rule"]
    assert rules, "pair-scoped clarification should have become a RuleAsset"
    assert "mislabeled" in rules[0].statement.lower() or "annotation" in rules[0].statement.lower()
