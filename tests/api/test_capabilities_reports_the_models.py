"""`/capabilities` must report the model each surface actually resolved.

The regression these guard is not cosmetic. ``chat_model`` and ``llm_utility_model`` are
``Role.comparability`` knobs, and on Bedrock every turn recorded the LangChain *class* name
``amazon_bedrock_converse_chat`` instead of the model id — so two arms serving different
Anthropic models published the same value and the drift gate compared them equal
(``runs/serve/2026-08-18.jsonl``, both turns, while actually running
``us.anthropic.claude-sonnet-5``).
"""

from __future__ import annotations

from typing import Any

from governed_bi.api.routes import capabilities_for, connection_for, models_for
from governed_bi.datasource.postgres import PostgresConnector
from governed_bi.datasource.sqlite import SqliteConnector
from governed_bi.serve.runtime import model_id
from governed_bi.serve.session import _model_name

#: A DSN with a password in it, so the redaction assertions have something to fail on.
_DSN = "host=127.0.0.1 port=5432 dbname=facilities user=facilities password=super_secret_pw"


class _Bedrock:
    """``ChatBedrockConverse``'s shape: it spells the id ``model_id`` and nothing else.

    Deliberately not a mock of the real class — the point is the *attribute spelling*, and a
    fake that offered ``model_name`` too would pass while the real client failed.
    """

    model_id = "us.anthropic.claude-sonnet-5"
    _llm_type = "amazon_bedrock_converse_chat"


class _OpenAI:
    model_name = "gpt-4o-mini"
    _llm_type = "openai-chat"


def test_model_id_reads_the_bedrock_spelling() -> None:
    assert model_id(_Bedrock()) == "us.anthropic.claude-sonnet-5"


def test_model_name_does_not_fall_back_to_the_class_name() -> None:
    """The `_llm_type` branch must not be reached when an id is available."""
    assert _model_name(_Bedrock()) == "us.anthropic.claude-sonnet-5"
    assert _model_name(_Bedrock()) != "amazon_bedrock_converse_chat"


def test_model_name_still_prefers_model_name_over_model_id() -> None:
    """`model_id` is last in the probe order, so earlier arms keep their recorded meaning."""
    assert _model_name(_OpenAI()) == "gpt-4o-mini"


class _Session:
    connector = type("C", (), {"dialect": "postgres"})()
    agent_model = object()
    utility_model = None
    knobs_resolved: dict[str, Any] = {
        "chat_model": "us.anthropic.claude-sonnet-5",
        "llm_provider": "bedrock",
        "llm_reasoning_effort": "xhigh",
        "llm_utility_model": "us.anthropic.claude-sonnet-5",
        "llm_utility_provider": "bedrock",
        "embedding_model": "bedrock:amazon.titan-embed-text-v2:0",
        "embedding_provider": "bedrock",
        "embedding_dimensions": 1024,
    }


def test_models_reports_all_three_surfaces() -> None:
    models = models_for(_Session())
    assert set(models) == {"agent", "utility", "embedding"}
    assert models["agent"] == {
        "id": "us.anthropic.claude-sonnet-5",
        "provider": "bedrock",
        "effort": "xhigh",
    }
    assert models["utility"]["id"] == "us.anthropic.claude-sonnet-5"
    assert models["utility"]["provider"] == "bedrock"
    # No live utility client on this session, and no knob records the effort, so it is null
    # rather than silently borrowing the agent surface's value.
    assert models["utility"]["effort"] is None


def test_embedding_id_keeps_its_provider_qualifier() -> None:
    """The qualifier is cache-key identity, and the id itself contains a colon.

    Splitting on ``:`` would yield ``amazon.titan-embed-text-v2`` and lose the ``0``, which is a
    different model id — and dropping the qualifier would let two gateways serving one nominal
    id share cached vectors (``retrieve.semantic.cache_key`` is ``model|dimensions|text``).
    """
    embedding = models_for(_Session())["embedding"]
    assert embedding["id"] == "bedrock:amazon.titan-embed-text-v2:0"
    assert embedding["provider"] == "bedrock"
    assert embedding["dimensions"] == 1024


def test_capabilities_carries_models_and_keeps_the_legacy_model_field() -> None:
    """The header chip reads `model`; removing it would blank the chip."""
    caps = capabilities_for(_Session())
    assert caps["model"] == "us.anthropic.claude-sonnet-5"
    assert caps["models"] == models_for(_Session())


def test_models_reports_none_rather_than_omitting_a_surface() -> None:
    """An offline profile resolves no chat model. The keys must still be there.

    Omission is what made three knobs *absent* rather than null from 8,106 measurement rows,
    where a missing key compares equal to itself and the drift gate passes on a configuration it
    never saw (`serve/session.py::_resolved_knobs`). Same hazard, same rule, one surface over.
    """

    class _Offline(_Session):
        agent_model = None
        knobs_resolved: dict[str, Any] = {}

    models = models_for(_Offline())
    assert set(models) == {"agent", "utility", "embedding"}
    assert models["agent"] == {"id": None, "provider": None, "effort": None}
    assert models["embedding"] == {"id": None, "provider": None, "dimensions": None}


# ── the connection projection: it must identify the warehouse and never the credential ──


def test_postgres_endpoint_identifies_the_database() -> None:
    assert PostgresConnector(_DSN).endpoint == {
        "host": "127.0.0.1",
        "port": "5432",
        "database": "facilities",
    }


def test_postgres_endpoint_never_carries_the_credential() -> None:
    """The load-bearing assertion. `user` and `password` are not parsed out at all.

    Asserted over the serialized form, not key-by-key: a future field that happened to embed the
    DSN would pass an `assert "password" not in endpoint` and fail this.
    """
    endpoint = PostgresConnector(_DSN).endpoint
    serialized = repr(endpoint)
    assert "super_secret_pw" not in serialized
    assert "password" not in serialized
    assert "user" not in serialized


def test_an_unparseable_dsn_is_empty_rather_than_raising() -> None:
    """A settings page saying where it points is not worth a 500."""
    assert PostgresConnector("=not a dsn=").endpoint == {}


def test_sqlite_endpoint_is_the_file_name_only() -> None:
    """No host, no port, and not the absolute path — that is this machine's layout."""
    assert SqliteConnector("/srv/data/gbi_demo_sales.db").endpoint == {
        "database": "gbi_demo_sales.db"
    }


def test_connection_for_merges_dialect_with_the_endpoint() -> None:
    class _S(_Session):
        connector = PostgresConnector(_DSN)

    assert connection_for(_S()) == {
        "dialect": "postgres",
        "host": "127.0.0.1",
        "port": "5432",
        "database": "facilities",
    }
    assert "super_secret_pw" not in repr(capabilities_for(_S()))


def test_connection_survives_a_connector_with_no_endpoint() -> None:
    """`capabilities_for` is documented as callable with a partial session; keep it true."""
    assert connection_for(_Session()) == {"dialect": "postgres"}


def test_connection_ignores_a_non_mapping_endpoint() -> None:
    """A test double whose `endpoint` is a Mock must not become a `**` unpacking error."""

    class _S(_Session):
        connector = type("C", (), {"dialect": "postgres", "endpoint": object()})()

    assert connection_for(_S()) == {"dialect": "postgres"}
