from pathlib import Path

from casefile_ai.config import Settings


def test_settings_do_not_require_live_model_credentials(tmp_path: Path) -> None:
    settings = Settings(
        data_dir=tmp_path / "data",
        database_url=f"sqlite:///{(tmp_path / 'test.db').as_posix()}",
        _env_file=None,
    )

    settings.ensure_local_directories()

    assert settings.data_dir.is_dir()
    assert settings.llm_api_key is None


def test_settings_representation_hides_api_key() -> None:
    settings = Settings(llm_api_key="super-secret-value", _env_file=None)

    assert "super-secret-value" not in repr(settings)
