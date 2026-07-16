"""
migrations/env.py — Alembic Migration Environment
Database schema version control।
"""

from logging.config import fileConfig
from sqlalchemy import create_engine, pool
from alembic import context
import os

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

DATABASE_URL = os.environ.get("DATABASE_URL", "")
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# Fix: DATABASE_URL is passed straight to create_engine() below, never
# through config.set_main_option()/get_main_option() — those round-trip
# through Python's ConfigParser, which treats a literal "%" as the start
# of its own interpolation syntax and crashes on any password containing
# a URL-encoded special character (e.g. "%40" for "@").
target_metadata = None


def run_migrations_offline():
    context.configure(url=DATABASE_URL, target_metadata=target_metadata,
                      literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    connectable = create_engine(DATABASE_URL, poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
