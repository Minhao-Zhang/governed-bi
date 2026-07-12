"""Tests for the project config: TOML policy + .env secrets."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from governed_bi.config import (
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
    assert m.llm_model == "gpt-5.5"
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
    assert settings.allow_edit is True
    assert settings.cors_origins == ("http://localhost:3000",)


# --------------------------------------------------------------------------- #
# load_settings
# --------------------------------------------------------------------------- #


def test_load_project_config_file():
    """The committed governed_bi.toml carries the project's model decision."""
    settings = load_settings(REPO_ROOT / "governed_bi.toml", apply_local=False)
    assert settings.environment is Environment.dev
    assert settings.models.llm_model == "gpt-5.5"
    assert settings.models.llm_reasoning_effort == "low"
    assert settings.models.embedding_model == "text-embedding-3-small"
    assert settings.corpus_root == "corpus"
    assert settings.datasource.kind == "sqlite"
    assert settings.can_stream is False
    assert settings.allow_edit is True
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
                'cors_origins = ["https://app.example.com", "http://localhost:3000"]',
            ]
        ),
        encoding="utf-8",
    )
    settings = load_settings(cfg)
    assert settings.corpus_root == "../BIRD-corpus"
    assert settings.can_stream is True
    assert settings.allow_edit is False
    assert settings.cors_origins == ("https://app.example.com", "http://localhost:3000")


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
