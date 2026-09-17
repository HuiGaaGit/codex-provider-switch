from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .constants import BACKUP_DIRECTORY_NAME


class ThreadStateError(RuntimeError):
    pass


@dataclass(slots=True)
class ThreadModelSyncResult:
    database: Path
    backup: Path
    updated_threads: int


def _table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}


def sync_thread_models(
    codex_home: Path,
    provider_key: str,
    model: str,
) -> list[ThreadModelSyncResult]:
    """Make reopened, non-archived threads follow the newly selected model."""
    if not provider_key or not model:
        raise ThreadStateError("同步会话模型需要 provider 标签和模型名。")
    backup_root = codex_home / BACKUP_DIRECTORY_NAME
    backup_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    results: list[ThreadModelSyncResult] = []
    for database in sorted(codex_home.glob("state_*.sqlite")):
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(database, timeout=5)
            connection.execute("PRAGMA busy_timeout = 5000")
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            if "threads" not in tables:
                continue
            columns = _table_columns(connection, "threads")
            if not {"archived", "model", "model_provider"}.issubset(columns):
                continue
            backup_path = backup_root / f"{database.name}.model-sync-{stamp}.bak"
            backup_connection = sqlite3.connect(backup_path)
            try:
                connection.backup(backup_connection)
            finally:
                backup_connection.close()
            with connection:
                updated = connection.execute(
                    """
                    UPDATE threads
                       SET model = ?
                     WHERE archived = 0
                       AND model_provider = ?
                       AND (model IS NULL OR model <> ?)
                    """,
                    (model, provider_key, model),
                ).rowcount
            results.append(ThreadModelSyncResult(database, backup_path, max(0, updated)))
        except sqlite3.Error as exc:
            raise ThreadStateError(f"无法同步 Codex 会话模型：{exc}") from exc
        finally:
            if connection is not None:
                connection.close()
    return results
