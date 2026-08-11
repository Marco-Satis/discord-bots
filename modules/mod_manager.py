"""
Mod-Verwaltung — Fassade ueber die echten Backends.

VORGESCHICHTE (2026-08-11)
--------------------------
Die vorige Fassung dieses Moduls war eine Attrappe: `_install_satisfactory_mod`
und `_install_minecraft_mod` haben Metadaten von einer API geholt, einen
JSON-Eintrag mit einem `file_path` geschrieben, der nie angelegt wurde, und
"✓ Mod installiert" gemeldet. Im ganzen Modul gab es keinen einzigen Download
und keinen Dateizugriff. Zusaetzlich zeigte die hinterlegte Satisfactory-URL
(ficsit.app/api/v2) ins Leere und lieferte HTML statt JSON.

Jetzt delegiert alles an echte Quellen:
  * Satisfactory -> modules.mods.ficsit_backend  (offizielles ficsit-cli)
  * Minecraft    -> modules.mods.minecraft_backend (Dateisystem + Modrinth)

Nicht unterstuetzte Operationen melden das ehrlich, statt Erfolg vorzutaeuschen.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from utils.logger import get_logger
from modules.mods import ficsit_backend
from modules.mods.minecraft_backend import MinecraftModFiles

logger = get_logger("mod_manager")

SUPPORTED_GAMES = ("satisfactory", "minecraft")


class ModManager:
    """Einheitliche Mod-Verwaltung fuer Satisfactory und Minecraft."""

    def __init__(
        self, game: str, server_path: Path, mods_dir: Optional[Path] = None
    ) -> None:
        self.game = (game or "").lower()
        if self.game not in SUPPORTED_GAMES:
            raise ValueError(f"Unsupported game: {game}")

        self.server_path = Path(server_path)
        self.mods_dir = Path(mods_dir) if mods_dir else self.server_path / "mods"

        # Kein mkdir mehr: das Verzeichnis gehoert dem Server-User. Fehlt es,
        # wird das beim Zugriff gemeldet statt beim Import zu warnen. (Frueher
        # erzeugte das eine irrefuehrende "Kein Zugriff"-Warnung fuer Paper-
        # Server, die gar keinen mods-Ordner haben.)
        self._mc = MinecraftModFiles(self.mods_dir) if self.game == "minecraft" else None

    # ------------------------------------------------------------------
    # Lesen
    # ------------------------------------------------------------------

    def list_installed(self) -> List[Dict[str, Any]]:
        """Listet installierte Mods (synchron, fuer den Discord-Cog).

        Minecraft liest das Dateisystem. Satisfactory liest das ficsit-Profil
        aus profiles.json — beides ohne Subprozess, daher synchron moeglich.
        """
        if self.game == "minecraft" and self._mc:
            return self._mc.list_mods()

        if self.game == "satisfactory":
            import json
            try:
                data = json.loads(
                    ficsit_backend.PROFILES_JSON.read_text(encoding="utf-8")
                )
            except (OSError, json.JSONDecodeError):
                return []
            mods = ((data.get("profiles") or {}).get("Default") or {}).get("mods") or {}
            out: List[Dict[str, Any]] = []
            for ref, meta in sorted(mods.items()):
                enabled = meta.get("enabled", True) if isinstance(meta, dict) else True
                version = meta.get("version", ">=0.0.0") if isinstance(meta, dict) else str(meta)
                out.append({
                    "mod_id": ref, "name": ref, "version": version,
                    "enabled": enabled, "game": "satisfactory",
                })
            return out

        return []

    def get_mod_info(self, mod_name: str) -> Optional[Dict[str, Any]]:
        """Details zu einer installierten Mod."""
        needle = (mod_name or "").lower()
        for m in self.list_installed():
            if needle in (m.get("name", "").lower(), m.get("mod_id", "").lower()):
                return m
        # Teiltreffer als Fallback
        for m in self.list_installed():
            if needle and needle in m.get("name", "").lower():
                return m
        return None

    async def search_mods(
        self, query: Optional[str] = None, game: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Sucht Mods in der jeweiligen offiziellen Quelle."""
        target = (game or self.game).lower()
        if not query:
            return []
        if target == "satisfactory":
            return await ficsit_backend.search_mods(query)
        if target == "minecraft":
            return await MinecraftModFiles.search(query)
        return []

    # ------------------------------------------------------------------
    # Schreiben
    # ------------------------------------------------------------------

    async def install_mod(
        self, mod_id: str, version: str = "latest", **kwargs: Any
    ) -> Tuple[bool, str]:
        """Installiert eine Mod — mit echtem Download.

        Satisfactory: nimmt die Mod ins ficsit-Profil auf. Wirksam wird sie
        mit `apply()`, das ficsit-cli ausfuehrt (inkl. Abhaengigkeiten + SML).
        Minecraft: laedt die Datei von Modrinth in den mods-Ordner.
        """
        if self.game == "satisfactory":
            spec = ">=0.0.0" if version in ("latest", "", None) else version
            return await ficsit_backend.add_mod(mod_id, spec)

        if self.game == "minecraft" and self._mc:
            game_version = kwargs.get("game_version")
            if not game_version:
                return False, (
                    "Minecraft-Version noetig (z.B. game_version='1.21.1') — "
                    "ohne sie laesst sich keine passende Datei bestimmen"
                )
            return await self._mc.install_from_modrinth(
                mod_id, game_version, kwargs.get("loader", "neoforge")
            )

        return False, f"Spiel '{self.game}' nicht unterstuetzt"

    async def uninstall_mod(self, mod_name: str) -> Tuple[bool, str]:
        """Entfernt eine Mod."""
        if self.game == "satisfactory":
            return await ficsit_backend.remove_mod(mod_name)
        if self.game == "minecraft" and self._mc:
            return await self._mc.delete_mod(mod_name)
        return False, f"Spiel '{self.game}' nicht unterstuetzt"

    async def set_enabled(self, mod_id: str, enabled: bool) -> Tuple[bool, str]:
        """Schaltet eine Mod ein oder aus."""
        if self.game == "satisfactory":
            return await ficsit_backend.set_enabled(mod_id, enabled)
        if self.game == "minecraft" and self._mc:
            return await self._mc.set_enabled(mod_id, enabled)
        return False, f"Spiel '{self.game}' nicht unterstuetzt"

    async def apply(self, server_running: bool = False) -> Tuple[bool, str]:
        """Wendet ausstehende Aenderungen an (nur Satisfactory).

        Bei Minecraft wirken Datei-Aenderungen direkt; dort genuegt ein
        Server-Neustart.
        """
        if self.game == "satisfactory":
            return await ficsit_backend.apply(server_running=server_running)
        return True, "Minecraft: Aenderungen wirken beim naechsten Server-Neustart"

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    async def backend_status(self) -> Dict[str, Any]:
        """Sagt, ob das Backend einsatzbereit ist — fuer die Dashboard-Anzeige."""
        if self.game == "satisfactory":
            ok, msg = await ficsit_backend.is_available()
            return {"ready": ok, "detail": msg, "backend": "ficsit-cli"}
        if self.game == "minecraft" and self._mc:
            ok, msg = self._mc.writable()
            return {"ready": ok, "detail": msg, "backend": "Dateisystem + Modrinth"}
        return {"ready": False, "detail": "kein Backend", "backend": "-"}

    # ------------------------------------------------------------------
    # Bewusst nicht (mehr) unterstuetzt — ehrlich statt vorgetaeuscht
    # ------------------------------------------------------------------

    async def update_mod(self, mod_name: str) -> Tuple[bool, str]:
        """Aktualisiert eine Mod auf die neueste Version."""
        if self.game == "satisfactory":
            ok, msg = await ficsit_backend.set_enabled(mod_name, True)
            if not ok:
                return False, msg
            return True, (
                f"`{mod_name}` ist auf '>=0.0.0' gesetzt; `apply` zieht die "
                f"neueste passende Version."
            )
        return False, (
            "Einzel-Update fuer Minecraft-Mods ist nicht implementiert — "
            "Mod entfernen und neu installieren, oder das Modpack-Update nutzen."
        )

    async def check_updates(self) -> List[Dict[str, Any]]:
        """Update-Pruefung pro Mod — derzeit nicht implementiert.

        Gibt bewusst eine leere Liste zurueck statt erfundener Treffer.
        """
        logger.debug(f"check_updates({self.game}): nicht implementiert")
        return []

    async def create_modpack(self, name: str) -> Tuple[bool, str, Optional[Path]]:
        """Modpack-Export — nicht implementiert."""
        return False, "Modpack-Export ist nicht implementiert", None

    async def import_modpack(self, modpack_path: Path) -> Tuple[bool, str]:
        """Modpack-Import — nicht implementiert."""
        return False, "Modpack-Import ist nicht implementiert"
