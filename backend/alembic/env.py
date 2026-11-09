from alembic import context
from sqlalchemy import create_engine
from app.core.catalog import metadata
from app.core.runtime_config import database_url

engine = create_engine(database_url(), pool_pre_ping=True)
with engine.connect() as connection:
    context.configure(connection=connection, target_metadata=metadata)
    with context.begin_transaction():
        context.run_migrations()
engine.dispose()
