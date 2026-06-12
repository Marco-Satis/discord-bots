"""
Savegame Deep Analyzer for Satisfactory
Parses .sav files using the satisfactory-save package.

API structure (satisfactory-save 0.9.0):
  SaveGame.allSaveObjects() -> list[SaveObject]
  SaveObject.Header -> FActorSaveHeader | FObjectSaveHeader
  FActorSaveHeader.ObjectHeader.ClassName -> str  (e.g. "/Game/FactoryGame/Buildable/...")
  FObjectSaveHeader.BaseHeader -> ... (component, no ClassName directly)
  SaveGame.mSaveHeader -> FSaveHeader
  FSaveHeader.SessionName, PlayDurationSeconds, BuildVersion, SaveDateTime
"""

import asyncio
import json
import multiprocessing
import queue as _queue
import time
from pathlib import Path
from typing import Dict, Optional, Tuple, Any
from datetime import datetime, timedelta
from dataclasses import dataclass, field

from utils.logger import get_logger
from utils.formatting import format_bytes
from modules.satisfactory.save_header import read_save_header

logger = get_logger("satisfactory.savegame_analyzer")

# Try importing satisfactory-save package
try:
    from satisfactory_save import SaveGame, FActorSaveHeader, FObjectSaveHeader
    SAVE_PARSER_AVAILABLE = True
except ImportError:
    SAVE_PARSER_AVAILABLE = False
    logger.warning(
        "satisfactory-save package not installed. "
        "Building stats will not be available. "
        "Install with: pip install satisfactory-save"
    )

# Timeout fuer den isolierten Save-Parser-Kindprozess (Sekunden)
_SAVE_PARSE_TIMEOUT = 90


@dataclass
class WorldStats:
    """Analyzed world statistics from savegame"""
    # Basic info
    session_name: str = ""
    play_hours: float = 0.0
    save_date: str = ""
    save_size: str = ""
    build_version: int = 0

    # Building counts (legacy/aggregate)
    total_buildings: int = 0
    generators: int = 0
    production_machines: int = 0
    conveyor_belts: int = 0
    pipes: int = 0
    power_poles: int = 0
    storage: int = 0
    foundations: int = 0
    walls: int = 0
    trains: int = 0
    vehicles: int = 0
    stations: int = 0

    # Power
    total_power_mw: float = 0.0

    # Detailed per-tier belt tracking
    belts_mk1: int = 0  # 60/min
    belts_mk2: int = 0  # 120/min
    belts_mk3: int = 0  # 270/min
    belts_mk4: int = 0  # 480/min
    belts_mk5: int = 0  # 780/min
    belts_mk6: int = 0  # 1200/min
    lifts_total: int = 0

    # Detailed production
    smelters: int = 0
    foundries: int = 0
    constructors: int = 0
    assemblers: int = 0
    manufacturers: int = 0
    refineries: int = 0
    blenders: int = 0
    packagers: int = 0
    particle_accelerators: int = 0
    quantum_encoders: int = 0
    converters: int = 0

    # Detailed generators
    biomass_burners: int = 0
    coal_generators: int = 0
    fuel_generators: int = 0
    geothermal_generators: int = 0
    nuclear_plants: int = 0
    alien_augmenters: int = 0
    power_storage: int = 0

    # Resource extractors
    miners: int = 0
    oil_extractors: int = 0
    water_extractors: int = 0
    resource_well_pressurizers: int = 0

    # Splitter/Merger
    splitters: int = 0
    mergers: int = 0
    smart_splitters: int = 0
    programmable_splitters: int = 0
    priority_mergers: int = 0  # 1.1 New!

    # Pipelines per tier
    pipes_mk1: int = 0  # 300 m³/min
    pipes_mk2: int = 0  # 600 m³/min
    pipeline_pumps: int = 0
    valves: int = 0

    # Transport detailed
    locomotives: int = 0
    freight_cars: int = 0
    train_stations_count: int = 0
    drone_ports: int = 0
    drones: int = 0
    tractors: int = 0
    trucks: int = 0
    explorers: int = 0
    cyber_wagons: int = 0
    factory_carts: int = 0
    hypertube_entrances: int = 0

    # 1.1 New buildings
    personnel_elevators: int = 0
    conveyor_wall_holes: int = 0
    throughput_monitors: int = 0
    buffer_stops: int = 0
    hypertube_junctions: int = 0

    # Portals (1.0)
    portals: int = 0

    # AWESOME Sink
    awesome_sinks: int = 0

    # Meta
    last_analyzed: Optional[str] = None
    analysis_error: Optional[str] = None
    available: bool = False


# ClassName path fragments for classification
# These match against the full ClassName path from the save file

# Belt tiers
BELTS_MK1 = ["ConveyorBeltMk1"]
BELTS_MK2 = ["ConveyorBeltMk2"]
BELTS_MK3 = ["ConveyorBeltMk3"]
BELTS_MK4 = ["ConveyorBeltMk4"]
BELTS_MK5 = ["ConveyorBeltMk5"]
BELTS_MK6 = ["ConveyorBeltMk6"]
LIFTS = ["ConveyorLiftMk"]

# Generator types (detailed)
GENERATORS_BIOMASS = ["GeneratorBiomass", "GeneratorIntegratedBiomass"]
GENERATORS_COAL = ["GeneratorCoal"]
GENERATORS_FUEL = ["GeneratorFuel"]
GENERATORS_GEO = ["GeneratorGeo"]
GENERATORS_NUCLEAR = ["GeneratorNuclear"]
GENERATORS_ALIEN = ["AlienPowerAugmenter"]

# All generators for classification
GENERATORS = (
    GENERATORS_BIOMASS + GENERATORS_COAL + GENERATORS_FUEL +
    GENERATORS_GEO + GENERATORS_NUCLEAR + GENERATORS_ALIEN
)

# Production machines (detailed)
SMELTERS = ["SmelterMk"]
FOUNDRIES = ["FoundryMk"]
CONSTRUCTORS = ["ConstructorMk"]
ASSEMBLERS = ["AssemblerMk"]
MANUFACTURERS = ["ManufacturerMk"]
REFINERIES = ["OilRefinery"]
BLENDERS = ["Blender"]
PACKAGERS = ["Packager"]
PARTICLE_ACCELERATORS = ["HadronCollider"]
QUANTUM_ENCODERS = ["QuantumEncoder"]
CONVERTERS = ["Converter"]

# All production for classification
PRODUCTION = (
    SMELTERS + FOUNDRIES + CONSTRUCTORS + ASSEMBLERS + MANUFACTURERS +
    REFINERIES + BLENDERS + PACKAGERS + PARTICLE_ACCELERATORS +
    QUANTUM_ENCODERS + CONVERTERS
)

# Resource extraction
MINERS = ["ResourceExtractor", "ResourceExtractorMk"]
OIL_EXTRACTORS = ["OilPump"]
WATER_EXTRACTORS = ["WaterPump"]
RESOURCE_WELL_PRESSURIZERS = ["ResourceWellPressurizationUnit"]

# Splitters/Mergers
SPLITTERS = ["ConveyorSplitterMk"]
MERGERS = ["ConveyorMergerMk"]
SMART_SPLITTERS = ["SmartSplitter"]
PROGRAMMABLE_SPLITTERS = ["ProgrammableSplitter"]
PRIORITY_MERGERS = ["PriorityMerger"]

# Pipeline tiers and components
PIPES_MK1 = ["Pipeline_", "PipelineMk1"]
PIPES_MK2 = ["PipelineMk2"]
PIPELINE_PUMPS = ["PipelinePump"]
VALVES = ["PipelineValve"]

# All pipes for classification
PIPES = PIPES_MK1 + PIPES_MK2 + ["PipeHyper"] + PIPELINE_PUMPS + VALVES

# Transport
LOCOMOTIVES = ["Locomotive"]
FREIGHT_CARS = ["FreightWagon"]
TRAIN_STATIONS = ["TrainStation", "TrainPlatform", "TrainDockingStation"]
DRONE_PORTS = ["DronePort"]
DRONES = ["Drone"]
TRACTORS = ["Tractor"]
TRUCKS = ["Truck"]
EXPLORERS = ["Explorer"]
CYBER_WAGONS = ["CyberWagon"]
FACTORY_CARTS = ["FactoryCart"]
HYPERTUBE_ENTRANCES = ["HyperTubeEntrance"]

# All trains for classification
TRAINS = LOCOMOTIVES + FREIGHT_CARS

# All vehicles for classification
VEHICLES = DRONES + TRACTORS + TRUCKS + EXPLORERS + CYBER_WAGONS

# All stations for classification
STATIONS = TRAIN_STATIONS + DRONE_PORTS + ["TruckStation", "FreightPlatform"]

# 1.1 New buildings
NEW_1_1_BUILDINGS = [
    "PersonnelElevator",
    "ConveyorWallHole",
    "ThroughputMonitor",
    "BufferStop",
    "HyperTubeJunction",
]

# Portals
PORTALS = ["Portal"]

# AWESOME Sink
AWESOME_SINKS = ["AWSink"]

# Storage
STORAGE = [
    "StorageContainerMk1", "StorageContainerMk2",
    "IndustrialFluidContainer", "PipeStorageTank",
]

# Foundations
FOUNDATIONS = [
    "Foundation_", "Ramp_", "RampDouble",
    "PillarBase", "PillarMiddle", "PillarTop",
]

# Walls
WALLS = [
    "Wall_", "Fence_", "Gate_", "Door_", "Window_",
]

# Power infrastructure
POWER_INFRA = [
    "PowerPoleMk", "PowerLine", "PowerTowerMk", "PowerSwitch",
    "PowerStorageMk",
]

# Generator power output in MW
GENERATOR_POWER_MW = {
    "GeneratorBiomass": 30,
    "GeneratorIntegratedBiomass": 30,
    "GeneratorCoal": 75,
    "GeneratorFuel": 250,
    "GeneratorNuclear": 2500,
    "GeneratorGeo": 200,
    "AlienPowerAugmenter": 500,
}


def _get_class_name(obj: Any) -> str:
    """Extract ClassName from a SaveObject, handling both Actor and Object headers."""
    h = obj.Header
    if isinstance(h, FActorSaveHeader):
        return h.ObjectHeader.ClassName
    # FObjectSaveHeader doesn't have a direct ClassName - skip
    return ""


def _classify(class_name: str) -> Tuple[str, Optional[str]]:
    """
    Classify a building by its ClassName path.
    Returns (primary_category, detailed_category)
    """
    # Generators
    for kw in GENERATORS_BIOMASS:
        if kw in class_name:
            return ("generator", "biomass")
    for kw in GENERATORS_COAL:
        if kw in class_name:
            return ("generator", "coal")
    for kw in GENERATORS_FUEL:
        if kw in class_name:
            return ("generator", "fuel")
    for kw in GENERATORS_GEO:
        if kw in class_name:
            return ("generator", "geothermal")
    for kw in GENERATORS_NUCLEAR:
        if kw in class_name:
            return ("generator", "nuclear")
    for kw in GENERATORS_ALIEN:
        if kw in class_name:
            return ("generator", "alien")

    # Production machines
    for kw in SMELTERS:
        if kw in class_name:
            return ("production", "smelter")
    for kw in FOUNDRIES:
        if kw in class_name:
            return ("production", "foundry")
    for kw in CONSTRUCTORS:
        if kw in class_name:
            return ("production", "constructor")
    for kw in ASSEMBLERS:
        if kw in class_name:
            return ("production", "assembler")
    for kw in MANUFACTURERS:
        if kw in class_name:
            return ("production", "manufacturer")
    for kw in REFINERIES:
        if kw in class_name:
            return ("production", "refinery")
    for kw in BLENDERS:
        if kw in class_name:
            return ("production", "blender")
    for kw in PACKAGERS:
        if kw in class_name:
            return ("production", "packager")
    for kw in PARTICLE_ACCELERATORS:
        if kw in class_name:
            return ("production", "particle_accelerator")
    for kw in QUANTUM_ENCODERS:
        if kw in class_name:
            return ("production", "quantum_encoder")
    for kw in CONVERTERS:
        if kw in class_name:
            return ("production", "converter")

    # Belts
    for kw in BELTS_MK1:
        if kw in class_name:
            return ("belt", "mk1")
    for kw in BELTS_MK2:
        if kw in class_name:
            return ("belt", "mk2")
    for kw in BELTS_MK3:
        if kw in class_name:
            return ("belt", "mk3")
    for kw in BELTS_MK4:
        if kw in class_name:
            return ("belt", "mk4")
    for kw in BELTS_MK5:
        if kw in class_name:
            return ("belt", "mk5")
    for kw in BELTS_MK6:
        if kw in class_name:
            return ("belt", "mk6")
    for kw in LIFTS:
        if kw in class_name:
            return ("belt", "lift")

    # Pipes
    for kw in PIPES_MK1:
        if kw in class_name:
            return ("pipe", "mk1")
    for kw in PIPES_MK2:
        if kw in class_name:
            return ("pipe", "mk2")
    if "PipeHyper" in class_name:
        return ("pipe", "hyper")
    for kw in PIPELINE_PUMPS:
        if kw in class_name:
            return ("pipe", "pump")
    for kw in VALVES:
        if kw in class_name:
            return ("pipe", "valve")

    # Splitters/Mergers
    for kw in SPLITTERS:
        if kw in class_name:
            return ("splitter", "splitter")
    for kw in MERGERS:
        if kw in class_name:
            return ("merger", "merger")
    for kw in SMART_SPLITTERS:
        if kw in class_name:
            return ("splitter", "smart")
    for kw in PROGRAMMABLE_SPLITTERS:
        if kw in class_name:
            return ("splitter", "programmable")
    for kw in PRIORITY_MERGERS:
        if kw in class_name:
            return ("merger", "priority")

    # Resource extraction
    for kw in MINERS:
        if kw in class_name:
            return ("extractor", "miner")
    for kw in OIL_EXTRACTORS:
        if kw in class_name:
            return ("extractor", "oil")
    for kw in WATER_EXTRACTORS:
        if kw in class_name:
            return ("extractor", "water")
    for kw in RESOURCE_WELL_PRESSURIZERS:
        if kw in class_name:
            return ("extractor", "pressurizer")

    # Transport
    for kw in LOCOMOTIVES:
        if kw in class_name:
            return ("train", "locomotive")
    for kw in FREIGHT_CARS:
        if kw in class_name:
            return ("train", "freight_car")
    for kw in DRONES:
        if kw in class_name:
            return ("vehicle", "drone")
    for kw in TRACTORS:
        if kw in class_name:
            return ("vehicle", "tractor")
    for kw in TRUCKS:
        if kw in class_name:
            return ("vehicle", "truck")
    for kw in EXPLORERS:
        if kw in class_name:
            return ("vehicle", "explorer")
    for kw in CYBER_WAGONS:
        if kw in class_name:
            return ("vehicle", "cyber_wagon")
    for kw in FACTORY_CARTS:
        if kw in class_name:
            return ("vehicle", "factory_cart")

    # Stations
    for kw in TRAIN_STATIONS:
        if kw in class_name:
            return ("station", "train")
    for kw in ["TruckStation", "FreightPlatform"]:
        if kw in class_name:
            return ("station", "truck")
    for kw in DRONE_PORTS:
        if kw in class_name:
            return ("station", "drone")

    # Hypertubes
    for kw in HYPERTUBE_ENTRANCES:
        if kw in class_name:
            return ("hypertube", "entrance")
    for kw in ["HyperTubeJunction"]:
        if kw in class_name:
            return ("hypertube", "junction")

    # New 1.1 buildings
    for kw in NEW_1_1_BUILDINGS:
        if kw in class_name:
            if "PersonnelElevator" in class_name:
                return ("new_1_1", "personnel_elevator")
            elif "ConveyorWallHole" in class_name:
                return ("new_1_1", "conveyor_wall_hole")
            elif "ThroughputMonitor" in class_name:
                return ("new_1_1", "throughput_monitor")
            elif "BufferStop" in class_name:
                return ("new_1_1", "buffer_stop")
            elif "HyperTubeJunction" in class_name:
                return ("new_1_1", "hypertube_junction")

    # Portals
    for kw in PORTALS:
        if kw in class_name:
            return ("portal", "portal")

    # AWESOME Sink
    for kw in AWESOME_SINKS:
        if kw in class_name:
            return ("awesome_sink", "sink")

    # Standard classifications
    for kw in STORAGE:
        if kw in class_name:
            return ("storage", None)
    for kw in FOUNDATIONS:
        if kw in class_name:
            return ("foundation", None)
    for kw in WALLS:
        if kw in class_name:
            return ("wall", None)
    for kw in POWER_INFRA:
        if kw in class_name:
            return ("power_pole", None)

    # Only count as building if it's in the Buildable path
    if "/Buildable/" in class_name or "/Build_" in class_name:
        return ("other_building", None)
    return ("world", None)  # Not a player-built object


def _estimate_power(class_name: str) -> float:
    """Estimate power output for a generator type."""
    for gen_type, power in GENERATOR_POWER_MW.items():
        if gen_type in class_name:
            return float(power)
    return 0.0


def _extract_save_objects_worker(save_path: str, result_q) -> None:
    """
    Laeuft in einem ISOLIERTEN fork-Kindprozess.

    Fuehrt den nativen satisfactory-save-Parse durch und gibt nur
    serialisierbare Daten zurueck: (header_dict, [class_names]).
    Ein SIGSEGV im nativen Parser (z.B. inkompatibles Save-Format nach
    einem Game-Update) toetet damit NUR diesen Kindprozess, nicht den Bot.
    Die reine Klassifikation passiert anschliessend im Parent-Prozess.

    Bewusst KEIN logging und KEIN import hier — der Kindprozess nutzt das
    bereits modul-global importierte ``SaveGame`` (vermeidet Import-/Logging-
    Lock-Deadlocks beim fork).
    """
    try:
        save = SaveGame(save_path)
        h = save.mSaveHeader
        header = {
            "session_name": h.SessionName or "",
            "build_version": h.BuildVersion or 0,
            "play_hours": round(h.PlayDurationSeconds / 3600, 1),
        }
        class_names = []
        for obj in save.allSaveObjects():
            cn = _get_class_name(obj)
            if cn:
                class_names.append(cn)
        result_q.put(("ok", (header, class_names)))
    except Exception as e:  # noqa: BLE001 - Fehler an Parent durchreichen
        result_q.put(("error", str(e)[:200]))


class SavegameAnalyzer:
    """
    Deep savegame analyzer with caching.
    Results are cached and only re-analyzed when the save file changes.
    """

    def __init__(self, savegame_path: Path, cache_dir: Optional[Path] = None) -> None:
        self.savegame_path = savegame_path
        self.cache_dir = cache_dir or savegame_path.parent / ".analyzer_cache"
        self._cached_stats: Optional[WorldStats] = None
        self._cached_mtime: float = 0
        self._analyzing: bool = False
        self._lock: asyncio.Lock = asyncio.Lock()
        # Sobald der native Parser einmal mit SIGSEGV (Signal 11) crasht, wird der
        # Deep-Parse-Subprozess fuer diese Analyzer-Instanz abgeschaltet (Header-
        # Fallback) -> kein 5-Min-Crash-Spam mehr bei inkompatiblem Save-Format.
        # In-Memory: ein Bot-Neustart / Lib-Update re-checkt einmal. (build_version
        # aus dem Header ist bei kaputtem Format oft 0/None -> taugt nicht als Key.)
        self._native_parse_disabled: bool = False

    def _get_latest_save(self) -> Optional[Path]:
        """Find the most recent .sav file"""
        # Try multiple known paths
        search_dirs = [
            self.savegame_path / "server",
            self.savegame_path,
        ]
        for d in search_dirs:
            if d.exists():
                saves = list(d.glob("*.sav"))
                if saves:
                    return max(saves, key=lambda f: f.stat().st_mtime)
        return None

    def _load_cache(self) -> Optional[WorldStats]:
        """Load cached stats from disk"""
        try:
            cache_file = self.cache_dir / "world_stats.json"
            if cache_file.exists():
                with open(cache_file, "r") as f:
                    data = json.load(f)
                stats = WorldStats(**{
                    k: v for k, v in data.items()
                    if k in WorldStats.__dataclass_fields__
                })
                return stats
        except Exception as e:
            logger.debug(f"Cache load failed: {e}")
        return None

    def _save_cache(self, stats: WorldStats) -> None:
        """Save stats to disk cache"""
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            cache_file = self.cache_dir / "world_stats.json"
            with open(cache_file, "w") as f:
                json.dump(vars(stats), f, indent=2, default=str)
        except Exception as e:
            logger.debug(f"Cache save failed: {e}")

    async def get_stats(self, force: bool = False) -> WorldStats:
        """
        Get world stats, using cache if save hasn't changed.
        Returns cached stats while analysis is running.
        """
        save_file = self._get_latest_save()

        if not save_file:
            stats = WorldStats()
            stats.analysis_error = "Keine Savegames gefunden"
            return stats

        current_mtime = save_file.stat().st_mtime

        # Return cache if save hasn't changed
        if not force and self._cached_stats and self._cached_mtime == current_mtime:
            return self._cached_stats

        # Load disk cache on first call
        if not self._cached_stats:
            self._cached_stats = self._load_cache()
            if self._cached_stats and self._cached_stats.available:
                self._cached_mtime = current_mtime
                return self._cached_stats

        # Don't start another analysis if one is running
        if self._analyzing:
            return self._cached_stats or WorldStats()

        # Run analysis in executor (non-blocking)
        async with self._lock:
            self._analyzing = True
            try:
                stats = await asyncio.get_running_loop().run_in_executor(
                    None, self._analyze_save, save_file
                )
                self._cached_stats = stats
                self._cached_mtime = current_mtime
                self._save_cache(stats)
                return stats
            except Exception as e:
                logger.error(f"Save analysis failed: {e}")
                stats = WorldStats()
                stats.analysis_error = str(e)[:200]
                return self._cached_stats or stats
            finally:
                self._analyzing = False

    def _analyze_save(self, save_file: Path) -> WorldStats:
        """Synchronous deep analysis of a save file."""
        stats = WorldStats()
        stats.save_size = self._format_size(save_file.stat().st_size)

        if not SAVE_PARSER_AVAILABLE:
            stats.analysis_error = "satisfactory-save Paket nicht installiert"
            self._read_basic_header(save_file, stats)
            return stats

        # Build-Version vorab aus dem Header lesen (billig, kann NICHT segfaulten).
        # Hat der native Parser dieses Save-Build schon mit SIGSEGV gekillt, den
        # Deep-Parse-Subprozess ueberspringen — sonst crasht er bei jeder neuen
        # Autosave (alle ~5 Min) erneut und spammt das Log.
        self._read_basic_header(save_file, stats)
        if self._native_parse_disabled:
            stats.analysis_error = (
                "Deep-Parse deaktiviert — nativer Parser inkompatibel mit dem "
                "aktuellen Save-Format (Header-Fallback)"
            )
            return stats
        build_ver = stats.build_version

        start_time = time.time()
        logger.info(f"Analyzing save: {save_file.name} ({stats.save_size})")

        # Nativen Parser in ISOLIERTEM Kindprozess laufen lassen, damit ein
        # SIGSEGV (z.B. inkompatibles Save-Format nach einem Game-Update) nur
        # den Subprozess killt und NICHT den gesamten Bot-Prozess. fork-Context
        # vermeidet ein Re-Import des __main__-Moduls (bots/recon_bot.py mit
        # modul-level Bot-Setup); ein try/except kann SIGSEGV nicht fangen.
        ctx = multiprocessing.get_context("fork")
        result_q = ctx.Queue()
        proc = ctx.Process(
            target=_extract_save_objects_worker,
            args=(str(save_file), result_q),
            daemon=True,
        )
        proc.start()

        # Auf Ergebnis pollen: holt Daten sobald verfuegbar (Erfolg, schnell)
        # ODER erkennt einen toten Kindprozess ohne Daten (nativer Crash,
        # ~0.5s) -> kein 90s-Blockieren im Crash-Fall. get() VOR join()
        # verhindert den Queue-Pipe-Buffer-Deadlock bei grossen Ergebnissen.
        parse_result = None
        deadline = time.time() + _SAVE_PARSE_TIMEOUT
        while time.time() < deadline:
            try:
                parse_result = result_q.get(timeout=0.5)
                break
            except _queue.Empty:
                pass
            except Exception as e:
                logger.warning(f"Save-Parser Queue-Fehler: {e}")
                break
            if not proc.is_alive():
                # Kind beendet -> letzter Drain-Versuch (Puffer-Restdaten)
                try:
                    parse_result = result_q.get(timeout=1.0)
                except Exception:
                    parse_result = None
                break

        proc.join(timeout=10)
        if proc.is_alive():
            proc.terminate()
            proc.join(timeout=5)

        # Kein Ergebnis -> nativer Crash (exitcode < 0 = Signal) oder Timeout
        if parse_result is None:
            ec = proc.exitcode
            if ec is not None and ec < 0:
                # Erster Crash: Deep-Parse fuer diese Instanz abschalten + 1x warnen.
                # Folge-Autosaves ueberspringen den Subprozess (kein 5-Min-Spam mehr).
                if not self._native_parse_disabled:
                    self._native_parse_disabled = True
                    logger.warning(
                        f"Save-Parser-Kindprozess durch Signal {-ec} abgestuerzt "
                        f"(Save-Build {build_ver or '?'} inkompatibel) -> Header-Fallback; "
                        f"Deep-Parse wird bis zum naechsten Bot-Neustart uebersprungen"
                    )
                stats.analysis_error = f"Parser-Crash (Signal {-ec})"
            else:
                logger.warning(
                    f"Save-Analyse ohne Ergebnis (exitcode={ec}) -> Header-Fallback"
                )
                stats.analysis_error = "Save-Analyse fehlgeschlagen/Timeout"
            # Header bereits oben gelesen (Inkompatibel-Vorab-Check) — kein Re-Read.
            return stats

        status, payload = parse_result
        if status == "error":
            logger.error(f"Save parse error: {payload}")
            stats.analysis_error = payload
            self._read_basic_header(save_file, stats)
            return stats

        # Erfolg: payload = (header, [class_names]). Klassifikation ist reine
        # Python-Logik auf Strings -> kann nicht segfaulten, laeuft im Parent.
        try:
            header_data, class_names = payload
            stats.session_name = header_data.get("session_name", "")
            stats.build_version = header_data.get("build_version", 0)
            stats.play_hours = header_data.get("play_hours", 0.0)

            total_power = 0.0

            for class_name in class_names:
                category, detail = _classify(class_name)

                # Update aggregate counts and detailed counts
                if category == "generator":
                    stats.generators += 1
                    total_power += _estimate_power(class_name)
                    # Detailed generator tracking
                    if detail == "biomass":
                        stats.biomass_burners += 1
                    elif detail == "coal":
                        stats.coal_generators += 1
                    elif detail == "fuel":
                        stats.fuel_generators += 1
                    elif detail == "geothermal":
                        stats.geothermal_generators += 1
                    elif detail == "nuclear":
                        stats.nuclear_plants += 1
                    elif detail == "alien":
                        stats.alien_augmenters += 1

                elif category == "production":
                    stats.production_machines += 1
                    # Detailed production tracking
                    if detail == "smelter":
                        stats.smelters += 1
                    elif detail == "foundry":
                        stats.foundries += 1
                    elif detail == "constructor":
                        stats.constructors += 1
                    elif detail == "assembler":
                        stats.assemblers += 1
                    elif detail == "manufacturer":
                        stats.manufacturers += 1
                    elif detail == "refinery":
                        stats.refineries += 1
                    elif detail == "blender":
                        stats.blenders += 1
                    elif detail == "packager":
                        stats.packagers += 1
                    elif detail == "particle_accelerator":
                        stats.particle_accelerators += 1
                    elif detail == "quantum_encoder":
                        stats.quantum_encoders += 1
                    elif detail == "converter":
                        stats.converters += 1

                elif category == "belt":
                    stats.conveyor_belts += 1
                    # Detailed belt tracking
                    if detail == "mk1":
                        stats.belts_mk1 += 1
                    elif detail == "mk2":
                        stats.belts_mk2 += 1
                    elif detail == "mk3":
                        stats.belts_mk3 += 1
                    elif detail == "mk4":
                        stats.belts_mk4 += 1
                    elif detail == "mk5":
                        stats.belts_mk5 += 1
                    elif detail == "mk6":
                        stats.belts_mk6 += 1
                    elif detail == "lift":
                        stats.lifts_total += 1

                elif category == "pipe":
                    stats.pipes += 1
                    # Detailed pipe tracking
                    if detail == "mk1":
                        stats.pipes_mk1 += 1
                    elif detail == "mk2":
                        stats.pipes_mk2 += 1
                    elif detail == "pump":
                        stats.pipeline_pumps += 1
                    elif detail == "valve":
                        stats.valves += 1

                elif category == "splitter":
                    # Detailed splitter tracking
                    if detail == "smart":
                        stats.smart_splitters += 1
                    elif detail == "programmable":
                        stats.programmable_splitters += 1
                    else:
                        stats.splitters += 1

                elif category == "merger":
                    # Detailed merger tracking
                    if detail == "priority":
                        stats.priority_mergers += 1
                    else:
                        stats.mergers += 1

                elif category == "extractor":
                    # Detailed extractor tracking
                    if detail == "miner":
                        stats.miners += 1
                    elif detail == "oil":
                        stats.oil_extractors += 1
                    elif detail == "water":
                        stats.water_extractors += 1
                    elif detail == "pressurizer":
                        stats.resource_well_pressurizers += 1

                elif category == "storage":
                    stats.storage += 1

                elif category == "foundation":
                    stats.foundations += 1

                elif category == "wall":
                    stats.walls += 1

                elif category == "train":
                    stats.trains += 1
                    # Detailed train tracking
                    if detail == "locomotive":
                        stats.locomotives += 1
                    elif detail == "freight_car":
                        stats.freight_cars += 1

                elif category == "vehicle":
                    stats.vehicles += 1
                    # Detailed vehicle tracking
                    if detail == "drone":
                        stats.drones += 1
                    elif detail == "tractor":
                        stats.tractors += 1
                    elif detail == "truck":
                        stats.trucks += 1
                    elif detail == "explorer":
                        stats.explorers += 1
                    elif detail == "cyber_wagon":
                        stats.cyber_wagons += 1
                    elif detail == "factory_cart":
                        stats.factory_carts += 1

                elif category == "station":
                    stats.stations += 1
                    # Detailed station tracking
                    if detail == "train":
                        stats.train_stations_count += 1
                    elif detail == "drone":
                        stats.drone_ports += 1

                elif category == "power_pole":
                    stats.power_poles += 1

                elif category == "hypertube":
                    # Detailed hypertube tracking
                    if detail == "entrance":
                        stats.hypertube_entrances += 1
                    elif detail == "junction":
                        stats.hypertube_junctions += 1

                elif category == "new_1_1":
                    # 1.1 new buildings
                    if detail == "personnel_elevator":
                        stats.personnel_elevators += 1
                    elif detail == "conveyor_wall_hole":
                        stats.conveyor_wall_holes += 1
                    elif detail == "throughput_monitor":
                        stats.throughput_monitors += 1
                    elif detail == "buffer_stop":
                        stats.buffer_stops += 1
                    elif detail == "hypertube_junction":
                        stats.hypertube_junctions += 1

                elif category == "portal":
                    stats.portals += 1

                elif category == "awesome_sink":
                    stats.awesome_sinks += 1

                elif category == "other_building":
                    pass  # counted in total
                else:
                    continue  # world objects don't count

                stats.total_buildings += 1

            stats.total_power_mw = round(total_power, 0)
            stats.available = True
            stats.last_analyzed = datetime.now().strftime("%d.%m.%Y %H:%M")

            elapsed = time.time() - start_time
            logger.info(
                f"Save analysis complete in {elapsed:.1f}s: "
                f"{stats.total_buildings} buildings, "
                f"{stats.generators} generators ({stats.total_power_mw} MW), "
                f"{stats.production_machines} prod, "
                f"{stats.conveyor_belts} belts"
            )

        except Exception as e:
            logger.error(f"Save parse error: {e}")
            stats.analysis_error = str(e)[:200]
            self._read_basic_header(save_file, stats)

        return stats

    def _read_basic_header(self, save_file: Path, stats: WorldStats) -> None:
        """Read basic save header without full parsing (fallback)"""
        header = read_save_header(save_file)
        if header:
            stats.build_version = header.build_version
            stats.session_name = header.session_name
            stats.play_hours = header.play_hours

    @staticmethod
    def _format_size(num_bytes: int) -> str:
        for unit in ["B", "KB", "MB", "GB"]:
            if abs(num_bytes) < 1024.0:
                return f"{num_bytes:.1f} {unit}"
            num_bytes /= 1024.0
        return f"{num_bytes:.1f} TB"

    def _get_snapshot_dir(self) -> Path:
        """Get the weekly snapshots directory."""
        snapshot_dir = self.cache_dir / "weekly_snapshots"
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        return snapshot_dir

    def _get_iso_week(self, dt: datetime) -> str:
        """Get ISO week string (YYYY-WXX format)."""
        iso_calendar = dt.isocalendar()
        return f"{iso_calendar.year}-W{iso_calendar.week:02d}"

    async def create_weekly_snapshot(self) -> Dict[str, Any]:
        """Create a snapshot of current world stats for weekly comparison.
        Saved as JSON in data/weekly_snapshots/snapshot_YYYY-WXX.json
        Returns the snapshot dict."""
        stats = await self.get_stats()
        if not stats.available:
            logger.warning("Cannot create snapshot: stats not available")
            return {}

        iso_week = self._get_iso_week(datetime.now())
        snapshot_dir = self._get_snapshot_dir()
        snapshot_file = snapshot_dir / f"snapshot_{iso_week}.json"

        # Convert stats to dict
        snapshot_data = {
            "timestamp": datetime.now().isoformat(),
            "iso_week": iso_week,
            "stats": vars(stats),
        }

        try:
            with open(snapshot_file, "w") as f:
                json.dump(snapshot_data, f, indent=2, default=str)
            logger.info(f"Weekly snapshot created: {snapshot_file.name}")
            return snapshot_data
        except Exception as e:
            logger.error(f"Failed to create weekly snapshot: {e}")
            return {}

    async def get_weekly_delta(self) -> Optional[Dict[str, Any]]:
        """Compare current stats with last week's snapshot.
        Returns delta dict with changes, or None if no previous snapshot exists."""
        stats = await self.get_stats()
        if not stats.available:
            logger.warning("Cannot calculate delta: stats not available")
            return None

        iso_week = self._get_iso_week(datetime.now())
        snapshot_dir = self._get_snapshot_dir()

        # Calculate last week's ISO week
        last_week_date = datetime.now() - timedelta(days=7)
        last_week = self._get_iso_week(last_week_date)
        last_snapshot_file = snapshot_dir / f"snapshot_{last_week}.json"

        if not last_snapshot_file.exists():
            logger.debug(f"No previous snapshot found for {last_week}")
            return None

        try:
            with open(last_snapshot_file, "r") as f:
                last_snapshot = json.load(f)
            last_stats = last_snapshot["stats"]

            # Calculate deltas for key fields
            delta = {
                "timestamp": datetime.now().isoformat(),
                "iso_week": iso_week,
                "comparison_week": last_week,
                "deltas": {},
            }

            # Fields to track
            tracked_fields = [
                "total_buildings", "generators", "production_machines",
                "conveyor_belts", "pipes", "power_poles", "storage",
                "total_power_mw", "play_hours",
                # Detailed fields
                "belts_mk1", "belts_mk2", "belts_mk3", "belts_mk4", "belts_mk5", "belts_mk6",
                "lifts_total", "smelters", "foundries", "constructors", "assemblers",
                "manufacturers", "refineries", "blenders", "packagers", "particle_accelerators",
                "quantum_encoders", "converters", "biomass_burners", "coal_generators",
                "fuel_generators", "geothermal_generators", "nuclear_plants", "alien_augmenters",
                "power_storage", "miners", "oil_extractors", "water_extractors",
                "resource_well_pressurizers", "splitters", "mergers", "smart_splitters",
                "programmable_splitters", "priority_mergers", "pipes_mk1", "pipes_mk2",
                "pipeline_pumps", "valves", "locomotives", "freight_cars", "train_stations_count",
                "drone_ports", "drones", "tractors", "trucks", "explorers", "cyber_wagons",
                "factory_carts", "hypertube_entrances", "personnel_elevators",
                "conveyor_wall_holes", "throughput_monitors", "buffer_stops",
                "hypertube_junctions", "portals", "awesome_sinks",
            ]

            current_stats = vars(stats)
            for field in tracked_fields:
                if field in current_stats and field in last_stats:
                    current_val = current_stats[field]
                    last_val = last_stats[field]
                    # Handle both int and float
                    try:
                        delta_val = current_val - last_val
                        if delta_val != 0:
                            delta["deltas"][field] = {
                                "last_week": last_val,
                                "current": current_val,
                                "delta": delta_val,
                            }
                    except TypeError:
                        pass  # Skip fields that can't be subtracted

            logger.info(f"Weekly delta calculated: {len(delta['deltas'])} changed fields")
            return delta

        except Exception as e:
            logger.error(f"Failed to calculate weekly delta: {e}")
            return None
