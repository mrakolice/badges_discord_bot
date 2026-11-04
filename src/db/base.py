from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from typing import Any

from src.db.queries import Query
from src.db.templates import SqlTemplateError, SqlTemplates

Row = Mapping[str, Any]


class QueryExecutor(ABC):
    """Runs SQL templates named by `Query` enum members (see src/db/queries.py)."""

    def __init__(self, templates: SqlTemplates) -> None:
        self.templates = templates

    async def fetch(self, query: Query, /, **params: Any) -> list[Row]:
        rendered = self.templates.render(query, **params)
        return await self._fetch(rendered.sql, rendered.args)

    async def fetchrow(self, query: Query, /, **params: Any) -> Row | None:
        rendered = self.templates.render(query, **params)
        return await self._fetchrow(rendered.sql, rendered.args)

    async def fetchval(self, query: Query, /, **params: Any) -> Any:
        rendered = self.templates.render(query, **params)
        return await self._fetchval(rendered.sql, rendered.args)

    async def execute(self, query: Query, /, **params: Any) -> None:
        rendered = self.templates.render(query, **params)
        await self._execute(rendered.sql, rendered.args)

    async def execute_script(self, query: Query | str, /, **params: Any) -> None:
        """Runs a multi-statement template; `str` only for migrations discovered on disk."""
        rendered = self.templates.render(query, **params)
        if rendered.args:
            raise SqlTemplateError(f"script {query!r} must not use the `bind` filter")
        await self._execute_script(rendered.sql)

    @abstractmethod
    async def _fetch(self, sql: str, args: Sequence[Any]) -> list[Row]: ...

    @abstractmethod
    async def _fetchrow(self, sql: str, args: Sequence[Any]) -> Row | None: ...

    @abstractmethod
    async def _fetchval(self, sql: str, args: Sequence[Any]) -> Any: ...

    @abstractmethod
    async def _execute(self, sql: str, args: Sequence[Any]) -> None: ...

    @abstractmethod
    async def _execute_script(self, sql: str) -> None: ...


class Database(QueryExecutor):
    @abstractmethod
    async def connect(self, *, wait_seconds: float = 0) -> None:
        """Opens the connection(s), retrying transient failures for up to `wait_seconds`."""

    @abstractmethod
    async def close(self) -> None: ...

    @abstractmethod
    def transaction(self) -> AbstractAsyncContextManager[QueryExecutor]: ...
