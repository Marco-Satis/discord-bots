"""
Blueprint Manager for Satisfactory Server
Handles upload, validation, listing, download, and deletion of blueprints
Blueprint path: /home/satisfactory/.config/Epic/FactoryGame/Saved/SaveGames/blueprints/
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
    """Manage Satisfactory blueprint files with metadata tracking"""

    def __init__(self, blueprint_path: Path) -> None:
        self.blueprint_path = blueprint_path
        self._data: Dict[str, Any] = {"blueprints": []}

    async def load(self) -> None:
        """Load blueprint database"""
        try:
            if BLUEPRINT_DB.exists():
                async with aiofiles.open(BLUEPRINT_DB, "r", encoding="utf-8") as f:
                    content = await f.read()
                    self._data = json.loads(content)
            else:
                await self._save()
        except Exception as e:
            logger.error(f"Failed to load blueprint DB: {e}")

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
        """Save a blueprint pair to the server directory and register in DB."""
        try:
            # Sanitize name to prevent path traversal
            safe_name = Path(name).name
            if not safe_name or safe_name != name or "/" in name or "\\" in name or ".." in name:
                return False, "Ungueltiger Blueprint-Name!"

            # Ensure blueprint directory exists
            self.blueprint_path.mkdir(parents=True, exist_ok=True)

            # Write files
            sbp_path = self.blueprint_path / f"{safe_name}.sbp"
            cfg_path = self.blueprint_path / f"{safe_name}.sbpcfg"

            # Check if already exists
            if sbp_path.exists():
                return False, f"Blueprint '{name}' existiert bereits!"

            async with aiofiles.open(sbp_path, "wb") as f:
                await f.write(sbp_data)
            async with aiofiles.open(cfg_path, "wb") as f:
                await f.write(cfg_data)

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

            logger.info(f"Blueprint added: {name} ({category}) by {uploader_name}")
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
        """Get blueprint file paths"""
        sbp = self.blueprint_path / f"{name}.sbp"
        cfg = self.blueprint_path / f"{name}.sbpcfg"
        if sbp.exists() and cfg.exists():
            return sbp, cfg
        return None, None

    async def delete(self, name: str, deleted_by_id: int, is_admin: bool = False) -> Tuple[bool, str]:
        """Delete a blueprint. Users can only delete their own, admins can delete all."""
        bp = self.get_blueprint(name)
        if not bp:
            return False, f"Blueprint '{name}' nicht gefunden."

        if not is_admin and bp["uploader_id"] != deleted_by_id:
            return False, "Du kannst nur eigene Blueprints loeschen!"

        # Remove files
        sbp = self.blueprint_path / f"{name}.sbp"
        cfg = self.blueprint_path / f"{name}.sbpcfg"
        try:
            if sbp.exists():
                sbp.unlink()
            if cfg.exists():
                cfg.unlink()
        except Exception as e:
            logger.error(f"Failed to delete blueprint files: {e}")
            return False, f"Fehler beim Loeschen der Dateien: {e}"

        # Remove from database only after files are successfully deleted
        self._data["blueprints"] = [
            b for b in self._data["blueprints"]
            if b["name"].lower() != name.lower()
        ]
        await self._save()

        logger.info(f"Blueprint deleted: {name} by user {deleted_by_id}")
        return True, f"Blueprint '{name}' geloescht!"

    def count(self, category: Optional[str] = None) -> int:
        if category:
            return len([b for b in self._data.get("blueprints", [])
                       if b["category"] == category])
        return len(self._data.get("blueprints", []))
