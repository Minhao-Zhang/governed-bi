"""Tests for the project config: ModelConfig + governed_bi.toml loading."""

from __future__ import annotations

from pathlib import Path

import pytest

from governed_bi.config import Environment, ModelConfig, Settings, load_settings

REPO_ROOT = Path(__file__).resolve().parents[1]


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


# --------------------------------------------------------------------------- #
# load_settings
# --------------------------------------------------------------------------- #


def test_load_project_config_file():
    """The committed governed_bi.toml carries the project's model decision."""
    settings = load_settings(REPO_ROOT / "governed_bi.toml")
    assert settings.environment is Environment.dev
    assert settings.models.llm_model == "gpt-5.5"
    assert settings.models.llm_reasoning_effort == "low"
    assert settings.models.embedding_model == "text-embedding-3-small"
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


def test_env_var_override_locates_config(tmp_path, monkeypatch):
    cfg = tmp_path / "custom.toml"
    cfg.write_text('[models]\nllm_model = "gpt-env"\n', encoding="utf-8")
    monkeypatch.setenv("GOVERNED_BI_CONFIG", str(cfg))
    settings = load_settings()
    assert settings.models.llm_model == "gpt-env"
