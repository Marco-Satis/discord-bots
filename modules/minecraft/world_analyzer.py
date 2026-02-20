"""
Minecraft World Analyzer — Detaillierte Analyse der Minecraft-Welt

Analysiert level.dat, Spieler-Statistiken, Advancements und Region-Files.
Alle Datei-Operationen laufen in asyncio.to_thread() (blocking I/O).

Benoetigt: pip install nbtlib
Optional: anvil-parser2 (fuer detailliertere Region-Analyse)
"""

import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from utils.logger import get_logger

logger = get_logger("minecraft.world_analyzer")


class WorldAnalyzer:
    """Analysiert eine Minecraft-Welt (level.dat, Stats, Advancements, Regions)"""

    def __init__(self, world_path: Path, server_path: Path) -> None:
        """
        Args:
            world_path: Pfad zum World-Verzeichnis (z.B. /home/minecraft/bettermc/world)
            server_path: Pfad zum Server-Verzeichnis (fuer stats/advancements)
        """
        self.world_path = Path(world_path)
        self.server_path = Path(server_path)

    async def analyze(self) -> dict[str, Any]:
        """Fuehrt eine vollstaendige Analyse der Welt durch.

        Returns:
            Dict mit allen Analyse-Ergebnissen
        """
        results: dict[str, Any] = {
            "world_info": {},
            "player_stats": [],
            "advancements": [],
            "world_size": {},
            "errors": [],
        }

        # Alle Analysen parallel ausfuehren
        tasks = [
            asyncio.create_task(self._analyze_level_dat(results)),
            asyncio.create_task(self._analyze_player_stats(results)),
            asyncio.create_task(self._analyze_advancements(results)),
            asyncio.create_task(self._analyze_world_size(results)),
        ]

        await asyncio.gather(*tasks, return_exceptions=True)
        return results

    async def _analyze_level_dat(self, results: dict) -> None:
        """Liest level.dat und extrahiert Welt-Informationen"""
        level_dat = self.world_path / "level.dat"
        if not level_dat.exists():
            results["errors"].append("level.dat nicht gefunden")
            return

        try:
            info = await asyncio.to_thread(self._parse_level_dat, level_dat)
            results["world_info"] = info
        except ImportError:
            results["errors"].append(
                "nbtlib nicht installiert — level.dat kann nicht gelesen werden"
            )
        except Exception as e:
            results["errors"].append(f"level.dat Fehler: {e}")
            logger.error(f"level.dat Analyse fehlgeschlagen: {e}")

    @staticmethod
    def _parse_level_dat(level_dat: Path) -> dict[str, Any]:
        """Parst level.dat und gibt Welt-Info zurueck (blocking)"""
        import nbtlib

        nbt_file = nbtlib.load(str(level_dat))
        data = nbt_file.get("Data", nbt_file)  # Wurzel kann "Data" oder direkt sein

        info: dict[str, Any] = {}

        # Seed
        if "WorldGenSettings" in data:
            wgs = data["WorldGenSettings"]
            info["seed"] = str(wgs.get("seed", "?"))
        elif "RandomSeed" in data:
            info["seed"] = str(data["RandomSeed"])

        # Welt-Alter (Ticks → Tage/Stunden)
        if "Time" in data:
            ticks = int(data["Time"])
            seconds = ticks / 20
            hours = seconds / 3600
            days = hours / 24
            info["world_age_ticks"] = ticks
            info["world_age_hours"] = round(hours, 1)
            info["world_age_days"] = round(days, 1)

        # DayTime (In-Game Tageszeit)
        if "DayTime" in data:
            info["day_time"] = int(data["DayTime"])

        # Spawn-Koordinaten
        spawn = {}
        for key in ["SpawnX", "SpawnY", "SpawnZ"]:
            if key in data:
                spawn[key[-1].lower()] = int(data[key])
        if spawn:
            info["spawn"] = spawn

        # Schwierigkeitsgrad
        if "Difficulty" in data:
            diff_map = {0: "Peaceful", 1: "Easy", 2: "Normal", 3: "Hard"}
            info["difficulty"] = diff_map.get(int(data["Difficulty"]), str(data["Difficulty"]))

        # Spielmodus
        if "GameType" in data:
            mode_map = {0: "Survival", 1: "Creative", 2: "Adventure", 3: "Spectator"}
            info["gamemode"] = mode_map.get(int(data["GameType"]), str(data["GameType"]))

        # Hardcore
        if "hardcore" in data:
            info["hardcore"] = bool(data["hardcore"])

        # MC-Datenversion (= Server-Version)
        if "Version" in data:
            version = data["Version"]
            info["mc_version"] = str(version.get("Name", "?"))
            info["data_version"] = int(version.get("Id", 0))

        # Level-Name
        if "LevelName" in data:
            info["level_name"] = str(data["LevelName"])

        return info

    async def _analyze_player_stats(self, results: dict) -> None:
        """Liest Spieler-Statistiken aus stats/*.json"""
        stats_dir = self.world_path / "stats"
        if not stats_dir.exists():
            # Manche Server haben stats im Server-Root
            stats_dir = self.server_path / "world" / "stats"
            if not stats_dir.exists():
                return

        try:
            player_stats = await asyncio.to_thread(
                self._parse_player_stats, stats_dir
            )
            results["player_stats"] = player_stats
        except Exception as e:
            results["errors"].append(f"Spieler-Stats Fehler: {e}")
            logger.error(f"Spieler-Stats Analyse fehlgeschlagen: {e}")

    @staticmethod
    def _parse_player_stats(stats_dir: Path) -> list[dict[str, Any]]:
        """Parst alle Spieler-Statistik-Dateien (blocking)"""
        players = []

        for stat_file in stats_dir.glob("*.json"):
            try:
                with open(stat_file, "r", encoding="utf-8") as f:
                    data = json.load(f)

                stats = data.get("stats", {})
                uuid = stat_file.stem

                player: dict[str, Any] = {"uuid": uuid}

                # Spielzeit (in Ticks)
                custom = stats.get("minecraft:custom", {})
                play_ticks = custom.get("minecraft:play_time", 0) or custom.get(
                    "minecraft:play_one_minute", 0
                )
                if play_ticks:
                    play_hours = play_ticks / 20 / 3600
                    player["play_hours"] = round(play_hours, 1)

                # Tode
                player["deaths"] = custom.get("minecraft:deaths", 0)

                # Mob-Kills
                player["mob_kills"] = custom.get("minecraft:mob_kills", 0)

                # Abgebaute Bloecke (Top 5)
                mined = stats.get("minecraft:mined", {})
                if mined:
                    top_mined = sorted(
                        mined.items(), key=lambda x: x[1], reverse=True
                    )[:5]
                    player["top_mined"] = [
                        {"block": k.replace("minecraft:", ""), "count": v}
                        for k, v in top_mined
                    ]

                # Gecraftete Items (Top 5)
                crafted = stats.get("minecraft:crafted", {})
                if crafted:
                    top_crafted = sorted(
                        crafted.items(), key=lambda x: x[1], reverse=True
                    )[:5]
                    player["top_crafted"] = [
                        {"item": k.replace("minecraft:", ""), "count": v}
                        for k, v in top_crafted
                    ]

                # Distanzen (in cm → km)
                walk_cm = custom.get("minecraft:walk_one_cm", 0)
                swim_cm = custom.get("minecraft:swim_one_cm", 0)
                fly_cm = custom.get("minecraft:fly_one_cm", 0)
                player["distance_km"] = {
                    "walk": round(walk_cm / 100000, 2),
                    "swim": round(swim_cm / 100000, 2),
                    "fly": round(fly_cm / 100000, 2),
                }

                players.append(player)

            except (json.JSONDecodeError, IOError) as e:
                logger.debug(f"Stats-Datei {stat_file.name} nicht lesbar: {e}")

        # Nach Spielzeit sortieren
        players.sort(key=lambda p: p.get("play_hours", 0), reverse=True)
        return players

    async def _analyze_advancements(self, results: dict) -> None:
        """Liest Advancements/Achievements pro Spieler"""
        adv_dir = self.world_path / "advancements"
        if not adv_dir.exists():
            return

        try:
            advancements = await asyncio.to_thread(
                self._parse_advancements, adv_dir
            )
            results["advancements"] = advancements
        except Exception as e:
            results["errors"].append(f"Advancements Fehler: {e}")
            logger.error(f"Advancements Analyse fehlgeschlagen: {e}")

    @staticmethod
    def _parse_advancements(adv_dir: Path) -> list[dict[str, Any]]:
        """Parst Advancement-Dateien pro Spieler (blocking)"""
        players = []


        for adv_file in adv_dir.glob("*.json"):
            try:
                with open(adv_file, "r", encoding="utf-8") as f:
                    data = json.load(f)

                uuid = adv_file.stem
                completed = 0
                total = 0
                last_advancement = None
                last_time = None

                for key, entry in data.items():
                    # "DataVersion" Key ueberspringen
                    if not isinstance(entry, dict) or key == "DataVersion":
                        continue

                    total += 1
                    if entry.get("done", False):
                        completed += 1

                        # Letztes Achievement ermitteln
                        criteria = entry.get("criteria", {})
                        for crit_key, crit_time in criteria.items():
                            if isinstance(crit_time, str):
                                try:
                                    t = datetime.fromisoformat(
                                        crit_time.replace("Z", "+00:00")
                                    )
                                    if last_time is None or t > last_time:
                                        last_time = t
                                        last_advancement = key.replace(
                                            "minecraft:", ""
                                        )
                                except (ValueError, TypeError):
                                    pass

                if total > 0:
                    progress = round(completed / max(total, 1) * 100, 1)
                    players.append({
                        "uuid": uuid,
                        "completed": completed,
                        "total": total,
                        "progress_percent": progress,
                        "last_advancement": last_advancement,
                        "last_time": last_time.isoformat() if last_time else None,
                    })

            except (json.JSONDecodeError, IOError) as e:
                logger.debug(f"Advancement-Datei {adv_file.name} nicht lesbar: {e}")

        players.sort(key=lambda p: p.get("progress_percent", 0), reverse=True)
        return players

    async def _analyze_world_size(self, results: dict) -> None:
        """Analysiert die Welt-Groesse (Region-Files + Dateisystem)"""
        try:
            size_info = await asyncio.to_thread(self._calc_world_size)
            results["world_size"] = size_info
        except Exception as e:
            results["errors"].append(f"World-Size Fehler: {e}")
            logger.error(f"World-Size Analyse fehlgeschlagen: {e}")

    def _calc_world_size(self) -> dict[str, Any]:
        """Berechnet Welt-Groesse und erkundete Flaeche (blocking)"""
        info: dict[str, Any] = {}

        # Dimension-Verzeichnisse
        dimensions = {
            "overworld": self.world_path / "region",
            "nether": self.world_path / "DIM-1" / "region",
            "end": self.world_path / "DIM1" / "region",
        }

        total_size = 0
        total_regions = 0

        for dim_name, region_dir in dimensions.items():
            if not region_dir.exists():
                continue

            region_files = list(region_dir.glob("*.mca"))
            region_count = len(region_files)
            dim_size = sum(f.stat().st_size for f in region_files if f.is_file())

            # Erkundete Flaeche: 1 Region = 32x32 Chunks = 512x512 Bloecke
            # = 0.262144 km²
            explored_km2 = round(region_count * 0.262144, 2)

            info[dim_name] = {
                "regions": region_count,
                "size_bytes": dim_size,
                "size_mb": round(dim_size / (1024 * 1024), 1),
                "explored_km2": explored_km2,
            }

            total_size += dim_size
            total_regions += region_count

        # Gesamt-Groesse des World-Verzeichnisses
        try:
            world_total = self._get_dir_size(self.world_path)
        except Exception:
            world_total = total_size

        info["total"] = {
            "size_bytes": world_total,
            "size_mb": round(world_total / (1024 * 1024), 1),
            "total_regions": total_regions,
            "total_explored_km2": round(total_regions * 0.262144, 2),
        }

        return info

    @staticmethod
    def _get_dir_size(path: Path) -> int:
        """Berechnet die Gesamtgroesse eines Verzeichnisses (blocking)"""
        total = 0
        try:
            for entry in path.rglob("*"):
                if entry.is_file():
                    try:
                        total += entry.stat().st_size
                    except OSError:
                        pass
        except OSError:
            pass
        return total
