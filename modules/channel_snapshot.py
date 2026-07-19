"""
Guild-Channel-Snapshot — Bruecke Bot -> Dashboard fuer Channel-Dropdowns.

Der Bot-Prozess (recon-bot) hat die Discord-Guild und damit die Live-Channel-Liste.
Das Web-Dashboard laeuft als eigener Prozess OHNE Bot-Token und kann die Discord-API
nicht selbst abfragen. Analog zu `temp_voice_bridge` (JSON in `data/`) schreibt der Bot
hier periodisch einen Channel-Snapshot, den das Web ueber `web/channel_bridge.py` liest
und als `/api/guild/channels` ausliefert — damit Settings-Formulare Channel-NAMEN im
Dropdown zeigen statt roher IDs.

Format `data/guild_channels.json`:
    {
      "guild_id": "123...", "guild_name": "Marcos Server",
      "updated_at": "2026-07-19T17:40:00+00:00", "count": 42,
      "channels": [
        {"id": "111", "name": "allgemein", "type": "text",
         "category_id": "999", "position": 0}, ...
      ]
    }

Kein `import discord` noetig: der Writer greift nur per Attribut-Zugriff (Duck-Typing)
auf das Guild-Objekt zu, der Reader ist reine Stdlib (im Web importierbar).
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from utils.config import DATA_DIR
from utils.logger import get_logger

logger = get_logger("channel_snapshot")

SNAPSHOT_PATH: Path = DATA_DIR / "guild_channels.json"


def build_channels(guild: Any) -> list[dict[str, Any]]:
    """
    Discord-Guild -> serialisierbare Channel-Liste (nach position sortiert).

    Duck-typed: erwartet `guild.channels` (Iterable) mit `.id`, `.name`, `.type`
    (discord.ChannelType, `.name` z.B. 'text'/'voice'/'category'/'stage_voice'/'forum'),
    optional `.category_id` und `.position`.
    """
    out: list[dict[str, Any]] = []
    for ch in getattr(guild, "channels", []) or []:
        try:
            ctype = getattr(getattr(ch, "type", None), "name", None) or "unknown"
            cat_id = getattr(ch, "category_id", None)
            out.append({
                "id": str(ch.id),
                "name": str(getattr(ch, "name", "")),
                "type": ctype,
                "category_id": str(cat_id) if cat_id else None,
                "position": int(getattr(ch, "position", 0) or 0),
            })
        except Exception as e:  # einzelne kaputte Channel nicht den ganzen Snapshot kippen lassen
            logger.warning(f"channel_snapshot: Channel uebersprungen: {e}")
    out.sort(key=lambda c: (c["type"] != "category", c["position"], c["name"].lower()))
    return out


def write_snapshot(guild: Any) -> bool:
    """
    Channel-Snapshot der Guild atomar nach SNAPSHOT_PATH schreiben.
    Returns True bei Erfolg, False bei Fehler (nie werfend — Bot-Loop-safe).
    """
    if guild is None:
        return False
    try:
        payload = {
            "guild_id": str(getattr(guild, "id", "")),
            "guild_name": str(getattr(guild, "name", "")),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "channels": build_channels(guild),
        }
        payload["count"] = len(payload["channels"])
        SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = SNAPSHOT_PATH.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=0)
        os.replace(tmp, SNAPSHOT_PATH)  # atomarer Rename
        logger.info(f"Channel-Snapshot geschrieben: {payload['count']} Channels (Guild {payload['guild_id']})")
        return True
    except Exception as e:
        logger.error(f"Channel-Snapshot fehlgeschlagen: {e}")
        return False


def read_snapshot() -> dict[str, Any]:
    """
    Snapshot lesen (Web-Seite, reine Stdlib). Bei fehlender/kaputter Datei ein
    leeres, aber valides Grundgeruest zurueckgeben (Formular faellt auf ID-Input zurueck).
    """
    try:
        with open(SNAPSHOT_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("Snapshot ist kein Objekt")
        data.setdefault("channels", [])
        data.setdefault("count", len(data.get("channels", [])))
        return data
    except FileNotFoundError:
        return {"guild_id": "", "guild_name": "", "updated_at": None, "count": 0, "channels": []}
    except Exception as e:
        logger.warning(f"Channel-Snapshot nicht lesbar: {e}")
        return {"guild_id": "", "guild_name": "", "updated_at": None, "count": 0, "channels": []}
