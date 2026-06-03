"""
Dashboard ↔ Per-Guild-Config-Bruecke (MVP-Config, Phase Dashboard-Config-Basis).

Das Admin-Bot-Dashboard speichert Modul-Configs historisch als JSON-Dateien
(`data/<modul>/config.json`). Seit Phase C liest der Bot die **Leveling**-Config
aber per-Guild aus der `guild_config`-Tabelle (Migration v5, `modules.guild_context`).

Diese Bruecke schreibt die Dashboard-Eingaben zusaetzlich in die per-Guild
`guild_config` (Haupt-Guild aus ENV `GUILD_ID`) — im Format das der Bot erwartet
(Dicts statt Listen) — und liest sie zurueck fuer die Formular-Anzeige.

Cross-Prozess: das Dashboard laeuft als eigener Prozess (web-dashboard), schreibt
in die geteilte SQLite-`guild_config`. Der Bot-Prozess zieht die Aenderung ueber
seinen 15s-Config-TTL (LevelingManager._gcfg, Review-Fix F2). Marco-only —
RBAC + weitere Guilds kommen in Wave 2.
"""
from __future__ import annotations

from typing import Any

from modules.guild_context import GuildConfig, get_primary_guild_id
from utils.logger import get_logger

logger = get_logger("web.guild_config_bridge")

# Skalare Leveling-Keys (direkt, ohne Format-Konvertierung)
_LEVELING_INT_KEYS = (
    "xp_per_message_min",
    "xp_per_message_max",
    "xp_cooldown_seconds",
    "voice_xp_per_minute",
)


async def write_leveling_config(parsed: dict[str, Any], updated_by: str = "dashboard") -> bool:
    """
    Dashboard-Leveling-Form in die per-Guild `guild_config` schreiben.

    Konvertiert das Dashboard-Listenformat in das Bot-Dictformat:
      - channel_multipliers: [{channel_id, multiplier}] -> {channel_id: factor}
      - role_rewards:        [{level, role_id}]          -> {str(level): role_id}

    Returns:
        True wenn geschrieben, False wenn keine Haupt-Guild gesetzt (ENV GUILD_ID).
    """
    gid = get_primary_guild_id()
    if gid is None:
        logger.warning("write_leveling_config: GUILD_ID nicht gesetzt — übersprungen")
        return False

    channel_multipliers: dict[str, float] = {}
    for entry in parsed.get("channel_multipliers", []) or []:
        cid = str(entry.get("channel_id", "")).strip()
        if not cid:
            continue
        try:
            channel_multipliers[cid] = float(entry.get("multiplier", 1.0))
        except (TypeError, ValueError):
            continue

    role_rewards: dict[str, int] = {}
    for entry in parsed.get("role_rewards", []) or []:
        role_id = str(entry.get("role_id", "")).strip()
        level = entry.get("level")
        if not role_id or level is None:
            continue
        try:
            role_rewards[str(int(level))] = int(role_id)
        except (TypeError, ValueError):
            continue

    mapping: dict[str, Any] = {
        "channel_multipliers": channel_multipliers,
        "role_rewards": role_rewards,
    }
    for key in _LEVELING_INT_KEYS:
        if key in parsed:
            try:
                mapping[key] = int(parsed[key])
            except (TypeError, ValueError):
                continue
    # Neue C-Felder (falls das Formular sie liefert)
    if "no_xp_channels" in parsed:
        mapping["no_xp_channels"] = [str(c) for c in parsed["no_xp_channels"]]
    if "remove_lower_rewards" in parsed:
        mapping["remove_lower_rewards"] = bool(parsed["remove_lower_rewards"])

    for key, value in mapping.items():
        await GuildConfig.set(gid, f"leveling.{key}", value, updated_by=updated_by)

    logger.info(f"Leveling-Config -> guild_config Guild {gid} ({len(mapping)} Keys)")
    return True


async def read_leveling_config() -> dict[str, Any]:
    """
    Per-Guild-Leveling-Config aus `guild_config` lesen, ins Dashboard-Format
    (Listen) zurueckkonvertieren. Leeres dict wenn keine Guild/keine Werte —
    Aufrufer faellt dann auf die JSON-Defaults zurueck.
    """
    gid = get_primary_guild_id()
    if gid is None:
        return {}

    allcfg = await GuildConfig.get_all(gid)
    prefix = "leveling."
    lev = {
        key[len(prefix):]: value
        for key, value in allcfg.items()
        if key.startswith(prefix)
    }
    if not lev:
        return {}

    cm = lev.get("channel_multipliers", {}) or {}
    rr = lev.get("role_rewards", {}) or {}

    result: dict[str, Any] = {
        "channel_multipliers": [
            {"channel_id": cid, "multiplier": factor} for cid, factor in cm.items()
        ],
        "role_rewards": [
            {"level": int(level), "role_id": role_id} for level, role_id in rr.items()
        ],
    }
    for key in _LEVELING_INT_KEYS:
        if key in lev:
            result[key] = lev[key]
    if "no_xp_channels" in lev:
        result["no_xp_channels"] = lev["no_xp_channels"]
    if "remove_lower_rewards" in lev:
        result["remove_lower_rewards"] = lev["remove_lower_rewards"]
    return result
