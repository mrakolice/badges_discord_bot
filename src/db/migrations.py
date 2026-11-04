import hashlib
import logging
import re
from dataclasses import dataclass

from src.db.base import Database
from src.db.queries import SchemaVersioning
from src.db.templates import TEMPLATE_SUFFIX

log = logging.getLogger(__name__)

MIGRATIONS_DIR = "migrations"
VERSIONS_TABLE = "schema_migrations"
# Arbitrary constant shared by all bot instances so only one applies migrations at a time.
ADVISORY_LOCK_KEY = 7_316_401_022
_FILE_RE = re.compile(r"^(\d{4})_([a-z0-9_]+)$")


class MigrationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    name: str
    template: str
    checksum: str


class MigrationRunner:
    def __init__(self, db: Database) -> None:
        self._db = db
        self._templates = db.templates

    def discover(self) -> list[Migration]:
        migrations: list[Migration] = []
        for path in sorted((self._templates.root / MIGRATIONS_DIR).glob("*" + TEMPLATE_SUFFIX)):
            stem = path.name.removesuffix(TEMPLATE_SUFFIX)
            match = _FILE_RE.fullmatch(stem)
            if match is None:
                raise MigrationError(f"bad migration file name: {path.name}")
            template = f"{MIGRATIONS_DIR}/{stem}"
            checksum = hashlib.sha256(self._templates.source(template).encode()).hexdigest()
            migrations.append(Migration(int(match[1]), match[2], template, checksum))
        versions = [m.version for m in migrations]
        if len(versions) != len(set(versions)):
            raise MigrationError("duplicate migration versions")
        return migrations

    async def apply(self) -> list[Migration]:
        """Applies pending migrations atomically; returns the ones that were applied."""
        migrations = self.discover()
        async with self._db.transaction() as tx:
            await tx.execute(SchemaVersioning.ACQUIRE_LOCK, key=ADVISORY_LOCK_KEY)
            await tx.execute_script(SchemaVersioning.ENSURE_TABLE, table=VERSIONS_TABLE)
            rows = await tx.fetch(SchemaVersioning.LIST_APPLIED, table=VERSIONS_TABLE)
            applied = {row["version"]: row["checksum"] for row in rows}

            pending: list[Migration] = []
            for migration in migrations:
                known = applied.get(migration.version)
                if known is None:
                    pending.append(migration)
                elif known != migration.checksum:
                    raise MigrationError(
                        f"migration {migration.version:04d}_{migration.name} changed after "
                        "being applied; add a new migration instead"
                    )

            for migration in pending:
                log.info("applying migration %04d_%s", migration.version, migration.name)
                await tx.execute_script(migration.template)
                await tx.execute(
                    SchemaVersioning.RECORD_APPLIED,
                    table=VERSIONS_TABLE,
                    version=migration.version,
                    name=migration.name,
                    checksum=migration.checksum,
                )
        return pending
