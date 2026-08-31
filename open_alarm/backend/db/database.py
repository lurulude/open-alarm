from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from sqlite3 import Connection, complete_statement
from sqlite3 import connect as sqlite_connect

MIGRATIONS_DIR = Path(__file__).with_name("migrations")


class DatabaseIntegrityError(RuntimeError):
    pass


class DatabaseCheckpointError(RuntimeError):
    pass


def connect(path: str | Path) -> Connection:
    connection = sqlite_connect(path)
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA synchronous = FULL")
    connection.execute("PRAGMA busy_timeout = 5000")
    connection.execute("PRAGMA wal_autocheckpoint = 1000")
    return connection


def verify_integrity(connection: Connection, *, full: bool = False) -> None:
    pragma = "integrity_check" if full else "quick_check"
    rows = [str(row[0]) for row in connection.execute(f"PRAGMA {pragma}").fetchall()]
    if rows != ["ok"]:
        detail = "; ".join(rows) if rows else "no result"
        raise DatabaseIntegrityError(f"SQLite {pragma} failed: {detail}")


def checkpoint_wal(connection: Connection, *, truncate: bool = False) -> tuple[int, int, int]:
    mode = "TRUNCATE" if truncate else "PASSIVE"
    row = connection.execute(f"PRAGMA wal_checkpoint({mode})").fetchone()
    if row is None or len(row) != 3:
        raise DatabaseCheckpointError("SQLite WAL checkpoint returned an invalid result")
    result = (int(row[0]), int(row[1]), int(row[2]))
    if result[0] != 0:
        raise DatabaseCheckpointError(
            f"SQLite WAL checkpoint remained busy: log={result[1]} checkpointed={result[2]}"
        )
    return result


def apply_migrations(
    connection: Connection,
    migrations_dir: Path = MIGRATIONS_DIR,
) -> list[int]:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migration (
            version INTEGER PRIMARY KEY,
            applied_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
        )
        """
    )

    applied = {
        row[0]
        for row in connection.execute("SELECT version FROM schema_migration ORDER BY version")
    }
    newly_applied: list[int] = []

    for path in sorted(migrations_dir.glob("[0-9][0-9][0-9][0-9]_*.sql")):
        version = int(path.name.split("_", maxsplit=1)[0])
        if version in applied:
            continue

        statements = tuple(_sql_statements(path.read_text(encoding="utf-8")))
        try:
            connection.execute("BEGIN IMMEDIATE")
            for statement in statements:
                connection.execute(statement)
            connection.execute("INSERT INTO schema_migration(version) VALUES (?)", (version,))
            connection.commit()
        except Exception:
            connection.rollback()
            raise

        newly_applied.append(version)
        applied.add(version)

    return newly_applied


def _sql_statements(script: str) -> Iterator[str]:
    buffer = ""
    for line in script.splitlines(keepends=True):
        buffer += line
        if complete_statement(buffer):
            statement = buffer.strip()
            if statement:
                yield statement
            buffer = ""

    if buffer.strip():
        raise ValueError("migration contains an incomplete SQL statement")
