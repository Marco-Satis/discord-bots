"""
Linked-Accounts-Manager — generisches Verlinken externer Spiel-Accounts.

Verallgemeinert die urspruenglich nur fuer Activision geplante Funktion:
ein User verlinkt pro Guild seine Plattform-IDs (Activision/Steam/Epic/...),
nutzbar im Temp-Voice-Panel und in Profil-/Account-Commands.

Daten sind guild-scoped (`UNIQUE(guild_id, user_id, platform)`, Migration v7) —
kein Cross-Guild-Leak von verlinkter PII. Werte werden ueber parametrisierte
Statements geschrieben (CWE-89).
"""
from __future__ import annotations

from typing import Optional

from modules.database.db_manager import get_db
from utils.logger import get_logger

logger = get_logger("linked_accounts")

# Erlaubte Plattformen (Whitelist) -> Anzeigename. Erweiterbar.
PLATFORMS: dict[str, str] = {
    "activision": "Activision",
    "steam": "Steam",
    "epic": "Epic Games",
    "riot": "Riot",
    "battlenet": "Battle.net",
    "xbox": "Xbox",
    "psn": "PlayStation",
    "nintendo": "Nintendo",
    "origin": "EA / Origin",
    "ubisoft": "Ubisoft",
}

# Max-Laenge einer Account-ID (DoS/Garbage-Schutz)
MAX_ACCOUNT_ID_LEN = 100


def is_valid_platform(platform: str) -> bool:
    """True wenn die Plattform in der Whitelist steht (case-insensitive)."""
    return platform.lower() in PLATFORMS


def platform_display(platform: str) -> str:
    """Anzeigename einer Plattform (Fallback: Titlecase des Keys)."""
    return PLATFORMS.get(platform.lower(), platform.title())


class LinkedAccountsManager:
    """Verlinkte Accounts pro Guild + User (DB-backed, async)."""

    @staticmethod
    async def link(
        guild_id: int | str,
        user_id: int | str,
        platform: str,
        account_id: str,
    ) -> bool:
        """
        Account verlinken/aktualisieren (Upsert auf (guild, user, platform)).

        Returns:
            True bei Erfolg, False bei ungueltiger Plattform / leerer ID.
        """
        platform = platform.lower().strip()
        account_id = (account_id or "").strip()
        if not is_valid_platform(platform) or not account_id:
            return False
        if len(account_id) > MAX_ACCOUNT_ID_LEN:
            account_id = account_id[:MAX_ACCOUNT_ID_LEN]

        try:
            db = await get_db()
            await db.execute(
                "INSERT INTO user_linked_accounts "
                "(guild_id, user_id, platform, account_id, linked_at) "
                "VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP) "
                "ON CONFLICT(guild_id, user_id, platform) DO UPDATE SET "
                "account_id = excluded.account_id, linked_at = CURRENT_TIMESTAMP",
                (str(guild_id), str(user_id), platform, account_id),
            )
            await db.commit()
            return True
        except Exception as e:
            logger.error(f"link({guild_id},{user_id},{platform}) fehlgeschlagen: {e}")
            return False

    @staticmethod
    async def unlink(
        guild_id: int | str, user_id: int | str, platform: str
    ) -> bool:
        """
        Verlinkung loeschen.

        Returns:
            True wenn ein Eintrag entfernt wurde, sonst False.
        """
        platform = platform.lower().strip()
        try:
            db = await get_db()
            cursor = await db.execute(
                "DELETE FROM user_linked_accounts "
                "WHERE guild_id = ? AND user_id = ? AND platform = ?",
                (str(guild_id), str(user_id), platform),
            )
            await db.commit()
            return (cursor.rowcount or 0) > 0
        except Exception as e:
            logger.error(f"unlink({guild_id},{user_id},{platform}) fehlgeschlagen: {e}")
            return False

    @staticmethod
    async def get_links(
        guild_id: int | str, user_id: int | str
    ) -> dict[str, str]:
        """Alle Verlinkungen eines Users in der Guild: {platform: account_id}."""
        try:
            db = await get_db()
            cursor = await db.execute(
                "SELECT platform, account_id FROM user_linked_accounts "
                "WHERE guild_id = ? AND user_id = ? ORDER BY platform",
                (str(guild_id), str(user_id)),
            )
            rows = await cursor.fetchall()
            return {row[0]: row[1] for row in rows}
        except Exception as e:
            logger.error(f"get_links({guild_id},{user_id}) fehlgeschlagen: {e}")
            return {}

    @staticmethod
    async def get_link(
        guild_id: int | str, user_id: int | str, platform: str
    ) -> Optional[str]:
        """Account-ID einer einzelnen Plattform (oder None)."""
        platform = platform.lower().strip()
        try:
            db = await get_db()
            cursor = await db.execute(
                "SELECT account_id FROM user_linked_accounts "
                "WHERE guild_id = ? AND user_id = ? AND platform = ?",
                (str(guild_id), str(user_id), platform),
            )
            row = await cursor.fetchone()
            return row[0] if row else None
        except Exception as e:
            logger.error(f"get_link({guild_id},{user_id},{platform}) fehlgeschlagen: {e}")
            return None
