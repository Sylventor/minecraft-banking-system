from __future__ import annotations

import os
from logging.config import fileConfig
from alembic import context
from sqlalchemy import engine_from_config, pool

config = context.config
config.set_main_option("sqlalchemy.url", os.environ["FINANCE_DATABASE_URL"].replace("postgresql://", "postgresql+psycopg://").replace("%", "%%"))
if config.config_file_name:
    fileConfig(config.config_file_name)

def run_migrations_offline() -> None:
    context.configure(url=config.get_main_option("sqlalchemy.url"), version_table_schema="finance", literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()

def run_migrations_online() -> None:
    engine = engine_from_config(config.get_section(config.config_ini_section, {}), prefix="sqlalchemy.", poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, version_table_schema="finance", include_schemas=True)
        with context.begin_transaction():
            context.run_migrations()

if context.is_offline_mode(): run_migrations_offline()
else: run_migrations_online()
