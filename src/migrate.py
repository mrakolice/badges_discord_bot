import asyncio
import logging
import os

from src.db import MigrationRunner, create_asyncpg_database

log = logging.getLogger("migrate")


async def main() -> None:
    db = create_asyncpg_database(os.environ["DATABASE_URL"])
    await db.connect()
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
