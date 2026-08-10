from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine

from app.config import settings
from app.db import Base
from app import models  # noqa: F401 - registers tables on Base.metadata

if context.config.config_file_name:
    fileConfig(context.config.config_file_name)

target_metadata = Base.metadata

# ponytail: online mode only — nothing here needs `alembic --sql` offline output.
with create_engine(settings.DATABASE_URL).connect() as connection:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()
