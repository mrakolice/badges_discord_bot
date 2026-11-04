import os
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio

from src.db import AsyncpgDatabase, PostgresNumericDialect, SqlTemplates, create_asyncpg_database
from src.db.migrations import MigrationRunner
from tests.integration.queries import TestSql

TEST_SQL_ROOT = Path(__file__).resolve().parents[1] / "sql"
DSN = os.environ.get("TEST_DATABASE_URL")


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if Path(__file__).parent not in item.path.parents:
            continue
        item.add_marker(pytest.mark.integration)
        if DSN is None:
            item.add_marker(pytest.mark.skip(reason="TEST_DATABASE_URL is not set"))


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def admin() -> AsyncIterator[AsyncpgDatabase]:
    """Connection that runs test-only templates from tests/sql."""
    assert DSN is not None
    db = AsyncpgDatabase(DSN, SqlTemplates(TEST_SQL_ROOT, PostgresNumericDialect()))
    await db.connect()
    await db.execute_script(TestSql.RESET_SCHEMA)
    yield db
    await db.close()


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def db(admin: AsyncpgDatabase) -> AsyncIterator[AsyncpgDatabase]:
    assert DSN is not None
    database = create_asyncpg_database(DSN)
    await database.connect()
    await MigrationRunner(database).apply()
    yield database
    await database.close()


@pytest_asyncio.fixture(loop_scope="session")
async def clean_db(db: AsyncpgDatabase, admin: AsyncpgDatabase) -> AsyncpgDatabase:
    await admin.execute_script(TestSql.TRUNCATE_ALL)
    return db
