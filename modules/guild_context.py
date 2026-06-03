"""
Multi-Tenant-Fundament — Guild-Kontext + Per-Guild-Config + Feature-Flags.

Zentrale Stelle fuer alles was "welche Guild?" beantwortet. Ersetzt die ueber
den Code verstreute Single-Guild-Annahme (`GUILD_ID`-Hardcodes) durch einen
Resolver, und bietet eine schema-getriebene Per-Guild-Settings-Schicht
(Dashboard-editierbar), auf der Temp-Voice-Hubs, Level-Config etc. aufbauen.

Backed by Migration v5: Tabellen `guilds`, `guild_config`, `guild_modules`.

Nutzung:
    from modules.guild_context import get_primary_guild_id, GuildConfig

    gid = get_primary_guild_id()                       # Marcos Haupt-Guild (ENV)
    naming = await GuildConfig.get(gid, "tempvoice.naming", "{user}'s Voice")
    await GuildConfig.set(gid, "tempvoice.naming", "🎮 {user}", updated_by="dashboard")
    if await GuildConfig.is_module_enabled(gid, "leveling"):
        ...

Werte werden JSON-encoded gespeichert (bool/int/dict/list roundtrip-sicher).
Ein kurzer TTL-Cache (15 s) entlastet Hot-Paths (z.B. per-Message-Modul-Check);
da 4 Prozesse sich die DB teilen, ist Cross-Prozess-Staleness bis TTL moeglich
(akzeptabel fuer Config; Dashboard-Aenderung greift spaetestens nach TTL).
"""
from __future__ import annotations

import json
import time
from typing import Any, List, Optional, TYPE_CHECKING

from modules.database.db_manager import get_db, get_read_db
from utils.config import get_env
from utils.logger import get_logger

if TYPE_CHECKING:  # pragma: no cover — nur fuer Type-Hints, kein Laufzeit-Import
    import discord

logger = get_logger("guild_context")

# --- Bekannte Module (Feature-Flags) — zentrale Liste ----------------------
# Erweiterbar; unbekannte Module sind erlaubt (default-enabled), diese Liste
# dient Dashboard-Auflistung + Tippfehler-Vermeidung im Code.
KNOWN_MODULES: tuple[str, ...] = (
    "leveling",
    "tempvoice",
    "moderation",
    "music",
    "gameserver",   # SAT/MC-Monitoring (Freund ohne Game-Server schaltet ab)
    "giveaways",
    "tickets",
    "reaction_roles",
    "welcome",
)

# --- TTL-Cache (Hot-Path-Entlastung) ---------------------------------------
_CACHE_TTL: float = 15.0  # Sekunden — bounded Cross-Prozess-Staleness
_config_cache: dict[tuple[str, str], tuple[Any, float]] = {}
_module_cache: dict[tuple[str, str], tuple[bool, float]] = {}

# Sentinel fuer "Key existiert nicht" (unterscheidbar von gespeichertem None)
_MISSING = object()


def clear_cache() -> None:
    """Leert beide Caches komplett (Tests + nach Bulk-Config-Aenderung)."""
    _config_cache.clear()
    _module_cache.clear()


# ===========================================================================
# Guild-Resolver
# ===========================================================================

def get_primary_guild_id() -> Optional[int]:
    """
    Marcos Haupt-Guild-ID aus der ENV (`GUILD_ID`).

    Single-Guild-Default fuer Backward-Compat (Command-Sync, Voice-Stats).
    None wenn nicht gesetzt.
    """
    raw = get_env("GUILD_ID")
    if raw in (None, ""):
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        logger.warning(f"GUILD_ID nicht als int parsebar: {raw!r}")
        return None


def get_active_guild_ids(bot: "discord.Client") -> List[int]:
    """
    Alle Guilds die dieser Bot aktuell bedient.

    Fundament fuer Multi-Guild-Command-Sync (statt nur die eine `GUILD_ID`).
    """
    return [g.id for g in getattr(bot, "guilds", [])]


# ===========================================================================
# Per-Guild-Config + Feature-Flags
# ===========================================================================

class GuildConfig:
    """
    Schema-getriebene Per-Guild-Settings + Modul-Feature-Flags (DB-backed).

    Alle Methoden sind async und statisch. `guild_id` wird intern zu str
    normalisiert (Snowflake-TEXT, konsistent mit dem restlichen Schema).
    """

    # --- Generische Settings -----------------------------------------------

    @staticmethod
    async def get(guild_id: int | str, key: str, default: Any = None) -> Any:
        """
        Liest einen Config-Wert. `default` wenn Key fuer die Guild fehlt.

        Wert wird JSON-decoded (bool/int/dict/list roundtrip-sicher).
        """
        ck = (str(guild_id), key)
        cached = _config_cache.get(ck)
        now = time.monotonic()
        if cached is not None and cached[1] > now:
            val = cached[0]
            return default if val is _MISSING else val

        conn = await get_read_db()
        cursor = await conn.execute(
            "SELECT value FROM guild_config WHERE guild_id = ? AND key = ?",
            (str(guild_id), key),
        )
        row = await cursor.fetchone()
        if row is None:
            _config_cache[ck] = (_MISSING, now + _CACHE_TTL)
            return default
        value = _decode(row[0])
        _config_cache[ck] = (value, now + _CACHE_TTL)
        return value

    @staticmethod
    async def set(
        guild_id: int | str, key: str, value: Any, updated_by: str = "system"
    ) -> None:
        """Setzt/aktualisiert einen Config-Wert (Upsert) + invalidiert Cache."""
        conn = await get_db()
        await conn.execute(
            "INSERT INTO guild_config (guild_id, key, value, updated_at, updated_by) "
            "VALUES (?, ?, ?, CURRENT_TIMESTAMP, ?) "
            "ON CONFLICT(guild_id, key) DO UPDATE SET "
            "value = excluded.value, updated_at = CURRENT_TIMESTAMP, "
            "updated_by = excluded.updated_by",
            (str(guild_id), key, json.dumps(value), updated_by),
        )
        await conn.commit()
        _config_cache[(str(guild_id), key)] = (value, time.monotonic() + _CACHE_TTL)

    @staticmethod
    async def delete(guild_id: int | str, key: str) -> None:
        """Loescht einen Config-Key fuer die Guild (Reset auf Default)."""
        conn = await get_db()
        await conn.execute(
            "DELETE FROM guild_config WHERE guild_id = ? AND key = ?",
            (str(guild_id), key),
        )
        await conn.commit()
        _config_cache.pop((str(guild_id), key), None)

    @staticmethod
    async def get_all(guild_id: int | str) -> dict[str, Any]:
        """Alle Config-Werte einer Guild als dict (fuer Dashboard-Render)."""
        conn = await get_read_db()
        cursor = await conn.execute(
            "SELECT key, value FROM guild_config WHERE guild_id = ?",
            (str(guild_id),),
        )
        rows = await cursor.fetchall()
        return {row[0]: _decode(row[1]) for row in rows}

    # --- Feature-Flags / Modul-Toggles -------------------------------------

    @staticmethod
    async def is_module_enabled(
        guild_id: int | str, module: str, default: bool = True
    ) -> bool:
        """
        Ob ein Modul fuer die Guild aktiv ist. `default` wenn kein Eintrag
        existiert (Module sind standardmaessig an, ausser explizit deaktiviert).
        """
        ck = (str(guild_id), module)
        cached = _module_cache.get(ck)
        now = time.monotonic()
        if cached is not None and cached[1] > now:
            return cached[0]

        conn = await get_read_db()
        cursor = await conn.execute(
            "SELECT enabled FROM guild_modules WHERE guild_id = ? AND module = ?",
            (str(guild_id), module),
        )
        row = await cursor.fetchone()
        result = bool(row[0]) if row is not None else default
        _module_cache[ck] = (result, now + _CACHE_TTL)
        return result

    @staticmethod
    async def set_module(
        guild_id: int | str, module: str, enabled: bool
    ) -> None:
        """Aktiviert/deaktiviert ein Modul fuer die Guild (Upsert) + Cache-Invalidate."""
        conn = await get_db()
        await conn.execute(
            "INSERT INTO guild_modules (guild_id, module, enabled, updated_at) "
            "VALUES (?, ?, ?, CURRENT_TIMESTAMP) "
            "ON CONFLICT(guild_id, module) DO UPDATE SET "
            "enabled = excluded.enabled, updated_at = CURRENT_TIMESTAMP",
            (str(guild_id), module, 1 if enabled else 0),
        )
        await conn.commit()
        _module_cache[(str(guild_id), module)] = (
            bool(enabled), time.monotonic() + _CACHE_TTL,
        )

    @staticmethod
    async def get_modules(guild_id: int | str) -> dict[str, bool]:
        """Explizit gesetzte Modul-Flags der Guild (fuer Dashboard-Render)."""
        conn = await get_read_db()
        cursor = await conn.execute(
            "SELECT module, enabled FROM guild_modules WHERE guild_id = ?",
            (str(guild_id),),
        )
        rows = await cursor.fetchall()
        return {row[0]: bool(row[1]) for row in rows}

    # --- Guild-Registry ----------------------------------------------------

    @staticmethod
    async def ensure_guild(
        guild_id: int | str,
        name: Optional[str] = None,
        owner_id: Optional[int | str] = None,
    ) -> None:
        """
        Registriert eine Guild in der `guilds`-Tabelle (idempotent).

        Beim Bot-Start fuer jede `bot.guilds` aufrufen -> Multi-Tenant-Registry.
        Aktualisiert Name/Owner falls die Guild bereits existiert.
        """
        conn = await get_db()
        await conn.execute(
            "INSERT INTO guilds (guild_id, name, owner_id, active) "
            "VALUES (?, ?, ?, 1) "
            "ON CONFLICT(guild_id) DO UPDATE SET "
            "name = COALESCE(excluded.name, guilds.name), "
            "owner_id = COALESCE(excluded.owner_id, guilds.owner_id), "
            "active = 1",
            (str(guild_id), name, str(owner_id) if owner_id is not None else None),
        )
        await conn.commit()

    @staticmethod
    async def list_guilds(active_only: bool = True) -> list[dict[str, Any]]:
        """Alle registrierten Guilds (fuer SaaS-Meta-Sicht / Dashboard)."""
        conn = await get_read_db()
        sql = "SELECT guild_id, name, owner_id, joined_at, active FROM guilds"
        if active_only:
            sql += " WHERE active = 1"
        cursor = await conn.execute(sql)
        rows = await cursor.fetchall()
        return [
            {
                "guild_id": row[0],
                "name": row[1],
                "owner_id": row[2],
                "joined_at": row[3],
                "active": bool(row[4]),
            }
            for row in rows
        ]


def _decode(raw: Optional[str]) -> Any:
    """Decodiert einen gespeicherten Config-Wert (JSON; Fallback: roher Text)."""
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        # Toleranz fuer evtl. nicht-JSON-Altwerte (sollte nicht vorkommen)
        return raw
