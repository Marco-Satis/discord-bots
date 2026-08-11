"""
Minecraft-Mod-Backend — Dateisystem als Quelle der Wahrheit.

Der frueher hier verwendete JSON-Katalog (data/minecraft_mods.json) war eine
Attrappe: er wurde nie mit den tatsaechlich installierten Dateien abgeglichen,
und der "Installer" hat nie etwas heruntergeladen. Auf BMC5 liegen real 342
Mod-Dateien, von denen der Katalog nichts wusste.

Deshalb: gelistet wird, was im mods-Verzeichnis liegt.

Ein-/Ausschalten nutzt die auf dem Server bereits etablierte Konvention
`<name>.jar` <-> `<name>.jar.disabled` (NeoForge/Forge/Fabric ignorieren
Dateien, die nicht auf .jar enden).

Downloads laufen ueber die offizielle Modrinth-API (api.modrinth.com/v2).
"""

import asyncio
import hashlib
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import aiohttp

from utils.logger import get_logger

logger = get_logger("mods.minecraft")

MODRINTH_API = "https://api.modrinth.com/v2"
USER_AGENT = "Discord_Bots-ModManager/1.0 (self-hosted gameserver)"

DISABLED_SUFFIX = ".disabled"

# Dateinamen ohne Pfadanteile und ohne Traversal.
_SAFE_NAME_RE = re.compile(r"^[^/\\\x00]{1,255}$")


def is_safe_filename(name: str) -> bool:
    """Verhindert Pfad-Traversal bei allem, was aus HTTP/Discord kommt."""
    if not name or not _SAFE_NAME_RE.match(name):
        return False
    return ".." not in name and not name.startswith(".")


def _display_name(filename: str) -> str:
    """Macht aus einem Dateinamen einen lesbaren Mod-Namen."""
    base = filename[: -len(DISABLED_SUFFIX)] if filename.endswith(DISABLED_SUFFIX) else filename
    if base.endswith(".jar"):
        base = base[:-4]
    return base


class MinecraftModFiles:
    """Verwaltet die Mod-Dateien eines Minecraft-Servers."""

    def __init__(self, mods_dir: Path) -> None:
        self.mods_dir = Path(mods_dir)

    # ------------------------------------------------------------------
    # Lesen
    # ------------------------------------------------------------------

    def list_mods(self) -> List[Dict[str, Any]]:
        """Listet alle Mod-Dateien inkl. Aktiv-Status.

        Liest ausschliesslich das Dateisystem — kein Katalog, keine Annahmen.
        """
        if not self.mods_dir.is_dir():
            return []

        mods: List[Dict[str, Any]] = []
        try:
            entries = sorted(self.mods_dir.iterdir(), key=lambda p: p.name.lower())
        except OSError as e:
            logger.warning(f"Mods-Verzeichnis nicht lesbar ({self.mods_dir}): {e}")
            return []

        for path in entries:
            if not path.is_file():
                continue
            name = path.name
            enabled = name.endswith(".jar")
            if not enabled and not name.endswith(".jar" + DISABLED_SUFFIX):
                continue  # keine Mod-Datei

            try:
                size = path.stat().st_size
            except OSError:
                size = 0

            mods.append({
                "mod_id": name,
                "name": _display_name(name),
                "filename": name,
                "enabled": enabled,
                "size_bytes": size,
                "game": "minecraft",
            })
        return mods

    def writable(self) -> Tuple[bool, str]:
        """Prueft, ob im Mod-Verzeichnis geschrieben werden darf."""
        import os
        if not self.mods_dir.is_dir():
            return False, f"Verzeichnis fehlt: {self.mods_dir}"
        if not os.access(self.mods_dir, os.W_OK | os.X_OK):
            return False, (
                f"Kein Schreibrecht auf {self.mods_dir} — noetig ist "
                f"'chmod g+w' (botuser ist in Gruppe minecraft)"
            )
        return True, "beschreibbar"

    # ------------------------------------------------------------------
    # Schreiben
    # ------------------------------------------------------------------

    async def set_enabled(self, filename: str, enabled: bool) -> Tuple[bool, str]:
        """Schaltet eine Mod ein/aus (Umbenennen .jar <-> .jar.disabled)."""
        if not is_safe_filename(filename):
            return False, "Ungueltiger Dateiname"

        ok, msg = self.writable()
        if not ok:
            return False, msg

        src = self.mods_dir / filename
        if not src.is_file():
            return False, f"Datei nicht gefunden: {filename}"

        currently_enabled = filename.endswith(".jar")
        if currently_enabled == enabled:
            return True, f"`{_display_name(filename)}` ist bereits {'aktiv' if enabled else 'inaktiv'}"

        if enabled:
            target_name = filename[: -len(DISABLED_SUFFIX)]
        else:
            target_name = filename + DISABLED_SUFFIX

        dst = self.mods_dir / target_name
        if dst.exists():
            return False, f"Zieldatei existiert bereits: {target_name}"

        try:
            await asyncio.to_thread(src.rename, dst)
        except OSError as e:
            logger.error(f"Umbenennen fehlgeschlagen ({filename}): {e}")
            return False, f"Umbenennen fehlgeschlagen: {e}"

        logger.info(
            f"Mod {'aktiviert' if enabled else 'deaktiviert'}: {_display_name(filename)}"
        )
        return True, (
            f"`{_display_name(filename)}` {'aktiviert' if enabled else 'deaktiviert'} "
            f"— Server-Neustart noetig"
        )

    async def delete_mod(self, filename: str) -> Tuple[bool, str]:
        """Loescht eine Mod-Datei endgueltig."""
        if not is_safe_filename(filename):
            return False, "Ungueltiger Dateiname"

        ok, msg = self.writable()
        if not ok:
            return False, msg

        path = self.mods_dir / filename
        if not path.is_file():
            return False, f"Datei nicht gefunden: {filename}"

        try:
            await asyncio.to_thread(path.unlink)
        except OSError as e:
            return False, f"Loeschen fehlgeschlagen: {e}"

        logger.info(f"Mod geloescht: {filename}")
        return True, f"`{_display_name(filename)}` geloescht — Server-Neustart noetig"

    # ------------------------------------------------------------------
    # Modrinth
    # ------------------------------------------------------------------

    @staticmethod
    async def search(
        query: str, game_version: Optional[str] = None, limit: int = 15
    ) -> List[Dict[str, Any]]:
        """Sucht Mods ueber die offizielle Modrinth-API."""
        if not query or not query.strip():
            return []

        facets = '[["project_type:mod"]]'
        if game_version:
            facets = f'[["project_type:mod"],["versions:{game_version}"]]'

        params = {"query": query.strip(), "limit": str(limit), "facets": facets}
        try:
            async with aiohttp.ClientSession(headers={"User-Agent": USER_AGENT}) as s:
                async with s.get(
                    f"{MODRINTH_API}/search", params=params,
                    timeout=aiohttp.ClientTimeout(total=20),
                ) as resp:
                    if resp.status != 200:
                        logger.warning(f"Modrinth-Suche HTTP {resp.status}")
                        return []
                    data = await resp.json()
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            logger.warning(f"Modrinth-Suche fehlgeschlagen: {e}")
            return []

        return [
            {
                "mod_id": h.get("slug") or h.get("project_id", ""),
                "name": h.get("title", "?"),
                "description": (h.get("description") or "")[:200],
                "downloads": h.get("downloads", 0),
                "game": "minecraft",
            }
            for h in data.get("hits", [])
        ]

    async def install_from_modrinth(
        self, project_id: str, game_version: str, loader: str = "neoforge"
    ) -> Tuple[bool, str]:
        """Laedt eine Mod von Modrinth und legt sie WIRKLICH im mods-Ordner ab.

        Anders als die fruehere Attrappe: mit Download, SHA512-Pruefung und
        echtem Schreibvorgang.
        """
        ok, msg = self.writable()
        if not ok:
            return False, msg

        if not re.match(r"^[A-Za-z0-9_-]{1,64}$", project_id or ""):
            return False, "Ungueltige Projekt-ID"

        loaders = f'["{loader}"]'
        versions = f'["{game_version}"]'
        url = f"{MODRINTH_API}/project/{project_id}/version"

        try:
            async with aiohttp.ClientSession(headers={"User-Agent": USER_AGENT}) as s:
                async with s.get(
                    url, params={"loaders": loaders, "game_versions": versions},
                    timeout=aiohttp.ClientTimeout(total=20),
                ) as resp:
                    if resp.status != 200:
                        return False, f"Mod nicht gefunden (HTTP {resp.status})"
                    releases = await resp.json()

                if not releases:
                    return False, (
                        f"Keine Version fuer {loader}/{game_version} verfuegbar"
                    )

                files = releases[0].get("files") or []
                primary = next((f for f in files if f.get("primary")), files[0] if files else None)
                if not primary or not primary.get("url"):
                    return False, "Kein Download-Link in der Modrinth-Antwort"

                filename = primary.get("filename", f"{project_id}.jar")
                if not is_safe_filename(filename) or not filename.endswith(".jar"):
                    return False, f"Unerwarteter Dateiname: {filename[:60]}"

                target = self.mods_dir / filename
                if target.exists() or (self.mods_dir / (filename + DISABLED_SUFFIX)).exists():
                    return False, f"`{_display_name(filename)}` ist bereits installiert"

                async with s.get(
                    primary["url"], timeout=aiohttp.ClientTimeout(total=300)
                ) as dl:
                    if dl.status != 200:
                        return False, f"Download fehlgeschlagen (HTTP {dl.status})"
                    payload = await dl.read()

        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            return False, f"Modrinth-Zugriff fehlgeschlagen: {e}"

        expected = (primary.get("hashes") or {}).get("sha512")
        if expected:
            actual = hashlib.sha512(payload).hexdigest()
            if actual != expected:
                logger.error(f"SHA512-Mismatch bei {filename} — Download verworfen")
                return False, "Pruefsumme stimmt nicht — Download verworfen"

        try:
            tmp = self.mods_dir / (filename + ".part")
            await asyncio.to_thread(tmp.write_bytes, payload)
            await asyncio.to_thread(tmp.rename, target)
        except OSError as e:
            logger.error(f"Schreiben fehlgeschlagen ({filename}): {e}")
            return False, f"Schreiben fehlgeschlagen: {e}"

        size_mb = len(payload) / (1024 * 1024)
        logger.info(f"Mod installiert: {filename} ({size_mb:.1f} MB)")
        return True, (
            f"`{_display_name(filename)}` installiert ({size_mb:.1f} MB) "
            f"— Server-Neustart noetig"
        )
