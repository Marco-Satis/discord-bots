"""
Blueprint Manager for Satisfactory Server
Handles upload, validation, listing, download, and deletion of blueprints.
Erkennt automatisch den aktiven Welt-Ordner und synchronisiert Blueprints
bei Welt-Wechsel.

Blueprint-Struktur:
  blueprints/
    1.1/          <-- Welt-Ordner (pro Session/Welt)
      Start.sbp
      Start.sbpcfg
    Kreativ/
      Test.sbp
      Test.sbpcfg
"""

import io
import json
import shutil
import zipfile
import aiofiles
from pathlib import Path
from typing import List, Optional, Tuple, Dict, Any
from datetime import datetime
from utils.logger import get_logger
from utils.config import DATA_DIR

logger = get_logger("satisfactory.blueprints")

BLUEPRINT_DB = DATA_DIR / "blueprints.json"

CATEGORIES = [
    "Produktion",
    "Logistik",
    "Deko/Architektur",
    "Energie",
    "Transport/Zuege",
    "Sonstiges"
]


class BlueprintManager:
    """Manage Satisfactory blueprint files with metadata tracking.
    Erkennt automatisch den aktiven Welt-Ordner und kopiert
    Blueprints bei Welt-Wechsel automatisch mit."""

    def __init__(self, blueprint_path: Path) -> None:
        self.base_path = blueprint_path  # .../SaveGames/blueprints/
        self._data: Dict[str, Any] = {"blueprints": []}
        self._active_world: Optional[str] = None

    @property
    def active_world_path(self) -> Path:
        """Gibt den Pfad zum aktiven Welt-Ordner zurueck."""
        world = self._detect_active_world()
        return self.base_path / world

    # Legacy-Kompatibilitaet: blueprint_path zeigt auf den aktiven Ordner
    @property
    def blueprint_path(self) -> Path:
        return self.active_world_path

    def _detect_active_world(self) -> str:
        """Erkennt den aktiven Welt-Ordner (neuester nach Änderungszeit).
        Cached das Ergebnis in _active_world."""
        try:
            if not self.base_path.exists():
                self.base_path.mkdir(parents=True, exist_ok=True)
                return "default"

            worlds = [
                d for d in self.base_path.iterdir()
                if d.is_dir()
            ]

            if not worlds:
                return "default"

            # Neuester Ordner = aktive Welt
            newest = max(worlds, key=lambda d: d.stat().st_mtime)
            world_name = newest.name

            # Welt-Wechsel erkennen und Blueprints synchronisieren
            if self._active_world and self._active_world != world_name:
                logger.info(
                    f"Neue Welt erkannt: {self._active_world} -> {world_name}"
                )
                self._sync_blueprints(self._active_world, world_name)

            self._active_world = world_name
            return world_name

        except Exception as e:
            logger.error(f"Welt-Erkennung fehlgeschlagen: {e}")
            return self._active_world or "default"

    def _sync_blueprints(self, old_world: str, new_world: str) -> None:
        """Kopiert alle Blueprints vom alten in den neuen Welt-Ordner."""
        old_path = self.base_path / old_world
        new_path = self.base_path / new_world

        if not old_path.exists():
            return

        new_path.mkdir(parents=True, exist_ok=True)

        copied = 0
        skipped = 0
        for src_file in old_path.iterdir():
            if not src_file.is_file():
                continue
            if not (src_file.suffix in (".sbp", ".sbpcfg")):
                continue

            dest_file = new_path / src_file.name
            if dest_file.exists():
                skipped += 1
                continue

            try:
                shutil.copy2(src_file, dest_file)
                copied += 1
            except Exception as e:
                logger.warning(f"Blueprint-Sync fehlgeschlagen fuer {src_file.name}: {e}")

        if copied > 0:
            logger.info(
                f"Blueprint-Sync: {copied} Dateien von '{old_world}' nach "
                f"'{new_world}' kopiert ({skipped} uebersprungen)"
            )

    def get_worlds(self) -> List[Dict[str, Any]]:
        """Listet alle verfuegbaren Welt-Ordner auf."""
        worlds = []
        try:
            if not self.base_path.exists():
                return worlds
            for d in sorted(self.base_path.iterdir()):
                if not d.is_dir():
                    continue
                bp_count = len(list(d.glob("*.sbp")))
                worlds.append({
                    "name": d.name,
                    "blueprint_count": bp_count,
                    "is_active": d.name == self._active_world,
                    "modified": datetime.fromtimestamp(
                        d.stat().st_mtime
                    ).isoformat(),
                })
        except Exception as e:
            logger.error(f"Welt-Liste fehlgeschlagen: {e}")
        return worlds

    def sync_all_worlds(self) -> Dict[str, int]:
        """Synchronisiert Blueprints aus allen Welten in die aktive Welt.
        Gibt {world_name: copied_count} zurueck."""
        active = self._detect_active_world()
        active_path = self.base_path / active
        active_path.mkdir(parents=True, exist_ok=True)

        result = {}
        try:
            for d in self.base_path.iterdir():
                if not d.is_dir() or d.name == active:
                    continue

                copied = 0
                for src_file in d.iterdir():
                    if not src_file.is_file():
                        continue
                    if src_file.suffix not in (".sbp", ".sbpcfg"):
                        continue

                    dest_file = active_path / src_file.name
                    if dest_file.exists():
                        continue

                    try:
                        shutil.copy2(src_file, dest_file)
                        copied += 1
                    except Exception as e:
                        logger.warning(f"Sync fehlgeschlagen: {src_file.name}: {e}")

                if copied > 0:
                    result[d.name] = copied

        except Exception as e:
            logger.error(f"sync_all_worlds fehlgeschlagen: {e}")

        return result

    @staticmethod
    def _file_hash(path: Path) -> str:
        """SHA256 einer Datei (chunked). Leerer String wenn nicht lesbar."""
        import hashlib
        try:
            h = hashlib.sha256()
            with path.open("rb") as f:
                for chunk in iter(lambda: f.read(65536), b""):
                    h.update(chunk)
            return h.hexdigest()
        except Exception as e:
            logger.warning(f"Hash fehlgeschlagen fuer {path}: {e}")
            return ""

    def categorize_migration(
        self, source: str, target: str
    ) -> Dict[str, Any]:
        """Kategorisiert Blueprints fuer Migration source -> target.

        Returns dict:
            {"new": [names], "identical": [names], "conflict": [names],
             "error": "..." (optional)}

        - new       : im Ziel nicht vorhanden
        - identical : im Ziel vorhanden, .sbp-Hash gleich (echtes Duplikat)
        - conflict  : im Ziel vorhanden, .sbp-Hash unterschiedlich
        """
        result: Dict[str, Any] = {"new": [], "identical": [], "conflict": []}

        if Path(source).name != source or Path(target).name != target:
            result["error"] = "Ungueltiger Welt-Name (Pfad-Traversal)."
            return result
        if source == target:
            result["error"] = "Quell- und Ziel-Welt sind identisch."
            return result

        src_path = self.base_path / source
        dst_path = self.base_path / target
        if not src_path.is_dir():
            result["error"] = f"Quell-Welt '{source}' existiert nicht."
            return result

        for sbp_file in sorted(src_path.glob("*.sbp")):
            name = sbp_file.stem
            dst_sbp = dst_path / f"{name}.sbp"
            if not dst_sbp.exists():
                result["new"].append(name)
                continue
            # Hash-Vergleich der .sbp-Datei
            if self._file_hash(sbp_file) == self._file_hash(dst_sbp):
                result["identical"].append(name)
            else:
                result["conflict"].append(name)

        return result

    def delete_from_world(
        self, world: str, names: List[str]
    ) -> Tuple[int, List[str]]:
        """Loescht Blueprints (.sbp + .sbpcfg) gezielt aus EINEM Welt-Ordner.

        Returns (deleted_count, errors).
        """
        errors: List[str] = []
        deleted = 0

        if Path(world).name != world:
            return 0, ["Ungueltiger Welt-Name (Pfad-Traversal)."]

        world_path = self.base_path / world
        if not world_path.is_dir():
            return 0, [f"Welt '{world}' existiert nicht."]

        for name in names:
            safe = Path(name).name
            if safe != name:
                errors.append(f"{name}: ungueltiger Name")
                continue
            removed = False
            for suffix in (".sbp", ".sbpcfg"):
                f = world_path / f"{safe}{suffix}"
                if f.exists():
                    try:
                        f.unlink()
                        removed = True
                    except Exception as e:
                        errors.append(f"{f.name}: {e}")
            if removed:
                deleted += 1

        logger.info(
            f"Blueprint delete_from_world '{world}': {deleted} geloescht, "
            f"{len(errors)} Fehler"
        )
        return deleted, errors

    def migrate_world(
        self, source: str, target: str, overwrite: bool = False,
        only_names: Optional[List[str]] = None,
    ) -> Tuple[List[str], List[str], List[str]]:
        """Migriert Blueprints (.sbp + .sbpcfg) von Quell- in Ziel-Welt.

        Args:
            source: Name des Quell-Welt-Ordners
            target: Name des Ziel-Welt-Ordners
            overwrite: bei False werden existierende Blueprints uebersprungen
                       (Default), bei True ueberschrieben
            only_names: wenn gesetzt, nur diese Blueprint-Namen migrieren
                        (fuer gezieltes Ueberschreiben nach Button-Entscheidung)

        Returns:
            (copied_names, skipped_names, errors) — kopierte Blueprint-Namen,
            uebersprungene Namen (existieren im Ziel), Fehler-Liste.
            Namen sind Blueprint-Basisnamen (ohne Endung), dedupliziert.
        """
        copied: set = set()
        skipped: set = set()
        errors: List[str] = []

        # Pfad-Traversal-Schutz: nur Ordner-Namen erlauben
        if Path(source).name != source or Path(target).name != target:
            return [], [], ["Ungueltiger Welt-Name (Pfad-Traversal)."]

        src_path = self.base_path / source
        dst_path = self.base_path / target

        if not src_path.is_dir():
            return [], [], [f"Quell-Welt '{source}' existiert nicht."]
        if source == target:
            return [], [], ["Quell- und Ziel-Welt sind identisch."]

        dst_path.mkdir(parents=True, exist_ok=True)

        name_filter = set(only_names) if only_names is not None else None

        for src_file in src_path.iterdir():
            if not src_file.is_file():
                continue
            if src_file.suffix not in (".sbp", ".sbpcfg"):
                continue

            bp_name = src_file.stem  # Basisname ohne Endung
            if name_filter is not None and bp_name not in name_filter:
                continue

            dest_file = dst_path / src_file.name
            if dest_file.exists() and not overwrite:
                skipped.add(bp_name)
                continue

            try:
                shutil.copy2(src_file, dest_file)
                copied.add(bp_name)
            except Exception as e:
                errors.append(f"{src_file.name}: {e}")
                logger.warning(f"Migrate fehlgeschlagen fuer {src_file.name}: {e}")

        # Namen die kopiert wurden nicht zusaetzlich als skipped melden
        skipped -= copied

        logger.info(
            f"Blueprint-Migration: {len(copied)} kopiert, {len(skipped)} uebersprungen, "
            f"{len(errors)} Fehler ('{source}' -> '{target}', overwrite={overwrite})"
        )
        return sorted(copied), sorted(skipped), errors

    async def load(self) -> None:
        """Load blueprint database und erkennt aktive Welt."""
        try:
            if BLUEPRINT_DB.exists():
                async with aiofiles.open(BLUEPRINT_DB, "r", encoding="utf-8") as f:
                    content = await f.read()
                    self._data = json.loads(content)
            else:
                await self._save()
        except Exception as e:
            logger.error(f"Failed to load blueprint DB: {e}")

        # Aktive Welt erkennen und Blueprints synchronisieren
        world = self._detect_active_world()
        logger.info(f"Blueprint-Manager geladen, aktive Welt: {world}")

        # Beim Start: Alle Blueprints aus allen Welten in die aktive Welt kopieren
        synced = self.sync_all_worlds()
        if synced:
            total = sum(synced.values())
            logger.info(
                f"Blueprint-Sync beim Start: {total} Dateien aus "
                f"{len(synced)} Welten in '{world}' kopiert"
            )

    async def _save(self) -> None:
        """Save blueprint database"""
        try:
            BLUEPRINT_DB.parent.mkdir(parents=True, exist_ok=True)
            async with aiofiles.open(BLUEPRINT_DB, "w", encoding="utf-8") as f:
                await f.write(json.dumps(self._data, indent=2, ensure_ascii=False))
        except Exception as e:
            logger.error(f"Failed to save blueprint DB: {e}")

    @staticmethod
    def validate_files(filenames: List[str]) -> Tuple[bool, str, List[Tuple[str, str]]]:
        """Validate blueprint file pairs.
        Returns (valid, error_message, list_of_pairs[(sbp, sbpcfg)])"""
        sbp_files = [f for f in filenames if f.endswith(".sbp")]
        cfg_files = [f for f in filenames if f.endswith(".sbpcfg")]

        if not sbp_files and not cfg_files:
            return False, "Keine Blueprint-Dateien gefunden (.sbp + .sbpcfg erforderlich)", []

        pairs = []
        errors = []

        for sbp in sbp_files:
            base = sbp[:-4]  # Remove .sbp
            expected_cfg = base + ".sbpcfg"
            if expected_cfg in cfg_files:
                pairs.append((sbp, expected_cfg))
            else:
                errors.append(f"Fehlende .sbpcfg fuer: {sbp}")

        for cfg in cfg_files:
            base = cfg[:-7]  # Remove .sbpcfg
            expected_sbp = base + ".sbp"
            if expected_sbp not in sbp_files:
                errors.append(f"Fehlende .sbp fuer: {cfg}")

        if errors:
            return False, "\n".join(errors), []

        if not pairs:
            return False, "Keine gueltigen Blueprint-Paare gefunden.", []

        return True, "", pairs

    async def add_blueprint(self, name: str, category: str,
                            uploader_id: int, uploader_name: str,
                            sbp_data: bytes, cfg_data: bytes) -> Tuple[bool, str]:
        """Save a blueprint pair to ALL Welt-Ordner and register in DB."""
        try:
            # Sanitize name to prevent path traversal
            safe_name = Path(name).name
            if not safe_name or safe_name != name or "/" in name or "\\" in name or ".." in name:
                return False, "Ungueltiger Blueprint-Name!"

            # In ALLE Welt-Ordner hochladen
            worlds_written = []
            for world_dir in self.base_path.iterdir():
                if not world_dir.is_dir():
                    continue

                sbp_path = world_dir / f"{safe_name}.sbp"
                cfg_path = world_dir / f"{safe_name}.sbpcfg"

                if sbp_path.exists():
                    worlds_written.append(f"{world_dir.name} (existiert)")
                    continue

                try:
                    async with aiofiles.open(sbp_path, "wb") as f:
                        await f.write(sbp_data)
                    async with aiofiles.open(cfg_path, "wb") as f:
                        await f.write(cfg_data)
                    worlds_written.append(world_dir.name)
                except Exception as we:
                    logger.warning(f"Blueprint-Write in {world_dir.name} fehlgeschlagen: {we}")

            if not worlds_written:
                # Fallback: aktiven Ordner erstellen
                active = self.active_world_path
                active.mkdir(parents=True, exist_ok=True)
                sbp_path = active / f"{safe_name}.sbp"
                cfg_path = active / f"{safe_name}.sbpcfg"
                async with aiofiles.open(sbp_path, "wb") as f:
                    await f.write(sbp_data)
                async with aiofiles.open(cfg_path, "wb") as f:
                    await f.write(cfg_data)
                worlds_written.append(self._active_world or "default")

            # Register in database
            entry = {
                "name": name,
                "category": category,
                "uploader_id": uploader_id,
                "uploader_name": uploader_name,
                "uploaded_at": datetime.now().isoformat(),
                "size_bytes": len(sbp_data) + len(cfg_data)
            }
            self._data["blueprints"].append(entry)
            await self._save()

            logger.info(
                f"Blueprint added: {name} ({category}) by {uploader_name} "
                f"-> Welten: {', '.join(worlds_written)}"
            )
            return True, f"Blueprint '{name}' hochgeladen!"

        except Exception as e:
            logger.error(f"Failed to add blueprint {name}: {e}")
            return False, f"Fehler beim Upload: {e}"

    async def add_from_zip(self, zip_data: bytes, category: str,
                           uploader_id: int, uploader_name: str) -> Tuple[int, List[str], List[str]]:
        """Extract and add blueprints from a ZIP file.
        Returns (count_added, added_names, errors)"""
        added = []
        errors = []

        try:
            with zipfile.ZipFile(io.BytesIO(zip_data)) as zf:
                filenames = zf.namelist()
                valid, err, pairs = self.validate_files(filenames)

                if not valid:
                    return 0, [], [err]

                for sbp_name, cfg_name in pairs:
                    name = sbp_name[:-4]  # Base name without .sbp
                    # Handle subdirectories in zip safely using Path
                    name = Path(name).name

                    sbp_data = zf.read(sbp_name)
                    cfg_data = zf.read(cfg_name)

                    success, msg = await self.add_blueprint(
                        name, category, uploader_id, uploader_name,
                        sbp_data, cfg_data
                    )
                    if success:
                        added.append(name)
                    else:
                        errors.append(msg)

        except zipfile.BadZipFile:
            errors.append("Ungueltige ZIP-Datei!")
        except Exception as e:
            errors.append(f"ZIP-Fehler: {e}")

        return len(added), added, errors

    def get_list(self, category: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get all blueprints, optionally filtered by category"""
        blueprints = self._data.get("blueprints", [])
        if category:
            blueprints = [b for b in blueprints if b["category"] == category]
        return sorted(blueprints, key=lambda x: x.get("uploaded_at", ""), reverse=True)

    def get_blueprint(self, name: str) -> Optional[Dict[str, Any]]:
        """Get blueprint metadata by name"""
        for b in self._data.get("blueprints", []):
            if b["name"].lower() == name.lower():
                return b
        return None

    def get_files(self, name: str) -> Tuple[Optional[Path], Optional[Path]]:
        """Get blueprint file paths from active world"""
        active = self.active_world_path
        sbp = active / f"{name}.sbp"
        cfg = active / f"{name}.sbpcfg"
        if sbp.exists() and cfg.exists():
            return sbp, cfg
        # Fallback: in allen Welten suchen
        for world_dir in self.base_path.iterdir():
            if not world_dir.is_dir():
                continue
            sbp = world_dir / f"{name}.sbp"
            cfg = world_dir / f"{name}.sbpcfg"
            if sbp.exists() and cfg.exists():
                return sbp, cfg
        return None, None

    async def delete(self, name: str, deleted_by_id: int, is_admin: bool = False) -> Tuple[bool, str]:
        """Delete a blueprint from ALL worlds. Users can only delete their own."""
        bp = self.get_blueprint(name)
        if not bp:
            return False, f"Blueprint '{name}' nicht gefunden."

        if not is_admin and bp["uploader_id"] != deleted_by_id:
            return False, "Du kannst nur eigene Blueprints löschen!"

        # Remove files from ALL world directories
        deleted_from = []
        for world_dir in self.base_path.iterdir():
            if not world_dir.is_dir():
                continue
            sbp = world_dir / f"{name}.sbp"
            cfg = world_dir / f"{name}.sbpcfg"
            try:
                removed = False
                if sbp.exists():
                    sbp.unlink()
                    removed = True
                if cfg.exists():
                    cfg.unlink()
                    removed = True
                if removed:
                    deleted_from.append(world_dir.name)
            except Exception as e:
                logger.error(f"Failed to delete blueprint files in {world_dir.name}: {e}")

        if not deleted_from:
            return False, f"Blueprint-Dateien fuer '{name}' nicht gefunden."

        # Remove from database
        self._data["blueprints"] = [
            b for b in self._data["blueprints"]
            if b["name"].lower() != name.lower()
        ]
        await self._save()

        logger.info(
            f"Blueprint deleted: {name} by user {deleted_by_id} "
            f"from worlds: {', '.join(deleted_from)}"
        )
        return True, f"Blueprint '{name}' gelöscht!"

    def list_from_filesystem(self) -> List[Dict[str, Any]]:
        """Liest alle Blueprints direkt vom Dateisystem (aktive Welt).
        Liefert alle .sbp Dateien mit Metadaten."""
        results = []
        active = self.active_world_path

        if not active.exists():
            return results

        seen = set()
        for sbp_file in sorted(active.glob("*.sbp")):
            name = sbp_file.stem
            if name in seen:
                continue
            seen.add(name)

            cfg_file = active / f"{name}.sbpcfg"
            size = sbp_file.stat().st_size
            if cfg_file.exists():
                size += cfg_file.stat().st_size

            modified = datetime.fromtimestamp(
                sbp_file.stat().st_mtime
            )

            # Metadaten aus JSON-DB ergaenzen falls vorhanden
            db_entry = self.get_blueprint(name)

            results.append({
                "name": name,
                "size_bytes": size,
                "modified": modified.isoformat(),
                "has_cfg": cfg_file.exists(),
                "category": db_entry.get("category", "—") if db_entry else "—",
                "uploader_name": db_entry.get("uploader_name", "—") if db_entry else "—",
                "uploader_id": db_entry.get("uploader_id") if db_entry else None,
            })

        return results

    def list_all_names(self) -> List[str]:
        """Gibt alle Blueprint-Namen vom Dateisystem zurueck (fuer Autocomplete)."""
        active = self.active_world_path
        if not active.exists():
            return []
        return sorted(set(
            f.stem for f in active.glob("*.sbp")
        ))

    async def delete_by_name(self, name: str) -> Tuple[bool, str]:
        """Löscht einen Blueprint (sbp+sbpcfg) aus ALLEN Welten. Keine Rechte-Prüfung."""
        deleted_from = []
        for world_dir in self.base_path.iterdir():
            if not world_dir.is_dir():
                continue
            sbp = world_dir / f"{name}.sbp"
            cfg = world_dir / f"{name}.sbpcfg"
            try:
                removed = False
                if sbp.exists():
                    sbp.unlink()
                    removed = True
                if cfg.exists():
                    cfg.unlink()
                    removed = True
                if removed:
                    deleted_from.append(world_dir.name)
            except Exception as e:
                logger.error(f"Failed to delete {name} in {world_dir.name}: {e}")

        if not deleted_from:
            return False, f"Blueprint '{name}' nicht gefunden."

        # Auch aus JSON-DB entfernen falls vorhanden
        self._data["blueprints"] = [
            b for b in self._data["blueprints"]
            if b["name"].lower() != name.lower()
        ]
        await self._save()

        logger.info(f"Blueprint deleted: {name} from worlds: {', '.join(deleted_from)}")
        return True, f"Blueprint '{name}' gelöscht (aus {len(deleted_from)} Welten)!"

    def count(self, category: Optional[str] = None) -> int:
        if category:
            return len([b for b in self._data.get("blueprints", [])
                       if b["category"] == category])
        return len(self._data.get("blueprints", []))
