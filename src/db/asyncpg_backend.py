import json
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import Any

import asyncpg

from src.db.base import Database, QueryExecutor, Row
from src.db.templates import SqlTemplates

type _Conn = asyncpg.Connection | asyncpg.Pool


def normalize_dsn(dsn: str) -> str:
    return dsn.replace("postgresql+asyncpg://", "postgresql://", 1)


async def _init_connection(conn: asyncpg.Connection) -> None:
    for type_name in ("json", "jsonb"):
        await conn.set_type_codec(
            type_name, encoder=json.dumps, decoder=json.loads, schema="pg_catalog"
        )


class _AsyncpgExecutor(QueryExecutor):
    def __init__(self, templates: SqlTemplates, conn: _Conn) -> None:
        super().__init__(templates)
        self._conn = conn

    async def _fetch(self, sql: str, args: Sequence[Any]) -> list[Row]:
        return [dict(r) for r in await self._conn.fetch(sql, *args)]

    async def _fetchrow(self, sql: str, args: Sequence[Any]) -> Row | None:
        record = await self._conn.fetchrow(sql, *args)
        return dict(record) if record is not None else None

    async def _fetchval(self, sql: str, args: Sequence[Any]) -> Any:
        return await self._conn.fetchval(sql, *args)

    async def _execute(self, sql: str, args: Sequence[Any]) -> None:
        await self._conn.execute(sql, *args)

    async def _execute_script(self, sql: str) -> None:
        # Without args asyncpg uses the simple query protocol, which allows many statements.
        await self._conn.execute(sql)


class AsyncpgDatabase(Database):
    def __init__(
        self, dsn: str, templates: SqlTemplates, *, min_size: int = 1, max_size: int = 10
    ) -> None:
        super().__init__(templates)
        self._dsn = normalize_dsn(dsn)
        self._min_size = min_size
        self._max_size = max_size
        self._pool: asyncpg.Pool | None = None

    async def connect(self) -> None:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(
                self._dsn, min_size=self._min_size, max_size=self._max_size, init=_init_connection
            )

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[QueryExecutor]:
        async with self._require_pool().acquire() as conn, conn.transaction():
            yield _AsyncpgExecutor(self.templates, conn)

    def _executor(self) -> _AsyncpgExecutor:
        return _AsyncpgExecutor(self.templates, self._require_pool())

    def _require_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            raise RuntimeError("AsyncpgDatabase.connect() was not called")
        return self._pool

    async def _fetch(self, sql: str, args: Sequence[Any]) -> list[Row]:
        return await self._executor()._fetch(sql, args)

    async def _fetchrow(self, sql: str, args: Sequence[Any]) -> Row | None:
        return await self._executor()._fetchrow(sql, args)

    async def _fetchval(self, sql: str, args: Sequence[Any]) -> Any:
        return await self._executor()._fetchval(sql, args)

    async def _execute(self, sql: str, args: Sequence[Any]) -> None:
        await self._executor()._execute(sql, args)

    async def _execute_script(self, sql: str) -> None:
        await self._executor()._execute_script(sql)
