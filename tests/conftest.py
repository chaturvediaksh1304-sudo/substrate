import pytest
from sqlalchemy import URL, create_engine, make_url, text
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.db import Base
from app import models  # noqa: F401 - registers tables on Base.metadata

TEST_DB = "substrate_test"


def _test_url() -> URL:
    return make_url(settings.DATABASE_URL).set(database=TEST_DB)


@pytest.fixture(scope="session")
def test_engine():
    admin = create_engine(make_url(settings.DATABASE_URL).set(database="postgres"))
    with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        if not conn.execute(text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": TEST_DB}).scalar():
            conn.execute(text(f'CREATE DATABASE "{TEST_DB}"'))
    admin.dispose()

    engine = create_engine(_test_url())
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def db_session(test_engine):
    """Session against the substrate_test database; tables truncated after each test."""
    session = sessionmaker(bind=test_engine)()
    try:
        yield session
    finally:
        session.close()
        with test_engine.begin() as conn:
            conn.execute(text("TRUNCATE papers, chunks RESTART IDENTITY CASCADE"))
