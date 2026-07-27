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
    run_dir = tmp_path / "corpus_curated"
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
    run_dir = tmp_path / "corpus_curated"
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
    curated = build_curated_corpus(
        bird_connector,
        gateway,
        "beer_factory",
        train,
        tmp_path / "corpus_curated",
        run_agent=False,
        dialect="sqlite",
    )
    write_clarifications(
        curated / "clarifications.jsonl",
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
    curated_sme = build_curated_corpus_with_sme(
        bird_connector,
        gateway,
        "beer_factory",
        train,
        tmp_path / "corpus_curated_sme",
        responder=StaticResponder(default="Customers who bought root beer."),
        curated_root=curated,
        model=model,
        run_agent_repass=True,
        seed_ledger_if_empty=False,
    )
    import json

    from governed_bi.corpus import load_corpus
    from governed_bi.corpus.schemas import ProvenanceSource, ProvenanceStatus

    manifest = json.loads((curated_sme / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["fold_mode"] == "agent"
    assert manifest["agent_ran"] is True
    assert manifest["ledger_source"] == "agent"
    assert manifest["tool_calls"]["write_total"] >= 1

    corpus = load_corpus(curated_sme, schema="beer_factory")
    customers = next(t for t in corpus.tables() if t.physical_name == "customers")
    assert customers.description == "Customers who bought root beer."
    assert customers.audit is not None
    assert customers.audit.provenance.source is ProvenanceSource.human
    assert customers.audit.provenance.status is ProvenanceStatus.certified


def test_phase_b_empty_ledger_is_noop_not_failure(bird_connector, tmp_path: Path):
    """An empty ledger (agent resolved everything, asked nothing) is acceptable:
    Phase B no-ops and curated_sme == curated rather than raising. Zero SME questions is OK."""
    import json

    gateway = Gateway(bird_connector)
    train = [
        EvalItem(
            question="How many customers?",
            sql="SELECT COUNT(*) FROM customers",
            question_id="t1",
        )
    ]
    curated = build_curated_corpus(
        bird_connector,
        gateway,
        "beer_factory",
        train,
        tmp_path / "corpus_curated",
        run_agent=False,
        dialect="sqlite",
    )
    curated_sme = build_curated_corpus_with_sme(
        bird_connector,
        gateway,
        "beer_factory",
        train,
        tmp_path / "corpus_curated_sme",
        responder=StaticResponder(default="x"),
        curated_root=curated,
        model=None,
        seed_ledger_if_empty=False,
    )
    assert (curated_sme / "beer_factory" / "tables").exists()
    manifest = json.loads((curated_sme / "run_manifest.json").read_text(encoding="utf-8"))
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
        tmp_path / "corpus_curated",
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


def test_pair_scoped_clarification_becomes_note(bird_connector, tmp_path: Path):
    """A pair:/query:-scoped answered clarification (trap/annotation-error finding)
    must land as a governed NoteAsset in the served corpus, not die in the ledger."""
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
    curated = build_curated_corpus(
        bird_connector, gateway, "beer_factory", train,
        tmp_path / "corpus_curated", run_agent=False, dialect="sqlite",
    )
    rec = ClarificationRecord(
        id="q001",
        scope="pair:t1",
        question="The question and gold SQL disagree — which is intended?",
        status=ClarificationRecordStatus.open,
        raised_by=["t1"],
    )
    write_clarifications(clarifications_path(curated), [rec])

    curated_sme = build_curated_corpus_with_sme(
        bird_connector, gateway, "beer_factory", train,
        tmp_path / "corpus_curated_sme",
        responder=StaticResponder(default="This pair is mislabeled; treat as an annotation error."),
        curated_root=curated, model=None,
    )
    corpus = load_corpus(curated_sme, schema="beer_factory")
    notes = [a for a in corpus.assets if a.asset_type == "note"]
    assert notes, "pair-scoped clarification should have become a NoteAsset"
    assert "mislabeled" in notes[0].summary.lower() or "annotation" in notes[0].summary.lower()


# --------------------------------------------------------------------------- #
# The SME round-trip must refuse to hand back a corpus it did not change.
#
# This is the build-side half of the incident that ran for weeks: `curated_sme`
# produced a corpus byte-identical to `curated`, so its EX equalled `curated` by
# construction and the "SME adds nothing" reading was an artifact of the build, not
# a measurement. The ledger has a serve-side detector for it (`sme_noop_dbs`), but
# that only fires after a full paid run. This guard fails the build instead, and it
# had never been exercised — every existing test either has an empty ledger (the
# complementary branch, where a no-op is legitimate) or a fold that genuinely
# differs.
# --------------------------------------------------------------------------- #


def test_an_sme_round_that_changes_nothing_fails_the_build(bird_connector, tmp_path: Path):
    """Open clarifications plus a model that writes nothing must raise, not return.

    Returning here is the dangerous outcome: the corpus is a valid, loadable copy of
    `curated`, so nothing downstream can tell it apart from a real SME corpus except
    by comparing bytes — which is exactly what this check does at the one moment the
    two roots are both to hand.
    """
    gateway = Gateway(bird_connector)
    train = [
        EvalItem(
            question="How many customers?",
            sql="SELECT COUNT(*) FROM customers",
            question_id="t1",
        )
    ]
    curated = build_curated_corpus(
        bird_connector, gateway, "beer_factory", train,
        tmp_path / "corpus_curated", run_agent=False, dialect="sqlite",
    )
    write_clarifications(
        curated / "clarifications.jsonl",
        [
            ClarificationRecord(
                id="q001", scope="table:customers",
                question="Who are the customers?", raised_by=["t1"],
            )
        ],
    )

    # A model that calls no tools at all: the ReAct loop terminates immediately, so
    # the fold applies nothing and the output is a byte-for-byte copy of curated.
    silent = ScriptedToolModel(responses=[AIMessage(content="I have nothing to add.")])

    with pytest.raises(RuntimeError, match="identical to curated"):
        build_curated_corpus_with_sme(
            bird_connector, gateway, "beer_factory", train,
            tmp_path / "corpus_curated_sme",
            responder=StaticResponder(default="Customers who bought root beer."),
            curated_root=curated,
            model=silent,
            run_agent_repass=True,
            seed_ledger_if_empty=False,
        )


def test_corpora_differ_compares_bytes_not_just_presence(tmp_path: Path):
    """The detector under the guard. A same-shaped tree with one edited value must
    read as different, or the guard passes on any corpus that merely has the right
    files — which a plain copy always does."""
    from governed_bi.curator.pipeline import _corpora_differ

    def _tree(root: Path, description: str) -> Path:
        d = root / "beer_factory" / "tables"
        d.mkdir(parents=True)
        (d / "customers.yaml").write_text(
            f"physical_name: customers\ndescription: {description}\n", encoding="utf-8"
        )
        return root

    a = _tree(tmp_path / "a", "Customers.")
    same = _tree(tmp_path / "same", "Customers.")
    edited = _tree(tmp_path / "edited", "Customers who bought root beer.")

    assert not _corpora_differ(a, same, "beer_factory"), "identical bytes must not differ"
    assert _corpora_differ(a, edited, "beer_factory"), "an edited value must differ"

    # A missing schema subtree fingerprints as "" — and two absent trees must not
    # read as "identical, therefore fine", because nothing was built at all.
    empty_a, empty_b = tmp_path / "e1", tmp_path / "e2"
    empty_a.mkdir(), empty_b.mkdir()
    assert not _corpora_differ(empty_a, empty_b, "beer_factory")
    assert _corpora_differ(empty_a, a, "beer_factory")


# --------------------------------------------------------------------------- #
# Two bugs that cost paid builds, both found by reading the real run artifacts.
# --------------------------------------------------------------------------- #


def test_a_non_utf8_description_csv_does_not_lose_the_schema(tmp_path: Path):
    """BIRD ships 11 description CSVs across 5 of the 69 schemas that are not UTF-8.
    `UnicodeDecodeError` is a `ValueError`, so the `except OSError` in `build_sme_brief`
    never caught it and the function raised — inside the SME build, which runs *after*
    baseline, seeded and curated are already paid for. The schema was then dropped from
    the scored pool having cost a full curator pass, and at `--build-workers 1` its YAML
    stayed in the shared arm roots competing as a router candidate for every other
    schema's questions.
    """
    from governed_bi.curator.sme import build_sme_brief

    d = tmp_path / "database_description"
    d.mkdir()
    # cp1252 bytes that are invalid UTF-8 (0x92 = curly apostrophe).
    (d / "t.csv").write_bytes(
        b"column_name,column_description\ncustomer,the customer\x92s name\n"
    )

    brief = build_sme_brief(d, [])
    assert "customer" in brief
    assert "not valid UTF-8" in brief, (
        "the degraded decode must be recorded, or a reader cannot tell why a "
        "description reads oddly"
    )


def test_a_readable_csv_carries_no_degradation_note(tmp_path: Path):
    """The complementary case, so the note above cannot be always-on."""
    from governed_bi.curator.sme import build_sme_brief

    d = tmp_path / "database_description"
    d.mkdir()
    (d / "t.csv").write_text(
        "column_name,column_description\ncustomer,the customer name\n", encoding="utf-8"
    )
    brief = build_sme_brief(d, [])
    assert "the customer name" in brief
    assert "not valid UTF-8" not in brief


def test_an_sme_build_does_not_consume_the_ledger_the_next_arm_needs(
    bird_connector, tmp_path: Path
):
    """`curated_sme_blind` used to answer `curated`'s open clarifications *in curated's
    own ledger*. `curated_sme` then read the same ledger, found nothing open, folded
    nothing, and produced a corpus identical to `curated` — so opting into the rung the
    docs recommend for splitting the docs-vs-protocol confound destroyed the arm the
    confound is about.
    """
    from governed_bi.curator.clarifications import clarifications_path

    gateway = Gateway(bird_connector)
    train = [
        EvalItem(question="How many customers?", sql="SELECT COUNT(*) FROM customers",
                 question_id="t1")
    ]
    curated = build_curated_corpus(
        bird_connector, gateway, "beer_factory", train,
        tmp_path / "corpus_curated", run_agent=False, dialect="sqlite",
    )
    write_clarifications(
        clarifications_path(curated),
        [ClarificationRecord(id="q001", scope="table:customers",
                             question="Who are the customers?", raised_by=["t1"])],
    )

    # Stand in for the blind arm: one SME build off the curated ledger.
    build_curated_corpus_with_sme(
        bird_connector, gateway, "beer_factory", train,
        tmp_path / "corpus_blind",
        responder=StaticResponder(default="Customers who bought root beer."),
        curated_root=curated, model=None, run_agent_repass=False,
        seed_ledger_if_empty=False,
    )

    still_open = [
        r for r in load_clarifications(clarifications_path(curated))
        if r.status is ClarificationRecordStatus.open
    ]
    assert still_open, (
        "the first SME arm consumed curated's open clarifications, so the second SME "
        "arm has nothing to fold and collapses onto curated"
    )
    # And the arm that ran did record its own answers.
    answered = load_clarifications(clarifications_path(tmp_path / "corpus_blind"))
    assert any(r.status is not ClarificationRecordStatus.open for r in answered)


def test_seeded_clarifications_do_not_pose_as_agent_authored_for_the_next_arm(
    bird_connector, tmp_path: Path
):
    """`seed_ledger_if_empty` synthesises gap questions for offline scaffolding. Written
    into `curated_root`'s ledger they became the *next* SME arm's input, and since the
    write-back of answers was removed they stay open — so `curated_sme` found open
    records and stamped `ledger_source="agent"`, the one field whose job is telling
    agent-authored clarifications from mechanically seeded ones.

    This is the path `--skip-agent` takes, which is how the offline smoke runs.
    """
    import json

    from governed_bi.curator.clarifications import clarifications_path

    gateway = Gateway(bird_connector)
    train = [
        EvalItem(question="How many customers?", sql="SELECT COUNT(*) FROM customers",
                 question_id="t1")
    ]
    curated = build_curated_corpus(
        bird_connector, gateway, "beer_factory", train,
        tmp_path / "corpus_curated", run_agent=False, dialect="sqlite",
    )
    assert not clarifications_path(curated).exists(), "curated starts with no ledger"

    sources = []
    for arm in ("blind", "sme"):
        out = build_curated_corpus_with_sme(
            bird_connector, gateway, "beer_factory", train,
            tmp_path / f"corpus_{arm}",
            responder=StaticResponder(default="Customers who bought root beer."),
            curated_root=curated, model=None, run_agent_repass=False,
            seed_ledger_if_empty=True,
        )
        manifest = json.loads(
            (out / "run_manifest.json").read_text(encoding="utf-8")
        )
        sources.append((arm, manifest["ledger_source"], manifest["clarifications_applied"]))

    for arm, source, applied in sources:
        assert source == "seed_gap", (
            f"{arm} reported ledger_source={source!r}; a mechanically seeded ledger must "
            "never be labelled agent-authored"
        )
        assert applied > 0, f"{arm} folded nothing"

    # And the shared input is left exactly as the curator left it.
    assert not clarifications_path(curated).exists(), (
        "an SME build seeded its scaffolding into the arm it derives from"
    )


def test_a_crashed_sme_build_leaves_the_pending_questions_behind(
    bird_connector, tmp_path: Path
):
    """The seed write is truncated by the answered write in a successful build, so it
    leaves no trace there. Its purpose is the failing build: when the responder raises,
    the arm root should still hold the questions that were pending, because that is the
    only record of what the build was attempting. A test pinning the write's *argument*
    rather than its effect left deleting the line entirely undetected.
    """
    from governed_bi.curator.clarifications import (
        clarifications_path,
        load_clarifications,
    )

    class _DeadResponder:
        """Stands in for a rate-limited or unreachable SME.

        ``fill_clarifications_with_responder`` passes the record's *question text*, not
        the record, so the parameter is named for what actually arrives.
        """

        def answer(self, question: str) -> str:  # noqa: ARG002
            raise RuntimeError("429 rate limit")

    gateway = Gateway(bird_connector)
    train = [
        EvalItem(question="How many customers?", sql="SELECT COUNT(*) FROM customers",
                 question_id="t1")
    ]
    curated = build_curated_corpus(
        bird_connector, gateway, "beer_factory", train,
        tmp_path / "corpus_curated", run_agent=False, dialect="sqlite",
    )
    out = tmp_path / "corpus_sme"

    # Pinned to the responder's own failure: a bare `Exception` would let an unrelated
    # raise stand in for the crash this test is about.
    with pytest.raises(RuntimeError, match="429"):
        build_curated_corpus_with_sme(
            bird_connector, gateway, "beer_factory", train, out,
            responder=_DeadResponder(),
            curated_root=curated, model=None, run_agent_repass=False,
            seed_ledger_if_empty=True,
        )

    ledger = clarifications_path(out)
    assert ledger.exists(), (
        "a crashed SME build left no ledger, so nothing records what it was trying to ask"
    )
    pending = load_clarifications(ledger)
    assert pending, "the ledger exists but holds no questions"
    assert all(r.status is ClarificationRecordStatus.open for r in pending), (
        "the pending questions should still read as open — none of them were answered"
    )
    # And the shared input is still untouched, even on the failure path.
    assert not clarifications_path(curated).exists()
