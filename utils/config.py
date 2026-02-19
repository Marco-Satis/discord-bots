"""
Configuration loader - reads .env and config.json
"""

import os
import json
from pathlib import Path
from dotenv import load_dotenv

# Project root is two levels up from utils/
PROJECT_ROOT = Path(__file__).parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"
DATA_DIR = PROJECT_ROOT / "data"
LOG_DIR = PROJECT_ROOT / "logs"


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
        with open(config_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return _default_config()


def _default_config():
    """Return default configuration if config.json missing"""
    return {
        "features": {
            "chat_bridge": False,
            "word_filter": True,
            "anti_spam": True,
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
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)


def get_env(key: str, default=None, cast=None):
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
