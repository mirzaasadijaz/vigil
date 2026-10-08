import asyncio
import sys
import os
from logging.config import fileConfig
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config
from alembic import context

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend.core.database import Base
from backend.models import (  # noqa: F401
    alert, attendance, camera, embedding, employee, mess, settings, user,
)

config = context.config

# Take the database URL from the APPLICATION's configuration, not from alembic.ini.
#
# This was a real and expensive bug: alembic.ini carried a hard-coded url pointing at a
# database name that no deployment actually used, and env.py never overrode it. So
# `alembic upgrade heads` connected to the wrong database - or to one that did not exist
# - while the application itself, reading DATABASE_URL from .env, was talking to the real
# one. The migration appeared to "not work"; in fact it was never applied to the database
# anybody was using, and the symptom surfaced much later as
# `column alert_logs.severity does not exist`.
#
# Reading from settings means alembic and the application can never again disagree about
# which database they are managing, and it removes the need to keep a live credential in
# a file that is tracked in git.
#
# The `%` doubling is not decorative: ConfigParser treats a single % as interpolation
# syntax, so a password containing one would raise here instead of connecting.
try:
    from backend.core.config import settings as _app_settings
    if _app_settings.DATABASE_URL:
        config.set_main_option("sqlalchemy.url",
                               _app_settings.DATABASE_URL.replace("%", "%%"))
except Exception as _e:  # pragma: no cover - fall back to whatever the ini says
    print(f"[alembic] could not read DATABASE_URL from settings ({_e}); "
          f"using alembic.ini", flush=True)
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
