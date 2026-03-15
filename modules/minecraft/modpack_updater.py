"""
Modpack-Update-Checker — prüft CurseForge API auf neue Modpack-Versionen

Unterstützt ausschließlich CurseForge API v1 (Modrinth hat keine Server Packs).

CurseForge API-Flow:
  1. GET /v1/mods/{project_id}/files?pageSize=1 → neueste Client-Version
  2. Client-Pack hat `serverPackFileId` → verweist auf Server Pack
  3. GET /v1/mods/{project_id}/files/{serverPackFileId} → Download-URL + Hashes

Konfiguration via ENV (Initial-Werte, danach SQLite):
    CURSEFORGE_API_KEY           — API-Key (kostenlos, bcrypt-Format)
    MC_{ID}_CURSEFORGE_PROJECT_ID — CurseForge Projekt-ID
    MC_{ID}_CURSEFORGE_FILE_ID    — Aktuell installierte Client-Pack File-ID
    MC_{ID}_MODPACK_VERSION       — Display-Version (z.B. "v48.5")
"""

import asyncio
import re
from datetime import datetime
from typing import Optional, Tuple, Dict, Any

import aiohttp

from utils.logger import get_logger
from utils.config import get_env

logger = get_logger("minecraft.modpack_updater")

CURSEFORGE_API = "https://api.curseforge.com/v1"

# Rate-Limiter: Minimale Pause zwischen API-Aufrufen (Sekunden)
_MIN_REQUEST_INTERVAL = 2.0
_last_request_time: float = 0.0


async def _rate_limited_request(
    session: aiohttp.ClientSession,
    url: str,
    headers: dict,
    params: Optional[dict] = None,
    max_retries: int = 3,
) -> dict:
    """
    CurseForge API-Request mit Rate-Limiting und Exponential Backoff.

    Returns:
        JSON-Response als dict

    Raises:
        aiohttp.ClientError bei Netzwerkfehlern
        ValueError bei ungültiger Antwort
    """
    global _last_request_time

    for attempt in range(1, max_retries + 1):
        # Rate-Limiting: Mindestabstand zwischen Requests
        now = asyncio.get_event_loop().time()
        wait = _MIN_REQUEST_INTERVAL - (now - _last_request_time)
        if wait > 0:
            await asyncio.sleep(wait)

        _last_request_time = asyncio.get_event_loop().time()

        try:
            async with session.get(
                url,
                headers=headers,
                params=params,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status == 200:
                    return await resp.json()
                elif resp.status == 429:
                    # Rate-Limited: Exponential Backoff
                    backoff = min(30, 2 ** attempt * 5)
                    logger.warning(
                        f"CurseForge Rate-Limit (429) — "
                        f"Warte {backoff}s (Versuch {attempt}/{max_retries})"
                    )
                    await asyncio.sleep(backoff)
                    continue
                else:
                    error = f"CurseForge API HTTP {resp.status}"
                    logger.warning(f"{error} für {url}")
                    if attempt < max_retries:
                        await asyncio.sleep(attempt * 5)
                        continue
                    raise ValueError(error)

        except aiohttp.ClientError as e:
            if attempt < max_retries:
                backoff = attempt * 5
                logger.warning(
                    f"CurseForge API-Fehler: {e} — "
                    f"Retry in {backoff}s (Versuch {attempt}/{max_retries})"
                )
                await asyncio.sleep(backoff)
            else:
                raise

    raise ValueError(f"CurseForge API nach {max_retries} Versuchen nicht erreichbar")


class ModpackUpdater:
    """
    Prüft CurseForge auf neue Modpack-Versionen und liefert Server Pack Details.

    Version-Tracking erfolgt über SQLite (server_versions Tabelle).
    ENV-Variablen dienen nur als Fallback für den ersten Start.
    """

    def __init__(
        self,
        server_id: str = "BMC",
        project_id: str = "",
        current_file_id: int = 0,
        current_version: str = "",
        api_key: str = "",
    ):
        self.server_id = server_id.upper()
        self.project_id = project_id
        self.current_file_id = current_file_id
        self.current_version = current_version
        self.api_key = api_key

        self.last_check: Optional[datetime] = None
        self.latest_version: Optional[str] = None
        self.latest_file_id: Optional[int] = None
        self.update_available = False

        # Server Pack Details (gefüllt nach check())
        self.server_pack_info: Optional[Dict[str, Any]] = None

    @property
    def enabled(self) -> bool:
        """Checker ist aktiv wenn Projekt-ID und API-Key konfiguriert sind."""
        return bool(self.project_id and self.api_key and self.current_file_id)

    @classmethod
    def from_env(cls, server_id: str = "BMC") -> "ModpackUpdater":
        """Erstellt Instanz aus ENV-Variablen (für ersten Start)."""
        sid = server_id.upper()
        return cls(
            server_id=sid,
            project_id=get_env(f"MC_{sid}_CURSEFORGE_PROJECT_ID", ""),
            current_file_id=int(get_env(f"MC_{sid}_CURSEFORGE_FILE_ID", "0")),
            current_version=get_env(f"MC_{sid}_MODPACK_VERSION", ""),
            api_key=get_env("CURSEFORGE_API_KEY", ""),
        )

    async def load_from_db(self, db) -> None:
        """Lädt aktuelle Version aus SQLite (bevorzugt gegenüber ENV)."""
        try:
            row = await db.fetch_one(
                "SELECT * FROM server_versions WHERE server_id = ?",
                (self.server_id,),
            )
            if row:
                self.current_file_id = row["curseforge_file_id"] or self.current_file_id
                self.current_version = row["display_version"] or self.current_version
                logger.info(
                    f"[{self.server_id}] Version aus DB geladen: "
                    f"{self.current_version} (File-ID: {self.current_file_id})"
                )
        except Exception as e:
            logger.debug(f"[{self.server_id}] DB-Version nicht verfügbar: {e}")

    # ------------------------------------------------------------------
    # Versions-Check
    # ------------------------------------------------------------------

    async def check(self) -> Tuple[bool, Dict[str, Any]]:
        """
        Prüft CurseForge auf neue Version.

        Returns:
            (update_verfügbar, info_dict)
        """
        if not self.enabled:
            return False, {"error": "Modpack-Updater nicht konfiguriert"}

        self.last_check = datetime.now()

        try:
            return await self._check_curseforge()
        except Exception as e:
            logger.warning(f"[{self.server_id}] Modpack-Check fehlgeschlagen: {e}")
            return False, {"error": str(e)}

    async def _check_curseforge(self) -> Tuple[bool, Dict[str, Any]]:
        """Prüft CurseForge API auf neue Versionen."""
        headers = {
            "x-api-key": self.api_key,
            "Accept": "application/json",
            "User-Agent": "DiscordBot/4.0.0 (GameServer-Management)",
        }

        async with aiohttp.ClientSession() as session:
            # Schritt 1: Neueste Version abfragen
            url = f"{CURSEFORGE_API}/mods/{self.project_id}/files"
            data = await _rate_limited_request(
                session, url, headers,
                params={"pageSize": "1"},
            )

            files = data.get("data", [])
            if not files:
                return False, {"error": "Keine Dateien auf CurseForge gefunden"}

            latest = files[0]
            latest_file_id = latest.get("id", 0)
            display_name = latest.get("displayName", "?")

            # Version aus displayName parsen (z.B. "BMC5 [NEOFORGE] 1.21.1 v48.5" → "v48.5")
            version_match = re.search(r'v[\d.]+', display_name)
            latest_version = version_match.group(0) if version_match else display_name

            self.latest_version = latest_version
            self.latest_file_id = latest_file_id

            info = {
                "current": self.current_version,
                "current_file_id": self.current_file_id,
                "latest": latest_version,
                "latest_file_id": latest_file_id,
                "source": "curseforge",
                "name": display_name,
                "game_versions": latest.get("gameVersions", []),
                "server_pack_file_id": latest.get("serverPackFileId"),
                "checked_at": self.last_check.isoformat() if self.last_check else "?",
            }

            # Versionsvergleich via File-ID (nicht Versionsstring!)
            if latest_file_id != self.current_file_id:
                self.update_available = True
                logger.info(
                    f"[{self.server_id}] Modpack-Update verfügbar: "
                    f"{self.current_version} → {latest_version} "
                    f"(File-ID: {self.current_file_id} → {latest_file_id})"
                )

                # Schritt 2: Server Pack Details abrufen
                server_pack_file_id = latest.get("serverPackFileId")
                if server_pack_file_id:
                    sp_info = await self._get_server_pack_details(
                        session, headers, server_pack_file_id
                    )
                    if sp_info:
                        info["server_pack"] = sp_info
                        self.server_pack_info = sp_info
                else:
                    logger.warning(
                        f"[{self.server_id}] Kein Server Pack für {latest_version} "
                        f"(serverPackFileId ist null)"
                    )
                    info["server_pack"] = None

                return True, info
            else:
                self.update_available = False
                return False, info

    async def _get_server_pack_details(
        self,
        session: aiohttp.ClientSession,
        headers: dict,
        server_pack_file_id: int,
    ) -> Optional[Dict[str, Any]]:
        """
        Ruft Details zum Server Pack ab (Download-URL, Hashes, Größe).

        Args:
            session: aiohttp Session
            headers: API-Headers mit Key
            server_pack_file_id: CurseForge File-ID des Server Packs

        Returns:
            Server Pack Info-Dict oder None
        """
        url = f"{CURSEFORGE_API}/mods/{self.project_id}/files/{server_pack_file_id}"

        try:
            data = await _rate_limited_request(session, url, headers)
            sp = data.get("data", {})

            # Hashes extrahieren
            hashes = {}
            for h in sp.get("hashes", []):
                algo = h.get("algo", 0)
                value = h.get("value", "")
                if algo == 1:  # SHA1
                    hashes["sha1"] = value
                elif algo == 2:  # MD5
                    hashes["md5"] = value

            info = {
                "file_id": sp.get("id"),
                "file_name": sp.get("fileName", "?"),
                "download_url": sp.get("downloadUrl", ""),
                "file_size": sp.get("fileLength", 0),
                "file_size_mb": round(sp.get("fileLength", 0) / (1024 * 1024), 1),
                "is_server_pack": sp.get("isServerPack", False),
                "hashes": hashes,
            }

            logger.info(
                f"[{self.server_id}] Server Pack gefunden: "
                f"{info['file_name']} ({info['file_size_mb']} MB)"
            )
            return info

        except Exception as e:
            logger.warning(
                f"[{self.server_id}] Server Pack Details konnten nicht "
                f"abgerufen werden: {e}"
            )
            return None

    # ------------------------------------------------------------------
    # Version aktualisieren
    # ------------------------------------------------------------------

    async def update_version_in_db(
        self,
        db,
        new_file_id: int,
        new_version: str,
        server_pack_file_id: Optional[int] = None,
        neoforge_version: Optional[str] = None,
    ) -> None:
        """
        Aktualisiert die installierte Version in SQLite.

        Args:
            db: Datenbank-Instanz
            new_file_id: Neue CurseForge Client-Pack File-ID
            new_version: Neue Display-Version (z.B. "v48.5")
            server_pack_file_id: Server Pack File-ID (optional)
            neoforge_version: NeoForge-Version (optional)
        """
        try:
            await db.execute(
                """INSERT OR REPLACE INTO server_versions
                   (server_id, display_version, curseforge_file_id,
                    curseforge_server_file_id, neoforge_version,
                    updated_at, updated_by)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    self.server_id,
                    new_version,
                    new_file_id,
                    server_pack_file_id,
                    neoforge_version,
                    datetime.now().isoformat(),
                    "auto",
                ),
            )

            self.current_file_id = new_file_id
            self.current_version = new_version
            self.update_available = False

            logger.info(
                f"[{self.server_id}] Version in DB aktualisiert: "
                f"{new_version} (File-ID: {new_file_id})"
            )
        except Exception as e:
            logger.error(f"[{self.server_id}] Version-Update in DB fehlgeschlagen: {e}")

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def get_status(self) -> Dict[str, Any]:
        """Status-Info für Discord-Commands und Dashboard."""
        return {
            "enabled": self.enabled,
            "source": "curseforge",
            "server_id": self.server_id,
            "project_id": self.project_id,
            "current_version": self.current_version,
            "current_file_id": self.current_file_id,
            "latest_version": self.latest_version,
            "latest_file_id": self.latest_file_id,
            "update_available": self.update_available,
            "server_pack": self.server_pack_info,
            "last_check": self.last_check.isoformat() if self.last_check else None,
        }
