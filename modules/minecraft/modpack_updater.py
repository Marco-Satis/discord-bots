"""
Modpack-Update-Checker — prueft Modrinth/CurseForge API auf neue Modpack-Versionen

Unterstuetzt:
- Modrinth API (bevorzugt, kein API-Key noetig)
- CurseForge API (Fallback, benoetigt CURSEFORGE_API_KEY)

Konfiguration via ENV:
    MC_BMC_MODPACK_ID       — Modrinth/CurseForge Projekt-ID
    MC_BMC_MODPACK_VERSION  — Aktuell installierte Version (manuell gesetzt)
    MC_BMC_MODPACK_SOURCE   — "modrinth" oder "curseforge" (Standard: modrinth)
    CURSEFORGE_API_KEY      — API-Key fuer CurseForge (optional)
"""

import aiohttp
from datetime import datetime
from typing import Optional, Tuple, Dict, Any

from utils.logger import get_logger
from utils.config import get_env

logger = get_logger("minecraft.modpack_updater")

MODRINTH_API = "https://api.modrinth.com/v2"
CURSEFORGE_API = "https://api.curseforge.com/v1"


class ModpackUpdater:
    """Prueft auf neue Modpack-Versionen via Modrinth oder CurseForge API"""

    def __init__(self,
                 modpack_id: str = "",
                 current_version: str = "",
                 source: str = "modrinth",
                 curseforge_api_key: str = ""):
        self.modpack_id = modpack_id
        self.current_version = current_version
        self.source = source.lower()
        self.curseforge_api_key = curseforge_api_key
        self.last_check: Optional[datetime] = None
        self.latest_version: Optional[str] = None
        self.update_available = False

    @property
    def enabled(self) -> bool:
        """Checker ist aktiv wenn eine Modpack-ID konfiguriert ist"""
        return bool(self.modpack_id and self.current_version)

    async def check(self) -> Tuple[bool, Dict[str, Any]]:
        """Prueft auf neue Version.

        Returns:
            (update_verfuegbar, info_dict)
        """
        if not self.enabled:
            return False, {"error": "Modpack-ID oder Version nicht konfiguriert"}

        self.last_check = datetime.now()

        try:
            if self.source == "curseforge" and self.curseforge_api_key:
                return await self._check_curseforge()
            else:
                return await self._check_modrinth()
        except Exception as e:
            logger.warning(f"Modpack-Check fehlgeschlagen: {e}")
            return False, {"error": str(e)}

    async def _check_modrinth(self) -> Tuple[bool, Dict[str, Any]]:
        """Prueft Modrinth API auf neue Versionen"""
        url = f"{MODRINTH_API}/project/{self.modpack_id}/version"
        headers = {
            "User-Agent": "DiscordBot/3.1.0 (GameServer Management)",
        }

        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status != 200:
                    error = f"Modrinth API Fehler: HTTP {resp.status}"
                    logger.warning(error)
                    return False, {"error": error}

                versions = await resp.json()

        if not versions:
            return False, {"error": "Keine Versionen gefunden"}

        # Neueste Version (erstes Element, sortiert nach Datum)
        latest = versions[0]
        latest_version = latest.get("version_number", "?")
        self.latest_version = latest_version

        info = {
            "current": self.current_version,
            "latest": latest_version,
            "source": "modrinth",
            "name": latest.get("name", "?"),
            "changelog_url": f"https://modrinth.com/modpack/{self.modpack_id}/version/{latest.get('id', '')}",
            "date": latest.get("date_published", "?")[:10],
            "checked_at": self.last_check.isoformat() if self.last_check else "?",
        }

        if latest_version != self.current_version:
            self.update_available = True
            logger.info(
                f"Modpack-Update verfuegbar: {self.current_version} -> {latest_version}"
            )
            return True, info
        else:
            self.update_available = False
            return False, info

    async def _check_curseforge(self) -> Tuple[bool, Dict[str, Any]]:
        """Prueft CurseForge API auf neue Versionen"""
        url = f"{CURSEFORGE_API}/mods/{self.modpack_id}/files"
        headers = {
            "x-api-key": self.curseforge_api_key,
            "Accept": "application/json",
        }
        params = {
            "pageSize": 1,
            "sortField": 1,  # Sort by date
            "sortOrder": "desc",
        }

        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, params=params,
                                   timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status != 200:
                    error = f"CurseForge API Fehler: HTTP {resp.status}"
                    logger.warning(error)
                    return False, {"error": error}

                data = await resp.json()

        files = data.get("data", [])
        if not files:
            return False, {"error": "Keine Dateien gefunden"}

        latest = files[0]
        latest_version = latest.get("displayName", "?")
        self.latest_version = latest_version

        info = {
            "current": self.current_version,
            "latest": latest_version,
            "source": "curseforge",
            "name": latest.get("displayName", "?"),
            "changelog_url": f"https://www.curseforge.com/minecraft/modpacks/{self.modpack_id}/files/{latest.get('id', '')}",
            "date": latest.get("fileDate", "?")[:10],
            "checked_at": self.last_check.isoformat() if self.last_check else "?",
        }

        if latest_version != self.current_version:
            self.update_available = True
            logger.info(
                f"Modpack-Update verfuegbar: {self.current_version} -> {latest_version}"
            )
            return True, info
        else:
            self.update_available = False
            return False, info

    def get_status(self) -> Dict[str, Any]:
        """Status-Info zurueckgeben"""
        return {
            "enabled": self.enabled,
            "source": self.source,
            "modpack_id": self.modpack_id,
            "current_version": self.current_version,
            "latest_version": self.latest_version,
            "update_available": self.update_available,
            "last_check": self.last_check.isoformat() if self.last_check else None,
        }
