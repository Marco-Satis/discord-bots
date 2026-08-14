"""
Configuration loader - reads .env and config.json
"""

import os
import json
import logging
from pathlib import Path
from dotenv import load_dotenv

# M30-Fix: Logger fuer Fallback-Warnung bei korrupter/fehlender config.json
logger = logging.getLogger(__name__)

# Project root is two levels up from utils/
PROJECT_ROOT = Path(__file__).parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"
DATA_DIR = PROJECT_ROOT / "data"
LOG_DIR = PROJECT_ROOT / "logs"

# Phase 11a: Getrennte Datenverzeichnisse pro Bot
GAMESERVER_DATA_DIR = DATA_DIR / "gameserver"
MONITOR_DATA_DIR = DATA_DIR / "monitor"
ADMIN_DATA_DIR = DATA_DIR / "admin"


def load_env():
    """Load .env file from config directory"""
    env_path = CONFIG_DIR / ".env"
    if env_path.exists():
        load_dotenv(env_path)
    else:
        # Fallback: try project root
        alt = PROJECT_ROOT / ".env"
        if alt.exists():
            load_dotenv(alt)


def get_config():
    """Load config.json with feature toggles and intervals"""
    config_path = CONFIG_DIR / "config.json"
    if config_path.exists():
        # M30-Fix: JSON-Load in try/except wrappen — bei korrupter config.json
        # (JSONDecodeError) oder Lese-Fehler (OSError) auf Default-Struktur
        # zurueckfallen statt mit Exception abzustuerzen.
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning(
                "config.json konnte nicht geladen werden (%s) — Fallback auf Defaults",
                exc,
            )
            return _default_config()
    return _default_config()


def _default_config():
    """Return default configuration if config.json missing"""
    return {
        "features": {
            "player_tracking": True,
            "auto_backup": True,
            "onedrive_backup": False,
            "email_notifications": False,
            "auto_update": False,
            "daily_restart": True
        },
        "intervals": {
            "health_check_seconds": 120,
            "status_embed_seconds": 600,
            "voice_stats_seconds": 300,
            "performance_check_seconds": 300,
            "auto_backup_seconds": 21600,
            "player_stats_seconds": 300
        },
        "thresholds": {
            "cpu_warning": 80,
            "ram_warning": 85,
            "disk_warning": 90,
            "crash_restart_delay": 30
        },
        "restart": {
            "daily_time": "04:00",
            "countdown_minutes": 10,
            "skip_if_players_online": True,
            "min_uptime_hours": 12
        },
        "backup": {
            "max_local": 20,
            "max_onedrive": 10,
            "retention_days": 7,
            "before_restart": True
        }
    }


def save_config(config):
    """Save config.json"""
    config_path = CONFIG_DIR / "config.json"
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    # M30-Fix: Atomar schreiben — erst in .tmp, dann os.replace (atomic rename).
    # Verhindert eine halb geschriebene/korrupte config.json bei Crash/Abbruch
    # waehrend des Schreibens.
    tmp_path = config_path.with_suffix(config_path.suffix + ".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)
    os.replace(tmp_path, config_path)


def get_env(key: str, default=None, cast=None) -> str | int | float | bool | None:
    """Get environment variable with optional type casting.

    Args:
        key: Environment variable name.
        default: Fallback value if variable is not set.
        cast: Type to cast the value to (int, float, bool).

    Returns:
        The environment variable value, cast to the requested type.
    """
    val = os.getenv(key)
    if val is None:
        return default
    if cast is int:
        try:
            return int(val)
        except (ValueError, TypeError):
            return default
    if cast is float:
        try:
            return float(val)
        except (ValueError, TypeError):
            return default
    if cast is bool:
        return str(val).lower() in ("true", "1", "yes", "on")
    return val


def server_ids(key: str, default: str) -> list[str]:
    """
    Liest eine Server-Liste aus einer ENV-Variable.

    Welche Spielserver es gibt, stand bis 2026-08-14 an drei Stellen im Code
    (`bots/recon_bot.py`, `bots/operator_bot.py`, `modules/config_validator.py`)
    plus in einem halben Dutzend Anzeige- und Port-Tabellen. Einen Server
    hinzuzunehmen oder stillzulegen hiess deshalb: Code aendern, testen,
    deployen. Jetzt steht es an einer Stelle, und der Wechsel ist ein Neustart.

    Args:
        key: Name der ENV-Variable, z.B. ``MC_SERVER_IDS``.
        default: Kommaliste, die gilt, wenn die Variable fehlt.

    Returns:
        Grossgeschriebene IDs in der angegebenen Reihenfolge, ohne Leereintraege
        und ohne Dubletten. Die Reihenfolge zaehlt: der erste Eintrag ist der
        Vorgabe-Server fuer Befehle ohne Server-Angabe.

    >>> server_ids("MC_SERVER_IDS", "BMC")          # ohne gesetzte Variable
    ['BMC']
    """
    roh = get_env(key, default) or default
    gesehen: dict[str, None] = {}
    for teil in str(roh).split(","):
        sid = teil.strip().upper()
        if sid:
            gesehen.setdefault(sid, None)
    return list(gesehen)
