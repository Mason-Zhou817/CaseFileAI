"""Stable local paths and optional, explicitly enabled model configuration."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url

SOURCE_ROOT = Path(__file__).resolve().parents[2]
BGE_MODEL = "BAAI/bge-small-zh-v1.5"
BGE_REVISION = "4e17e244a0fb63bfb78fca8fcf95079fcc664f5c"


def default_app_home() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
        return base / "CaseFileAI"
    return (
        Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))) / "casefile-ai"
    )


def configuration_file() -> Path:
    """Never discover configuration using the caller's working directory."""
    if os.environ.get("CASEFILE_PRODUCT_MODE") == "1":
        home = Path(os.environ.get("CASEFILE_APP_HOME", str(default_app_home()))).expanduser()
        home = (home if home.is_absolute() else default_app_home().parent / home).resolve()
        configured = Path(
            os.environ.get("CASEFILE_ENV_FILE", str(home / "config.env"))
        ).expanduser()
        return configured if configured.is_absolute() else home / configured
    base = SOURCE_ROOT if (SOURCE_ROOT / "pyproject.toml").is_file() else default_app_home()
    configured = Path(os.environ.get("CASEFILE_ENV_FILE", str(base / ".env"))).expanduser()
    return configured if configured.is_absolute() else base / configured


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CASEFILE_", extra="ignore")

    app_home: Path = Field(default_factory=default_app_home)
    config_dir: Path = Field(default_factory=lambda: configuration_file().parent, repr=False)
    data_dir: Path = Path("data")
    database_url: str = Field(default="", repr=False)
    log_dir: Path = Path("logs")
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    llm_enabled: bool = False
    llm_base_url: str | None = Field(default=None, repr=False)
    llm_api_key: str | None = Field(default=None, repr=False)
    llm_model: str | None = None
    llm_timeout_seconds: float = Field(default=60.0, gt=0, le=600)
    llm_structured_output_mode: Literal["json_schema", "json_object"] = "json_schema"
    llm_max_validation_attempts: int = Field(default=3, ge=1, le=5)
    embedding_model: str = BGE_MODEL
    embedding_revision: str | None = BGE_REVISION
    embedding_device: str = "cpu"
    embedding_query_instruction: str = ""

    def __init__(self, **values: Any) -> None:
        env_file = values.pop("_env_file", configuration_file())
        if isinstance(env_file, (str, Path)):
            env_path = Path(env_file).expanduser()
            if not env_path.is_absolute():
                env_path = configuration_file().parent / env_path
            env_file = env_path
            values.setdefault("config_dir", env_path.parent)
        super().__init__(_env_file=env_file, _env_file_encoding="utf-8", **values)

    @model_validator(mode="after")
    def stable_paths(self) -> Settings:
        self.config_dir = self._absolute(self.config_dir, configuration_file().parent)
        self.app_home = self._absolute(self.app_home, default_app_home().parent)
        self.data_dir = (
            self._absolute(self.data_dir, self.config_dir)
            if "data_dir" in self.model_fields_set
            else self.app_home / "data"
        )
        self.log_dir = (
            self._absolute(self.log_dir, self.config_dir)
            if "log_dir" in self.model_fields_set
            else self.app_home / "logs"
        )
        if not self.database_url:
            self.database_url = f"sqlite:///{(self.data_dir / 'casefile-ai.db').as_posix()}"
        else:
            url = make_url(self.database_url)
            if url.get_backend_name() != "sqlite":
                raise ValueError("a local SQLite database is required")
            if url.database and url.database != ":memory:":
                path = self._absolute(Path(url.database), self.config_dir)
                self.database_url = url.set(database=path.as_posix()).render_as_string()
        for field in ("llm_base_url", "llm_api_key", "llm_model"):
            value = getattr(self, field)
            setattr(self, field, value.strip() or None if value is not None else None)
        self.embedding_model = self.embedding_model.strip() or BGE_MODEL
        if self.embedding_model != BGE_MODEL and "embedding_revision" not in self.model_fields_set:
            self.embedding_revision = None
        return self

    @staticmethod
    def _absolute(path: Path, base: Path) -> Path:
        path = path.expanduser()
        return (path if path.is_absolute() else base / path).resolve()

    @property
    def llm_configured(self) -> bool:
        return bool(self.llm_base_url and self.llm_api_key and self.llm_model)

    @property
    def database_path(self) -> Path | None:
        name = make_url(self.database_url).database
        return Path(name) if name and name != ":memory:" else None

    def ensure_local_directories(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        if self.database_path is not None:
            self.database_path.parent.mkdir(parents=True, exist_ok=True)
