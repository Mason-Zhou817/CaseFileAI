"""Cwd-independent migration resource discovery shared by startup and diagnostics."""

from importlib.resources import files
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from casefile_ai.config import SOURCE_ROOT, Settings


def migration_config(settings: Settings) -> Config:
    # Checkout migrations have one canonical source; the build includes them in the wheel.
    bundled = Path(str(files("casefile_ai") / "resources/migrations"))
    scripts = bundled if bundled.is_dir() else SOURCE_ROOT / "migrations"
    config = Config()
    config.set_main_option("script_location", str(scripts).replace("%", "%%"))
    config.set_main_option("sqlalchemy.url", settings.database_url.replace("%", "%%"))
    config.attributes["configure_logger"] = False
    return config


def expected_revision(settings: Settings) -> str | None:
    return ScriptDirectory.from_config(migration_config(settings)).get_current_head()


def migrate_database(settings: Settings) -> None:
    settings.ensure_local_directories()
    command.upgrade(migration_config(settings), "head")
