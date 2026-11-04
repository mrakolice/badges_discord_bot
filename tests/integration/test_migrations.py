import pytest

from src.db import AsyncpgDatabase
from src.db.migrations import MigrationError, MigrationRunner
from tests.integration.queries import TestSql

pytestmark = pytest.mark.asyncio(loop_scope="session")

EXPECTED_TABLES = {
    "public.channels",
    "public.subscriptions",
    "public.campaigns_seen",
    "public.state",
    "public.stream_notifications",
    "migrations_meta.schema_migrations",
}


async def test_initial_migration_creates_tables(db: AsyncpgDatabase, admin: AsyncpgDatabase):
    tables = {r["table_name"] for r in await admin.fetch(TestSql.LIST_TABLES)}
    assert EXPECTED_TABLES <= tables


async def test_rerun_is_noop(db: AsyncpgDatabase) -> None:
    assert await MigrationRunner(db).apply() == []


async def test_changed_migration_is_rejected(db: AsyncpgDatabase, monkeypatch) -> None:
    runner = MigrationRunner(db)
    original = runner._templates.source
    monkeypatch.setattr(runner._templates, "source", lambda name: original(name) + "\n-- edit")
    with pytest.raises(MigrationError):
        await runner.apply()
