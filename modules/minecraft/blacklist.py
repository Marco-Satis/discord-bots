"""
Minecraft Blacklist Manager — serveruebergreifendes Ban-System (Dual-Read)

Verwaltet Bans zentral in einer JSON-Datei UND in der SQLite-Datenbank.
JSON dient als Fallback und wird bei jedem Schreibvorgang aktualisiert.
SQLite ist die primaere Datenquelle (Tabellen: blacklist, bans).

Dual-Read Modus:
  - load()          : Laedt aus JSON (Fallback / Erststart)
  - load_from_db()  : Laedt aus SQLite blacklist-Tabelle (WHERE server_type='minecraft')
                       und ueberschreibt die In-Memory-Daten
  - add() / remove(): Schreiben IMMER in JSON + SQLite gleichzeitig

Ban-Synchronisation ueber RCON an alle aktiven MC-Server bleibt unveraendert.

JSON-Dateiformat (data/mc_blacklist.json):
{
  "bans": [
    {
      "player_name": "Griefer123",
      "reason": "Griefing",
      "banned_by": "Admin",
      "timestamp": "2026-02-20T15:30:00",
      "servers": ["ALL"],
      "active": true
    }
  ]
}
"""

import asyncio
import json
import aiofiles
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Dict, Any

from utils.logger import get_logger
from utils.config import DATA_DIR
from modules.database.db_manager import get_db

logger = get_logger("minecraft.blacklist")

BLACKLIST_FILE = DATA_DIR / "mc_blacklist.json"


class MinecraftBlacklist:
    """Serveruebergreifendes Ban-System fuer Minecraft (Dual-Read: JSON + SQLite)"""

    def __init__(self, filepath: Optional[Path] = None) -> None:
        self.filepath = filepath or BLACKLIST_FILE
        self._data: Dict[str, Any] = {"bans": []}
        self._lock = asyncio.Lock()

    async def load(self) -> None:
        """Blacklist von Disk laden"""
        try:
            if self.filepath.exists():
                async with aiofiles.open(self.filepath, "r", encoding="utf-8") as f:
                    content = await f.read()
                    self._data = json.loads(content)
            else:
                await self._save()
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"Blacklist laden fehlgeschlagen: {e}")

    async def _save(self) -> None:
        """Blacklist auf Disk speichern"""
        try:
            self.filepath.parent.mkdir(parents=True, exist_ok=True)
            async with aiofiles.open(self.filepath, "w", encoding="utf-8") as f:
                await f.write(json.dumps(self._data, indent=2, ensure_ascii=False))
        except IOError as e:
            logger.error(f"Blacklist speichern fehlgeschlagen: {e}")

    async def load_from_db(self) -> None:
        """Blacklist aus SQLite laden (primaere Datenquelle).

        Laedt alle Eintraege aus der blacklist-Tabelle mit server_type='minecraft'
        und ueberschreibt die In-Memory _data["bans"]. Behaelt die JSON-Datei
        als Fallback bei.
        """
        try:
            db = await get_db()
            cursor = await db.execute(
                "SELECT player_name, ip_address, reason, added_by, added_at "
                "FROM blacklist WHERE server_type = ?",
                ("minecraft",),
            )
            rows = await cursor.fetchall()

            bans: List[Dict[str, Any]] = []
            for row in rows:
                entry: Dict[str, Any] = {
                    "player_name": row["player_name"] or "",
                    "reason": row["reason"] or "",
                    "banned_by": row["added_by"] or "",
                    "timestamp": row["added_at"] or "",
                    "servers": ["ALL"],
                    "active": True,
                }
                if row["ip_address"]:
                    entry["ip"] = row["ip_address"]
                bans.append(entry)

            # Ergaenze mit inaktiven Bans aus der bans-Tabelle (active=FALSE)
            cursor2 = await db.execute(
                "SELECT player_name, ip_address, reason, banned_by, banned_at "
                "FROM bans WHERE server_type = ? AND active = FALSE "
                "AND source = 'mc_blacklist'",
                ("minecraft",),
            )
            inactive_rows = await cursor2.fetchall()
            for row in inactive_rows:
                entry = {
                    "player_name": row["player_name"] or "",
                    "reason": row["reason"] or "",
                    "banned_by": row["banned_by"] or "",
                    "timestamp": row["banned_at"] or "",
                    "servers": ["ALL"],
                    "active": False,
                }
                if row["ip_address"]:
                    entry["ip"] = row["ip_address"]
                bans.append(entry)

            self._data["bans"] = bans
            logger.info(
                f"Blacklist aus SQLite geladen: {len(bans)} Eintraege "
                f"({sum(1 for b in bans if b['active'])} aktiv)"
            )
        except Exception as e:
            logger.error(f"Blacklist aus SQLite laden fehlgeschlagen: {e}")

    @property
    def bans(self) -> List[Dict[str, Any]]:
        return self._data.get("bans", [])

    def _active_bans(self) -> List[Dict[str, Any]]:
        """Nur aktive Bans zurueckgeben"""
        return [b for b in self.bans if b.get("active", True)]

    def is_banned(self, player_name: str) -> bool:
        """Prueft ob ein Spieler aktiv gebannt ist"""
        name_lower = player_name.strip().lower()
        return any(
            b["player_name"].lower() == name_lower
            for b in self._active_bans()
        )

    def get_ban_info(self, player_name: str) -> Optional[Dict[str, Any]]:
        """Aktiven Ban-Eintrag fuer einen Spieler zurueckgeben"""
        name_lower = player_name.strip().lower()
        for b in self._active_bans():
            if b["player_name"].lower() == name_lower:
                return b
        return None

    async def add(self, player_name: str, reason: str, banned_by: str,
                  servers: Optional[List[str]] = None,
                  ip: Optional[str] = None) -> bool:
        """Spieler zur Blacklist hinzufuegen.

        Args:
            player_name: Minecraft-Spielername
            reason: Ban-Grund
            banned_by: Discord-User der den Ban ausgesprochen hat
            servers: Liste von Server-IDs oder None fuer alle Server
            ip: Bekannte IP-Adresse des Spielers (fuer IP-Ban-Tracking)

        Returns:
            True wenn hinzugefuegt, False wenn bereits gebannt
        """
        async with self._lock:
            if self.is_banned(player_name):
                return False

            entry = {
                "player_name": player_name.strip(),
                "reason": reason,
                "banned_by": banned_by,
                "timestamp": datetime.now().isoformat(),
                "servers": servers or ["ALL"],
                "active": True,
            }
            if ip:
                entry["ip"] = ip

            self._data.setdefault("bans", []).append(entry)
            await self._save()

            # --- SQLite Dual-Write ---
            try:
                db = await get_db()
                await db.execute(
                    "INSERT INTO blacklist (player_name, ip_address, server_type, "
                    "reason, added_by) VALUES (?, ?, 'minecraft', ?, ?)",
                    (player_name.strip(), ip or "", reason, banned_by),
                )
                await db.execute(
                    "INSERT INTO bans (ip_address, player_name, server_type, "
                    "reason, banned_by, active, source) "
                    "VALUES (?, ?, 'minecraft', ?, ?, TRUE, 'mc_blacklist')",
                    (ip or "", player_name.strip(), reason, banned_by),
                )
                await db.commit()
                logger.debug(f"Blacklist SQLite: {player_name} in blacklist+bans eingefuegt")
            except Exception as e:
                logger.error(f"Blacklist SQLite-Write fehlgeschlagen fuer {player_name}: {e}")

            logger.info(
                f"Blacklist: {player_name} gebannt von {banned_by} — {reason}"
                + (f" (IP: {ip})" if ip else "")
            )
            return True

    async def remove(self, player_name: str) -> bool:
        """Spieler entbannen (Ban wird als active=false markiert).

        Returns:
            True wenn entbannt, False wenn nicht gefunden
        """
        async with self._lock:
            name_lower = player_name.strip().lower()
            found = False
            for ban in self.bans:
                if ban["player_name"].lower() == name_lower and ban.get("active", True):
                    ban["active"] = False
                    ban["unbanned_at"] = datetime.now().isoformat()
                    found = True

            if found:
                await self._save()

                # --- SQLite Dual-Write ---
                try:
                    db = await get_db()
                    await db.execute(
                        "DELETE FROM blacklist WHERE player_name = ? "
                        "AND server_type = 'minecraft'",
                        (player_name.strip(),),
                    )
                    await db.execute(
                        "UPDATE bans SET active = FALSE "
                        "WHERE player_name = ? AND server_type = 'minecraft'",
                        (player_name.strip(),),
                    )
                    await db.commit()
                    logger.debug(f"Blacklist SQLite: {player_name} entfernt/deaktiviert")
                except Exception as e:
                    logger.error(f"Blacklist SQLite-Remove fehlgeschlagen fuer {player_name}: {e}")

                logger.info(f"Blacklist: {player_name} entbannt")
            return found

    def get_active_list(self) -> List[Dict[str, Any]]:
        """Alle aktiven Bans zurueckgeben"""
        return list(self._active_bans())

    def get_history(self, player_name: str) -> List[Dict[str, Any]]:
        """Komplette Ban-Historie eines Spielers (aktiv + inaktiv)"""
        name_lower = player_name.strip().lower()
        return [
            b for b in self.bans
            if b["player_name"].lower() == name_lower
        ]

    def count_active(self) -> int:
        """Anzahl aktiver Bans"""
        return len(self._active_bans())

    async def sync_ban_to_servers(self, player_name: str, reason: str,
                                  mc_servers: dict, server_ids: Optional[List[str]] = None) -> Dict[str, bool]:
        """Ban via RCON an MC-Server senden.

        Args:
            player_name: Spielername
            reason: Ban-Grund
            mc_servers: Dict von server_id -> MinecraftServer
            server_ids: Nur bestimmte Server, None = alle

        Returns:
            Dict mit server_id -> Erfolg (True/False)
        """
        results: Dict[str, bool] = {}
        targets = mc_servers

        if server_ids and "ALL" not in server_ids:
            targets = {k: v for k, v in mc_servers.items() if k in server_ids}

        for sid, srv in targets.items():
            try:
                if await srv.is_running():
                    await srv.rcon_command(f"ban {player_name} {reason}")
                    results[sid] = True
                    logger.info(f"[{sid}] Ban fuer {player_name} ausgefuehrt")
                else:
                    results[sid] = False
                    logger.debug(f"[{sid}] Server offline, Ban nicht ausgefuehrt")
            except Exception as e:
                results[sid] = False
                logger.warning(f"[{sid}] Ban fuer {player_name} fehlgeschlagen: {e}")

        return results

    async def sync_unban_to_servers(self, player_name: str,
                                    mc_servers: dict) -> Dict[str, bool]:
        """Pardon via RCON an alle MC-Server senden.

        Returns:
            Dict mit server_id -> Erfolg (True/False)
        """
        results: Dict[str, bool] = {}
        for sid, srv in mc_servers.items():
            try:
                if await srv.is_running():
                    await srv.rcon_command(f"pardon {player_name}")
                    results[sid] = True
                    logger.info(f"[{sid}] Pardon fuer {player_name} ausgefuehrt")
                else:
                    results[sid] = False
                    logger.debug(f"[{sid}] Server offline, Pardon nicht ausgefuehrt")
            except Exception as e:
                results[sid] = False
                logger.warning(f"[{sid}] Pardon fuer {player_name} fehlgeschlagen: {e}")

        return results
