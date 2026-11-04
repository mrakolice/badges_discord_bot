from pathlib import Path

from src.db.asyncpg_backend import AsyncpgDatabase
from src.db.base import Database, QueryExecutor, Row
from src.db.migrations import MigrationRunner
from src.db.templates import Dialect, PostgresNumericDialect, SqlTemplates

SQL_ROOT = Path(__file__).resolve().parents[2] / "sql"


def create_asyncpg_database(dsn: str, sql_root: Path = SQL_ROOT) -> AsyncpgDatabase:
    return AsyncpgDatabase(dsn, SqlTemplates(sql_root, PostgresNumericDialect()))


__all__ = [
    "SQL_ROOT",
    "AsyncpgDatabase",
    "Database",
    "Dialect",
    "MigrationRunner",
    "PostgresNumericDialect",
    "QueryExecutor",
    "Row",
    "SqlTemplates",
    "create_asyncpg_database",
]
