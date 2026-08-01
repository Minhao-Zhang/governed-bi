"""Tests for the project config: TOML policy + .env secrets."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from governed_bi.config import (
    DataSourceConfig,
    Environment,
    ModelConfig,
    Settings,
    load_dotenv,
    load_settings,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

# Env vars these tests set (directly, via load_dotenv, or monkeypatch).
_TOUCHED_ENV = (
    "OPENAI_API_KEY",
    "MY_KEY",
    "SINGLE",
)


@pytest.fixture(autouse=True)
def _restore_touched_env():
    saved = {k: os.environ.get(k) for k in _TOUCHED_ENV}
    yield
    for key, value in saved.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


# --------------------------------------------------------------------------- #
# ModelConfig
# --------------------------------------------------------------------------- #


def test_model_config_defaults_are_the_project_decision():
    m = ModelConfig()
    assert m.provider == "openai"
    assert m.llm_model == "gpt-5.6-luna"
    assert m.llm_reasoning_effort == "low"
    assert m.embedding_model == "text-embedding-3-small"
    assert m.api_key_env == "OPENAI_API_KEY"


def test_api_key_reads_env(monkeypatch):
    m = ModelConfig(api_key_env="MY_KEY")
    monkeypatch.delenv("MY_KEY", raising=False)
    assert m.api_key() is None
    monkeypatch.setenv("MY_KEY", "sk-test")
    assert m.api_key() == "sk-test"


def test_settings_carries_a_default_model_config():
    settings = Settings.for_env(Environment.dev)
    assert settings.models == ModelConfig()
    assert settings.corpus_root == "corpus"
    # for_env(dev) opts in to file-write for local demo; the dataclass / TOML
    # safe default is False (see test_allow_edit_defaults).
    assert settings.allow_edit is True
    assert settings.serve_api_key_env is None
    assert settings.cors_origins == ("http://localhost:3000",)


def test_allow_edit_defaults():
    # Safe field / prod default is False; only for_env(dev) without an override
    # opts in so a forgotten TOML key cannot silently enable writes in prod.
    assert Settings.for_env(Environment.prod).allow_edit is False
    assert Settings.for_env(Environment.dev).allow_edit is True
    assert Settings.for_env(Environment.dev, allow_edit=False).allow_edit is False


# --------------------------------------------------------------------------- #
# load_settings
# --------------------------------------------------------------------------- #


def test_load_project_config_file():
    """The committed governed_bi.toml carries the project's model decision."""
    settings = load_settings(REPO_ROOT / "governed_bi.toml", apply_local=False)
    assert settings.environment is Environment.dev
    assert settings.models.llm_model == "gpt-5.6-luna"
    assert settings.models.llm_reasoning_effort == "low"
    # -3-large since d407d19: the embedding channel IS the schema router here, and
    # its recall@3 (0.70, against BM25's 0.35) decides which corpus the analyst ever
    # sees. That commit moved governed_bi.toml and left this assertion on the old
    # value, so the suite was red at HEAD.
    assert settings.models.embedding_model == "text-embedding-3-large"
    assert settings.corpus_root == "corpus"
    assert settings.datasource.kind == "sqlite"
    assert settings.can_stream is False
    assert settings.allow_edit is False  # committed [serve].allow_edit = false
    assert settings.serve_api_key_env is None
    assert settings.cors_origins == ("http://localhost:3000",)
    # dev toggles come from for_env, not the file.
    assert settings.hard_block_suspect_columns is True


def test_missing_file_falls_back_to_dev_defaults(tmp_path):
    settings = load_settings(tmp_path / "does_not_exist.toml")
    assert settings.environment is Environment.dev
    assert settings.models == ModelConfig()


def test_prod_env_and_custom_models(tmp_path):
    cfg = tmp_path / "governed_bi.toml"
    cfg.write_text(
        "\n".join(
            [
                "[runtime]",
                'environment = "prod"',
                "[models]",
                'provider = "openai"',
                'llm_model = "gpt-5.5-mini"',
                'llm_reasoning_effort = "medium"',
                'embedding_model = "text-embedding-3-large"',
            ]
        ),
        encoding="utf-8",
    )
    settings = load_settings(cfg)
    assert settings.environment is Environment.prod
    assert settings.hard_block_suspect_columns is False  # prod default
    assert settings.allow_edit is False  # prod default
    assert settings.models.llm_model == "gpt-5.5-mini"
    assert settings.models.embedding_model == "text-embedding-3-large"


def test_unknown_model_key_is_ignored(tmp_path):
    """A forward-compatible file must not crash an older build."""
    cfg = tmp_path / "governed_bi.toml"
    cfg.write_text(
        '[models]\nllm_model = "gpt-6"\nfuture_flag = true\n',
        encoding="utf-8",
    )
    settings = load_settings(cfg)
    assert settings.models.llm_model == "gpt-6"


def test_runtime_toggle_override(tmp_path):
    """A [runtime] toggle overrides the env default without changing the env."""
    cfg = tmp_path / "governed_bi.toml"
    cfg.write_text(
        "[runtime]\nenvironment = \"dev\"\nhard_block_suspect_columns = false\n",
        encoding="utf-8",
    )
    settings = load_settings(cfg)
    assert settings.environment is Environment.dev
    assert settings.hard_block_suspect_columns is False


def test_local_overlay_merges_and_wins(tmp_path):
    cfg = tmp_path / "governed_bi.toml"
    cfg.write_text(
        "\n".join(
            [
                "[paths]",
                'corpus_root = "corpus"',
                "[datasource]",
                'kind = "sqlite"',
                "[serve]",
                "can_stream = false",
            ]
        ),
        encoding="utf-8",
    )
    (tmp_path / "governed_bi.local.toml").write_text(
        "\n".join(
            [
                "[paths]",
                'corpus_root = "../BIRD-corpus"',
                "[datasource]",
                'kind = "postgres"',
                'dsn_env = "PG_RENAME_DECOY_DSN"',
                "[serve]",
                "can_stream = true",
            ]
        ),
        encoding="utf-8",
    )
    settings = load_settings(cfg, apply_local=True)
    assert settings.corpus_root == "../BIRD-corpus"
    assert settings.datasource.kind == "postgres"
    assert settings.datasource.dsn_env == "PG_RENAME_DECOY_DSN"
    assert settings.can_stream is True


def test_apply_local_false_skips_overlay(tmp_path):
    cfg = tmp_path / "governed_bi.toml"
    cfg.write_text('[datasource]\nkind = "sqlite"\n', encoding="utf-8")
    (tmp_path / "governed_bi.local.toml").write_text(
        '[datasource]\nkind = "postgres"\n',
        encoding="utf-8",
    )
    assert load_settings(cfg, apply_local=False).datasource.kind == "sqlite"


def test_paths_and_serve_tables(tmp_path):
    cfg = tmp_path / "governed_bi.toml"
    cfg.write_text(
        "\n".join(
            [
                "[paths]",
                'corpus_root = "../BIRD-corpus"',
                "[serve]",
                "can_stream = true",
                "allow_edit = false",
                'api_key_env = "GOVERNED_BI_API_KEY"',
                'cors_origins = ["https://app.example.com", "http://localhost:3000"]',
            ]
        ),
        encoding="utf-8",
    )
    settings = load_settings(cfg)
    assert settings.corpus_root == "../BIRD-corpus"
    assert settings.can_stream is True
    assert settings.allow_edit is False
    assert settings.serve_api_key_env == "GOVERNED_BI_API_KEY"
    assert settings.cors_origins == ("https://app.example.com", "http://localhost:3000")


def test_serve_api_key_reads_named_env(monkeypatch):
    settings = Settings.for_env(Environment.dev, serve_api_key_env="GOVERNED_BI_API_KEY")
    monkeypatch.delenv("GOVERNED_BI_API_KEY", raising=False)
    assert settings.serve_api_key() is None
    monkeypatch.setenv("GOVERNED_BI_API_KEY", "shared-secret")
    assert settings.serve_api_key() == "shared-secret"

def test_datasource_db_default_and_toml_override(tmp_path):
    assert DataSourceConfig().db == "main"
    cfg = tmp_path / "governed_bi.toml"
    cfg.write_text(
        "\n".join(
            [
                "[datasource]",
                'kind = "sqlite"',
                'db = "lake_a"',
                'corpus_pin = "beer_factory"',
            ]
        ),
        encoding="utf-8",
    )
    settings = load_settings(cfg)
    assert settings.datasource.db == "lake_a"
    assert settings.datasource.corpus_pin == "beer_factory"


def test_logging_table_checkpointer_knobs(tmp_path):
    cfg = tmp_path / "governed_bi.toml"
    cfg.write_text(
        "\n".join(
            [
                "[logging]",
                'conversation_checkpointer_kind = "postgres"',
                'conversation_checkpointer_path = "unused.sqlite"',
                'conversation_checkpointer_dsn_env = "CHECKPOINT_DSN"',
                'run_log_kind = "jsonl"',
                'run_log_path = "data/logs/runs.jsonl"',
            ]
        ),
        encoding="utf-8",
    )
    settings = load_settings(cfg)
    assert settings.conversation_checkpointer_kind == "postgres"
    assert settings.conversation_checkpointer_path == "unused.sqlite"
    assert settings.conversation_checkpointer_dsn_env == "CHECKPOINT_DSN"
    assert settings.run_log_kind == "jsonl"
    assert settings.run_log_path == "data/logs/runs.jsonl"


def test_settings_checkpointer_defaults():
    settings = Settings.for_env(Environment.dev)
    assert settings.conversation_checkpointer_kind == "sqlite"
    assert settings.conversation_checkpointer_path == (
        "data/checkpoints/conversations.sqlite"
    )
    assert settings.conversation_checkpointer_dsn_env is None


# --------------------------------------------------------------------------- #
# load_dotenv
# --------------------------------------------------------------------------- #


def test_dotenv_fills_unset_variable(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("OPENAI_API_KEY=sk-from-dotenv\n", encoding="utf-8")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    applied = load_dotenv(env)
    assert applied == {"OPENAI_API_KEY": "sk-from-dotenv"}
    assert ModelConfig().api_key() == "sk-from-dotenv"


def test_real_env_var_wins_over_dotenv(tmp_path, monkeypatch):
    """A variable already set in the environment is never overridden by .env."""
    env = tmp_path / ".env"
    env.write_text("OPENAI_API_KEY=sk-from-dotenv\n", encoding="utf-8")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-shell")
    applied = load_dotenv(env)
    assert applied == {}
    assert ModelConfig().api_key() == "sk-from-shell"


def test_dotenv_override_flag_replaces_set_variable(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("OPENAI_API_KEY=sk-from-dotenv\n", encoding="utf-8")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-shell")
    load_dotenv(env, override=True)
    assert ModelConfig().api_key() == "sk-from-dotenv"


def test_dotenv_missing_file_is_noop(tmp_path):
    assert load_dotenv(tmp_path / ".env") == {}


def test_dotenv_parses_comments_quotes_and_export(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text(
        "\n".join(
            [
                "# a comment line",
                "",
                'export OPENAI_API_KEY="sk-quoted"',
                "PG_RENAME_DECOY_DSN=host=127.0.0.1  # inline comment",
                "SINGLE='value'",
            ]
        ),
        encoding="utf-8",
    )
    for k in ("OPENAI_API_KEY", "PG_RENAME_DECOY_DSN", "SINGLE"):
        monkeypatch.delenv(k, raising=False)
    applied = load_dotenv(env)
    assert applied == {
        "OPENAI_API_KEY": "sk-quoted",
        "PG_RENAME_DECOY_DSN": "host=127.0.0.1",
        "SINGLE": "value",
    }


# --------------------------------------------------------------------------- #
# [routing]: the knobs the pooled benchmark varies.
#
# These three were reachable only through the eval CLI, so the benchmark ran
# shortlist@10 WITH the LLM pick while every deployment ran the dataclass defaults
# — shortlist@3, no pick — with no way to configure otherwise. A benchmark result
# then described a configuration no deployment could run, which makes "this improves
# the end result" unfalsifiable in the direction that matters.
# --------------------------------------------------------------------------- #


def test_routing_table_reaches_settings(tmp_path):
    cfg = tmp_path / "governed_bi.toml"
    cfg.write_text(
        "[routing]\ntop_k = 10\nllm_pick = true\npick_max_columns = 20\n",
        encoding="utf-8",
    )
    settings = load_settings(cfg)
    assert settings.schema_route_top_k == 10
    assert settings.schema_route_llm_pick is True
    assert settings.schema_pick_max_columns == 20


def test_routing_defaults_stand_when_the_table_is_absent(tmp_path):
    """The deployment defaults are a deliberate choice, not an accident of the
    loader, so an absent table must not change them."""
    cfg = tmp_path / "governed_bi.toml"
    cfg.write_text('[models]\nllm_model = "gpt-6"\n', encoding="utf-8")
    settings = load_settings(cfg)
    assert settings.schema_route_top_k == 3
    assert settings.schema_route_llm_pick is False
    assert settings.schema_pick_max_columns == 12


def test_a_partial_routing_table_leaves_the_rest_alone(tmp_path):
    """Setting one knob must not silently reset the others to their defaults — that
    would make a config file's meaning depend on which keys it happens to omit."""
    cfg = tmp_path / "governed_bi.toml"
    cfg.write_text("[routing]\nllm_pick = true\n", encoding="utf-8")
    settings = load_settings(cfg)
    assert settings.schema_route_llm_pick is True
    assert settings.schema_route_top_k == 3
    assert settings.schema_pick_max_columns == 12


def test_routing_llm_pick_is_read_as_a_bool_not_a_truthy_int(tmp_path):
    """`top_k` and `pick_max_columns` are ints and `llm_pick` is a bool. Coercing
    them all the same way would put `1` in a bool field, which then serialises into
    the run manifest as `1` and compares unequal to the `True` another run recorded
    — two identical configurations reading as incomparable."""
    cfg = tmp_path / "governed_bi.toml"
    cfg.write_text("[routing]\nllm_pick = true\ntop_k = 7\n", encoding="utf-8")
    settings = load_settings(cfg)
    assert settings.schema_route_llm_pick is True
    assert isinstance(settings.schema_route_llm_pick, bool)
    assert isinstance(settings.schema_route_top_k, int)
    assert settings.schema_route_top_k == 7
