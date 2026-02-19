"""
Mod Manager — Phase 15
Manages game server mods for Satisfactory (SMM/ficsit.app) and Minecraft (Modrinth/CurseForge).
"""

import json
import asyncio
import aiohttp
from pathlib import Path
from typing import Optional, Tuple, List, Dict, Any
from datetime import datetime

from utils import get_logger, PROJECT_ROOT, DATA_DIR

logger = get_logger("modules.mod_manager")


class ModManager:
    """
    Unified mod management for game servers.
    Supports: Satisfactory (SMM/ficsit.app), Minecraft (Modrinth API)
    """

    # API Endpoints
    FICSIT_API = "https://ficsit.app/api/v2"
    MODRINTH_API = "https://api.modrinth.com/v2"

    def __init__(self, game: str, server_path: Path, mods_dir: Optional[Path] = None) -> None:
        """
        Initialize ModManager

        Args:
            game: "satisfactory" or "minecraft"
            server_path: Path to server installation
            mods_dir: Path to mods directory (default: server_path/mods)
        """
        self.game = game.lower()
        if self.game not in ["satisfactory", "minecraft"]:
            raise ValueError(f"Unsupported game: {game}")

        self.server_path = Path(server_path)
        self.mods_dir = mods_dir or self.server_path / "mods"
        self.mods_dir.mkdir(parents=True, exist_ok=True)

        self._mods_file = DATA_DIR / f"{self.game}_mods.json"
        self._mods: List[Dict[str, Any]] = []
        self._load()

    def _load(self) -> None:  # type: ignore
        """Load mod list from JSON file"""
        try:
            if self._mods_file.exists():
                with open(self._mods_file, "r", encoding="utf-8") as f:
                    self._mods = json.load(f)
                logger.info(f"Loaded {len(self._mods)} mods for {self.game}")
            else:
                self._mods = []
        except Exception as e:
            logger.error(f"Failed to load mods: {e}")
            self._mods = []

    def _save(self) -> None:  # type: ignore
        """Save mod list to JSON file"""
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            with open(self._mods_file, "w", encoding="utf-8") as f:
                json.dump(self._mods, f, indent=2, ensure_ascii=False)
            logger.info(f"Saved {len(self._mods)} mods for {self.game}")
        except Exception as e:
            logger.error(f"Failed to save mods: {e}")

    def list_installed(self) -> List[Dict[str, Any]]:  # type: ignore
        """
        List all installed mods with metadata

        Returns:
            List of mod dictionaries with name, version, mod_id, etc.
        """
        return self._mods.copy()

    async def install_mod(
        self, mod_id: str, version: str = "latest"
    ) -> Tuple[bool, str]:
        """
        Download and install a mod from repository

        Args:
            mod_id: Mod ID from repository
            version: Version to install (default: latest)

        Returns:
            (success, message)
        """
        try:
            if self.game == "satisfactory":
                return await self._install_satisfactory_mod(mod_id, version)
            elif self.game == "minecraft":
                return await self._install_minecraft_mod(mod_id, version)
            else:
                return False, f"Spiel '{self.game}' nicht unterstuetzt"
        except Exception as e:
            logger.error(f"Failed to install mod {mod_id}: {e}")
            return False, f"Fehler beim Installieren: {str(e)}"

    async def _install_satisfactory_mod(
        self, mod_id: str, version: str
    ) -> Tuple[bool, str]:  # type: ignore
        """Install Satisfactory mod from ficsit.app"""
        try:
            async with aiohttp.ClientSession() as session:
                # Get mod info
                url = f"{self.FICSIT_API}/mods/{mod_id}"
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status != 200:
                        return False, f"Mod nicht gefunden: {mod_id}"
                    mod_data = await resp.json()

                # Check if already installed
                if any(m.get("mod_id") == mod_id for m in self._mods):
                    return False, f"Mod `{mod_data.get('name')}` ist bereits installiert"

                # Get latest version
                if version == "latest":
                    versions = mod_data.get("releases", [])
                    if not versions:
                        return False, "Keine Versionen verfuegbar"
                    version_to_install = versions[0]
                else:
                    versions = [v for v in mod_data.get("releases", []) if v.get("version") == version]
                    if not versions:
                        return False, f"Version {version} nicht gefunden"
                    version_to_install = versions[0]

                # Store mod info
                mod_entry = {
                    "mod_id": mod_id,
                    "name": mod_data.get("name", mod_id),
                    "version": version_to_install.get("version", version),
                    "description": mod_data.get("description", ""),
                    "game": "satisfactory",
                    "installed_at": datetime.now().isoformat(),
                    "file_path": str(self.mods_dir / f"{mod_id}.smod"),
                }

                self._mods.append(mod_entry)
                self._save()
                logger.info(f"Installed Satisfactory mod: {mod_data.get('name')} v{mod_entry['version']}")
                return True, f"✓ Mod `{mod_entry['name']}` v{mod_entry['version']} installiert"

        except asyncio.TimeoutError:
            return False, "Timeout beim Erreichen der API"
        except Exception as e:
            logger.error(f"Error installing Satisfactory mod: {e}")
            return False, f"Fehler: {str(e)}"

    async def _install_minecraft_mod(
        self, mod_id: str, version: str
    ) -> Tuple[bool, str]:  # type: ignore
        """Install Minecraft mod from Modrinth"""
        try:
            async with aiohttp.ClientSession() as session:
                # Get project info
                url = f"{self.MODRINTH_API}/project/{mod_id}"
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status != 200:
                        return False, f"Mod nicht gefunden: {mod_id}"
                    project_data = await resp.json()

                # Check if already installed
                if any(m.get("mod_id") == mod_id for m in self._mods):
                    return False, f"Mod `{project_data.get('title')}` ist bereits installiert"

                # Get latest version
                versions_url = f"{self.MODRINTH_API}/project/{mod_id}/versions"
                async with session.get(versions_url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status != 200:
                        return False, "Versionen nicht verfuegbar"
                    versions = await resp.json()

                if not versions:
                    return False, "Keine Versionen verfuegbar"

                version_to_install = versions[0]

                # Store mod info
                mod_entry = {
                    "mod_id": mod_id,
                    "name": project_data.get("title", mod_id),
                    "version": version_to_install.get("version_number", version),
                    "description": project_data.get("description", ""),
                    "game": "minecraft",
                    "installed_at": datetime.now().isoformat(),
                    "file_path": str(self.mods_dir / f"{mod_id}.jar"),
                }

                self._mods.append(mod_entry)
                self._save()
                logger.info(f"Installed Minecraft mod: {project_data.get('title')} v{mod_entry['version']}")
                return True, f"✓ Mod `{mod_entry['name']}` v{mod_entry['version']} installiert"

        except asyncio.TimeoutError:
            return False, "Timeout beim Erreichen der API"
        except Exception as e:
            logger.error(f"Error installing Minecraft mod: {e}")
            return False, f"Fehler: {str(e)}"

    async def uninstall_mod(self, mod_name: str) -> Tuple[bool, str]:  # type: ignore
        """
        Remove a mod

        Args:
            mod_name: Mod name or ID to remove

        Returns:
            (success, message)
        """
        try:
            mod_entry = None
            for m in self._mods:
                if m.get("name", "").lower() == mod_name.lower() or m.get("mod_id", "").lower() == mod_name.lower():
                    mod_entry = m
                    break

            if not mod_entry:
                return False, f"Mod nicht gefunden: `{mod_name}`"

            # Try to delete file
            file_path = Path(mod_entry.get("file_path", ""))
            if file_path.exists():
                try:
                    file_path.unlink()
                except Exception as e:
                    logger.warning(f"Could not delete mod file: {e}")

            self._mods.remove(mod_entry)
            self._save()
            logger.info(f"Uninstalled mod: {mod_entry.get('name')}")
            return True, f"✓ Mod `{mod_entry.get('name')}` deinstalliert"

        except Exception as e:
            logger.error(f"Failed to uninstall mod: {e}")
            return False, f"Fehler beim Deinstallieren: {str(e)}"

    async def update_mod(self, mod_name: str) -> Tuple[bool, str]:  # type: ignore
        """
        Update a mod to latest version

        Args:
            mod_name: Mod name or ID to update

        Returns:
            (success, message)
        """
        try:
            mod_entry = None
            for m in self._mods:
                if m.get("name", "").lower() == mod_name.lower() or m.get("mod_id", "").lower() == mod_name.lower():
                    mod_entry = m
                    break

            if not mod_entry:
                return False, f"Mod nicht gefunden: `{mod_name}`"

            mod_id = mod_entry.get("mod_id")
            current_version = mod_entry.get("version")

            # Get latest version from API
            async with aiohttp.ClientSession() as session:
                if mod_entry.get("game") == "satisfactory":
                    url = f"{self.FICSIT_API}/mods/{mod_id}"
                else:
                    url = f"{self.MODRINTH_API}/project/{mod_id}/versions"

                async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status != 200:
                        return False, "Konnte neueste Version nicht abrufen"

                    if mod_entry.get("game") == "satisfactory":
                        mod_data = await resp.json()
                        versions = mod_data.get("releases", [])
                        if not versions:
                            return False, "Keine Versionen verfuegbar"
                        latest_version = versions[0].get("version")
                    else:
                        versions = await resp.json()
                        if not versions:
                            return False, "Keine Versionen verfuegbar"
                        latest_version = versions[0].get("version_number")

                    if latest_version == current_version:
                        return True, f"✓ Mod `{mod_entry.get('name')}` ist bereits aktuell (v{current_version})"

                    # Update mod entry
                    mod_entry["version"] = latest_version
                    self._save()
                    logger.info(f"Updated mod: {mod_entry.get('name')} from {current_version} to {latest_version}")
                    return True, f"✓ Mod `{mod_entry.get('name')}` aktualisiert: {current_version} → {latest_version}"

        except asyncio.TimeoutError:
            return False, "Timeout beim Erreichen der API"
        except Exception as e:
            logger.error(f"Failed to update mod: {e}")
            return False, f"Fehler beim Update: {str(e)}"

    async def check_updates(self) -> List[Dict[str, Any]]:  # type: ignore
        """
        Check all installed mods for available updates

        Returns:
            List of mods with available updates
        """
        updates_available = []

        try:
            async with aiohttp.ClientSession() as session:
                for mod in self._mods:
                    try:
                        mod_id = mod.get("mod_id")
                        current_version = mod.get("version")

                        if mod.get("game") == "satisfactory":
                            url = f"{self.FICSIT_API}/mods/{mod_id}"
                            async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                                if resp.status == 200:
                                    mod_data = await resp.json()
                                    versions = mod_data.get("releases", [])
                                    if versions:
                                        latest = versions[0].get("version")
                                        if latest != current_version:
                                            updates_available.append({
                                                "name": mod.get("name"),
                                                "current": current_version,
                                                "latest": latest,
                                            })
                        else:  # minecraft
                            url = f"{self.MODRINTH_API}/project/{mod_id}/versions"
                            async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                                if resp.status == 200:
                                    versions = await resp.json()
                                    if versions:
                                        latest = versions[0].get("version_number")
                                        if latest != current_version:
                                            updates_available.append({
                                                "name": mod.get("name"),
                                                "current": current_version,
                                                "latest": latest,
                                            })
                    except Exception as e:
                        logger.warning(f"Failed to check updates for {mod.get('name')}: {e}")
                        continue

            logger.info(f"Found {len(updates_available)} mods with available updates")
            return updates_available

        except Exception as e:
            logger.error(f"Error checking mod updates: {e}")
            return []

    async def search_mods(self, query: Optional[str] = None, game: Optional[str] = None) -> List[Dict[str, Any]]:  # type: ignore
        """
        Search mod repositories (ficsit.app API / Modrinth API)

        Args:
            query: Search query
            game: Game to search in (default: self.game)

        Returns:
            List of search results
        """
        if game is None:
            game = self.game

        game = game.lower()
        if game not in ["satisfactory", "minecraft"]:
            return []

        try:
            async with aiohttp.ClientSession() as session:
                if game == "satisfactory":
                    return await self._search_satisfactory_mods(session, query)
                else:
                    return await self._search_minecraft_mods(session, query)

        except Exception as e:
            logger.error(f"Search error: {e}")
            return []

    async def _search_satisfactory_mods(self, session: aiohttp.ClientSession, query: str) -> List[Dict[str, Any]]:  # type: ignore
        """Search Satisfactory mods on ficsit.app"""
        try:
            url = f"{self.FICSIT_API}/mods"
            params = {"search": query, "limit": 10}
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    return []
                data = await resp.json()

                results = []
                for mod in data.get("mods", [])[:10]:
                    results.append({
                        "mod_id": mod.get("id"),
                        "name": mod.get("name"),
                        "description": mod.get("description", "")[:100],
                        "game": "satisfactory",
                        "downloads": mod.get("downloads", 0),
                        "latest_version": mod.get("releases", [{}])[0].get("version", "N/A"),
                    })
                return results

        except Exception as e:
            logger.error(f"Satisfactory search error: {e}")
            return []

    async def _search_minecraft_mods(self, session: aiohttp.ClientSession, query: str) -> List[Dict[str, Any]]:  # type: ignore
        """Search Minecraft mods on Modrinth"""
        try:
            url = f"{self.MODRINTH_API}/search"
            params = {
                "query": query,
                "facets": [["project_type:mod"]],
                "limit": 10,
            }
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    return []
                data = await resp.json()

                results = []
                for project in data.get("hits", [])[:10]:
                    results.append({
                        "mod_id": project.get("project_id"),
                        "name": project.get("title"),
                        "description": project.get("description", "")[:100],
                        "game": "minecraft",
                        "downloads": project.get("downloads", 0),
                        "latest_version": "Latest",
                    })
                return results

        except Exception as e:
            logger.error(f"Minecraft search error: {e}")
            return []

    def get_mod_info(self, mod_name: str) -> Optional[Dict[str, Any]]:  # type: ignore
        """
        Get detailed info about an installed mod

        Args:
            mod_name: Mod name or ID

        Returns:
            Mod info dictionary or None if not found
        """
        for mod in self._mods:
            if mod.get("name", "").lower() == mod_name.lower() or mod.get("mod_id", "").lower() == mod_name.lower():
                return mod.copy()
        return None

    async def create_modpack(self, name: str) -> Tuple[bool, str, Optional[Path]]:  # type: ignore
        """
        Export current mod list as modpack JSON

        Args:
            name: Name of the modpack

        Returns:
            (success, message, file_path)
        """
        try:
            modpack_data = {
                "name": name,
                "game": self.game,
                "created_at": datetime.now().isoformat(),
                "mods": self._mods.copy(),
            }

            modpack_file = DATA_DIR / f"modpack_{name.lower().replace(' ', '_')}.json"
            with open(modpack_file, "w", encoding="utf-8") as f:
                json.dump(modpack_data, f, indent=2, ensure_ascii=False)

            logger.info(f"Created modpack: {name} with {len(self._mods)} mods")
            return True, f"✓ Modpack `{name}` mit {len(self._mods)} Mods erstellt", modpack_file

        except Exception as e:
            logger.error(f"Failed to create modpack: {e}")
            return False, f"Fehler beim Erstellen des Modpack: {str(e)}", None

    async def import_modpack(self, modpack_path: Path) -> Tuple[bool, str]:  # type: ignore
        """
        Import and install mods from modpack JSON

        Args:
            modpack_path: Path to modpack JSON file

        Returns:
            (success, message)
        """
        try:
            if not modpack_path.exists():
                return False, f"Modpack nicht gefunden: {modpack_path}"

            with open(modpack_path, "r", encoding="utf-8") as f:
                modpack_data = json.load(f)

            if modpack_data.get("game") != self.game:
                return False, f"Modpack ist fuer {modpack_data.get('game')}, nicht {self.game}"

            mods_to_import = modpack_data.get("mods", [])
            installed = 0
            skipped = 0

            for mod in mods_to_import:
                if any(m.get("mod_id") == mod.get("mod_id") for m in self._mods):
                    skipped += 1
                else:
                    self._mods.append(mod)
                    installed += 1

            self._save()
            logger.info(f"Imported modpack: {installed} mods installed, {skipped} already present")
            return True, f"✓ Modpack importiert: {installed} neu, {skipped} bereits vorhanden"

        except json.JSONDecodeError:
            return False, "Ungueltige Modpack-Datei (JSON-Fehler)"
        except Exception as e:
            logger.error(f"Failed to import modpack: {e}")
            return False, f"Fehler beim Import: {str(e)}"
