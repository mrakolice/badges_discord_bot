import pytest

from src.db import SQL_ROOT, PostgresNumericDialect, SqlTemplates


@pytest.fixture(scope="session")
def templates() -> SqlTemplates:
    return SqlTemplates(SQL_ROOT, PostgresNumericDialect())
