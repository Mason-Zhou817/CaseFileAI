from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect

from casefile_ai.infrastructure.db.models import Base

ROOT = Path(__file__).resolve().parents[2]


def test_migrated_schema_matches_models(tmp_path: Path) -> None:
    url = f"sqlite:///{(tmp_path / 'schema.db').as_posix()}"
    command.upgrade(_config(url), "head")
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            context = MigrationContext.configure(
                connection,
                opts={
                    "include_name": lambda name, type_, _parents: (
                        not (type_ == "table" and name is not None and name.startswith("chunk_fts"))
                    )
                },
            )
            assert compare_metadata(context, Base.metadata) == []
    finally:
        engine.dispose()


def _config(database_url: str) -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def test_initial_migration_upgrades_and_downgrades(tmp_path: Path) -> None:
    database_url = f"sqlite:///{(tmp_path / 'migration.db').as_posix()}"
    config = _config(database_url)

    command.upgrade(config, "head")
    engine = create_engine(database_url)
    assert {"alembic_version", "cases"}.issubset(inspect(engine).get_table_names())
    columns = {column["name"] for column in inspect(engine).get_columns("cases")}
    assert columns == {
        "case_id",
        "name",
        "description",
        "status",
        "created_at",
        "updated_at",
    }

    engine.dispose()
    command.downgrade(config, "base")
    engine = create_engine(database_url)
    assert "cases" not in inspect(engine).get_table_names()
    engine.dispose()
