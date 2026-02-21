"""
Config History — Config-Versioning

Tracks all configuration changes in a SQLite table (config_history).
Provides functions to log changes and query the history.
"""

from __future__ import annotations

import json
from datetime import datetime

from modules.database.db_manager import get_db
from utils.logger import get_logger

logger = get_logger("config_history")

# ---------------------------------------------------------------------------
# Schema — Table + Indexes
# ---------------------------------------------------------------------------

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS config_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    config_key TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT,
    changed_by TEXT,
    source TEXT
);
"""

INDEX_SQL = [
    "CREATE INDEX IF NOT EXISTS idx_config_hist_time ON config_history(timestamp);",
    "CREATE INDEX IF NOT EXISTS idx_config_hist_key ON config_history(config_key);",
]


async def ensure_table() -> None:
    """Create the config_history table and indexes if they don't exist."""
    db = await get_db()
    await db.execute(SCHEMA_SQL)
    for idx_sql in INDEX_SQL:
        await db.execute(idx_sql)
    await db.commit()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def log_config_change(
    key: str,
    old_value,
    new_value,
    changed_by: str = "system",
    source: str = "bot",
) -> None:
    """
    Record a configuration change in the config_history table.

    Args:
        key: The configuration key that was changed.
        old_value: Previous value (auto-converted to JSON string if not str).
        new_value: New value (auto-converted to JSON string if not str).
        changed_by: Who triggered the change (user ID, "system", etc.).
        source: Origin of the change ("bot", "dashboard", "api", etc.).
    """
    await ensure_table()

    # Convert non-string values to JSON
    if old_value is not None and not isinstance(old_value, str):
        old_value = json.dumps(old_value, ensure_ascii=False)
    if new_value is not None and not isinstance(new_value, str):
        new_value = json.dumps(new_value, ensure_ascii=False)

    db = await get_db()
    await db.execute(
        """
        INSERT INTO config_history (config_key, old_value, new_value, changed_by, source)
        VALUES (?, ?, ?, ?, ?)
        """,
        (key, old_value, new_value, changed_by, source),
    )
    await db.commit()

    logger.info(
        "Config changed: %s | by=%s source=%s | old=%s -> new=%s",
        key,
        changed_by,
        source,
        old_value,
        new_value,
    )


async def get_config_history(
    key: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """
    Query the config_history table.

    Args:
        key: Optional config key to filter by. If None, return all entries.
        limit: Maximum number of rows to return (default 50).

    Returns:
        List of dicts with columns: id, timestamp, config_key,
        old_value, new_value, changed_by, source.
    """
    await ensure_table()

    db = await get_db()

    if key is not None:
        cursor = await db.execute(
            """
            SELECT id, timestamp, config_key, old_value, new_value, changed_by, source
            FROM config_history
            WHERE config_key = ?
            ORDER BY timestamp DESC
            LIMIT ?
            """,
            (key, limit),
        )
    else:
        cursor = await db.execute(
            """
            SELECT id, timestamp, config_key, old_value, new_value, changed_by, source
            FROM config_history
            ORDER BY timestamp DESC
            LIMIT ?
            """,
            (limit,),
        )

    rows = await cursor.fetchall()
    return [
        {
            "id": row[0],
            "timestamp": row[1],
            "config_key": row[2],
            "old_value": row[3],
            "new_value": row[4],
            "changed_by": row[5],
            "source": row[6],
        }
        for row in rows
    ]


async def get_last_change(key: str) -> dict | None:
    """
    Get the most recent change for a specific config key.

    Args:
        key: The configuration key to look up.

    Returns:
        A dict with the latest change, or None if no history exists.
    """
    await ensure_table()

    db = await get_db()
    cursor = await db.execute(
        """
        SELECT id, timestamp, config_key, old_value, new_value, changed_by, source
        FROM config_history
        WHERE config_key = ?
        ORDER BY timestamp DESC
        LIMIT 1
        """,
        (key,),
    )
    row = await cursor.fetchone()

    if row is None:
        return None

    return {
        "id": row[0],
        "timestamp": row[1],
        "config_key": row[2],
        "old_value": row[3],
        "new_value": row[4],
        "changed_by": row[5],
        "source": row[6],
    }
