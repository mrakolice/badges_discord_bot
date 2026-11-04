import asyncio
import logging
import os
import time

import asyncpg

from src.db import AsyncpgDatabase, MigrationRunner, create_asyncpg_database

log = logging.getLogger("migrate")

# Postgres lives in another compose stack, so depends_on can't wait for it.
_TRANSIENT_ERRORS = (OSError, asyncpg.CannotConnectNowError, asyncpg.TooManyConnectionsError)


async def connect_with_retry(db: AsyncpgDatabase, wait_seconds: float) -> None:
    deadline = time.monotonic() + wait_seconds
    delay = 1.0
    while True:
        try:
            await db.connect()
            return
        except _TRANSIENT_ERRORS as exc:
            if time.monotonic() + delay > deadline:
                raise
            log.warning("database is not reachable yet (%s), retrying in %.0fs", exc, delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 10.0)


async def main() -> None:
    db = create_asyncpg_database(os.environ["DATABASE_URL"])
    await connect_with_retry(db, float(os.environ.get("MIGRATE_WAIT_SECONDS", "60")))
    try:
        applied = await MigrationRunner(db).apply()
    finally:
        await db.close()
    if applied:
        log.info("applied: %s", ", ".join(f"{m.version:04d}_{m.name}" for m in applied))
    else:
        log.info("schema is up to date")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    asyncio.run(main())
