"""
Bruecke Dashboard <-> Bot fuer die Temp-Voice-Hub-Konfiguration (C1).

Hintergrund: Der Bot (`modules/temp_voice.TempVoiceManager`) liest seine Config
aus `data/admin/temp_voice_config.json`. Das Dashboard schrieb frueher eine
voellig andere Datei (`data/temp_voice/config.json`, Key `hub_channel_id`) — der
Bot las sie nie, die Temp-Voice-Tab-Einstellungen blieben wirkungslos.

Diese Bruecke schreibt die Hub-Liste in die echte Bot-Config (merge statt
clobber, atomic) — analog zur Leveling-guild_config-Bruecke. Der Bot uebernimmt
Aenderungen ohne Neustart (mtime-Reload im Manager).

Das Dashboard editiert Hubs als Text (eine Zeile pro Hub, Pipe-getrennt):

    hub_channel_id | category_id | limit | privat | naming-template

Beispiel:

    111111111111111111 | 444444444444444444 | 5 | nein | {user}'s COD Voice
    333333333333333333 |  | 0 | ja | {game} #{count}

Platzhalter im Naming: {user} (Anzeigename), {count} (laufende Nummer des Hubs),
{game} (optionaler Spielname). Leere Felder = Default.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from utils.config import ADMIN_DATA_DIR
from utils.logger import get_logger

logger = get_logger("web.temp_voice_bridge")

# Die echte Config-Datei die der Bot (TempVoiceManager) liest.
BOT_CONFIG_FILE: Path = ADMIN_DATA_DIR / "temp_voice_config.json"

# Wahrheits-Werte fuer das Privat-Feld im Textformat.
_TRUE_WORDS = {"ja", "yes", "1", "true", "privat", "private", "x"}


def _read_bot_config() -> dict[str, Any]:
    """Bot-Config-JSON lesen (oder leeres Dict bei fehlender/kaputter Datei)."""
    try:
        if BOT_CONFIG_FILE.exists():
            with open(BOT_CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return data
    except (json.JSONDecodeError, IOError) as e:
        logger.warning(f"Temp-Voice-Bot-Config lesen fehlgeschlagen: {e}")
    return {}


def read_temp_voice_hubs() -> list[dict[str, Any]]:
    """Hub-Liste aus der Bot-Config lesen (fuer die Dashboard-Anzeige)."""
    cfg = _read_bot_config()
    hubs = cfg.get("hubs")
    return hubs if isinstance(hubs, list) else []


def read_afk_timeout(default: int = 5) -> int:
    """AFK-Timeout (Minuten) aus der Bot-Config lesen."""
    cfg = _read_bot_config()
    try:
        return int(cfg.get("afk_timeout_minutes", default))
    except (TypeError, ValueError):
        return default


def write_temp_voice_config(
    hubs: list[dict[str, Any]],
    afk_timeout_minutes: int | None = None,
) -> bool:
    """
    Hubs (+ optional AFK-Timeout) in die Bot-Config schreiben.

    Merge statt clobber: bestehende bot-eigene Keys (z.B. interface_channel_id,
    join_channel_id) bleiben erhalten. Atomic via .tmp + replace.
    """
    cfg = _read_bot_config()
    cfg["hubs"] = hubs
    if afk_timeout_minutes is not None:
        try:
            cfg["afk_timeout_minutes"] = max(1, min(120, int(afk_timeout_minutes)))
        except (TypeError, ValueError):
            pass
    try:
        BOT_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = BOT_CONFIG_FILE.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        tmp.replace(BOT_CONFIG_FILE)
        logger.info(f"Temp-Voice-Hubs geschrieben: {len(hubs)} Hub(s)")
        return True
    except (IOError, OSError) as e:
        logger.error(f"Temp-Voice-Hubs schreiben fehlgeschlagen: {e}")
        return False


def parse_hubs_text(text: str) -> list[dict[str, Any]]:
    """
    Textarea-Inhalt in eine Hub-Liste parsen (eine Zeile pro Hub).

    Format pro Zeile (Pipe-getrennt):
        hub_channel_id | category_id | limit | privat | naming
    Leere Zeilen und Zeilen die mit '#' beginnen werden ignoriert. Zeilen
    ohne numerische hub_channel_id werden uebersprungen (robust gegen Tippfehler).
    """
    hubs: list[dict[str, Any]] = []
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("|")]
        cid = parts[0]
        if not cid.isdigit():
            continue
        hub: dict[str, Any] = {"hub_id": int(cid)}

        cat = parts[1] if len(parts) > 1 else ""
        hub["category_id"] = int(cat) if cat.isdigit() else None

        lim = parts[2] if len(parts) > 2 else ""
        hub["default_limit"] = max(0, min(99, int(lim))) if lim.isdigit() else 0

        priv = (parts[3] if len(parts) > 3 else "").lower()
        hub["default_private"] = priv in _TRUE_WORDS

        naming = parts[4] if len(parts) > 4 and parts[4] else "{user}'s Channel"
        hub["naming"] = naming[:100]

        hub["game"] = (parts[5][:50] if len(parts) > 5 else "")
        hubs.append(hub)
    return hubs


def hubs_to_text(hubs: list[dict[str, Any]]) -> str:
    """Hub-Liste in das Textarea-Format zurueckwandeln (fuer Anzeige)."""
    lines: list[str] = []
    for hub in hubs:
        if not isinstance(hub, dict):
            continue
        cid = hub.get("hub_id", "")
        cat = hub.get("category_id") or ""
        lim = hub.get("default_limit", 0)
        priv = "ja" if hub.get("default_private") else "nein"
        naming = hub.get("naming", "{user}'s Channel")
        line = f"{cid} | {cat} | {lim} | {priv} | {naming}"
        game = hub.get("game") or ""
        if game:
            line += f" | {game}"
        lines.append(line)
    return "\n".join(lines)
