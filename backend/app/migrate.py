"""Apply schema before seeding, then exit successfully."""
from pathlib import Path
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine
from app.core.runtime_config import database_url
from app.core.seed import seed

def main():
    command.upgrade(Config(str(Path(__file__).resolve().parents[1] / 'alembic.ini')), 'head')
    engine = create_engine(database_url())
    try:
        seed(engine)
    finally:
        engine.dispose()

if __name__ == '__main__':
    main()
