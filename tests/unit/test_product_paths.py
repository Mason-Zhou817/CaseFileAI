"""Stable defaults, explicit legacy paths and no implicit data migration."""

from pathlib import Path

import pytest

from casefile_ai.config import BGE_MODEL, Settings


def test_defaults_are_identical_across_working_directories(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CASEFILE_APP_HOME", str(tmp_path / "application"))
    for name in ("DATA_DIR", "DATABASE_URL", "LOG_DIR"):
        monkeypatch.delenv("CASEFILE_" + name, raising=False)
    before = Settings(_env_file=None)
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.chdir(other)
    after = Settings(_env_file=None)
    assert before.data_dir == after.data_dir == tmp_path / "application" / "data"
    assert before.database_url == after.database_url
    assert before.log_dir == after.log_dir == tmp_path / "application" / "logs"
    assert after.database_path == after.data_dir / "casefile-ai.db"
    assert after.embedding_model == BGE_MODEL
    assert not other.joinpath("data").exists()


def test_relative_explicit_env_paths_remain_at_config_location(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_dir = tmp_path / "legacy"
    config_dir.mkdir()
    env = config_dir / ".env"
    env.write_text(
        "CASEFILE_DATA_DIR=./data\nCASEFILE_DATABASE_URL=sqlite:///./data/casefile-ai.db\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    first = Settings(_env_file=env)
    monkeypatch.chdir(config_dir)
    second = Settings(_env_file=env)
    assert first.data_dir == second.data_dir == config_dir / "data"
    assert first.database_path == second.database_path == config_dir / "data" / "casefile-ai.db"
    assert not first.data_dir.exists()


def test_custom_data_path_automatically_sets_default_database(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, data_dir=tmp_path / "custom")
    assert settings.database_path == tmp_path / "custom" / "casefile-ai.db"
    assert not settings.data_dir.exists()


def test_explicit_env_file_is_independent_of_cwd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = tmp_path / "configuration.env"
    env.write_text("CASEFILE_LLM_MODEL=synthetic-model\n", encoding="utf-8")
    monkeypatch.setenv("CASEFILE_ENV_FILE", str(env))
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    assert Settings().llm_model == "synthetic-model"


def test_environment_overrides_file_and_cloud_is_opt_in(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = tmp_path / ".env"
    env.write_text(
        "CASEFILE_DATA_DIR=./old\nCASEFILE_LLM_API_KEY=synthetic-file-key\n", encoding="utf-8"
    )
    monkeypatch.setenv("CASEFILE_DATA_DIR", str(tmp_path / "override"))
    settings = Settings(_env_file=env)
    assert settings.data_dir == tmp_path / "override"
    assert not settings.llm_enabled
    assert "synthetic-file-key" not in repr(settings)
