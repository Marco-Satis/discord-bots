"""
manual_stop_state — Persistente Markierung welche Server manuell gestoppt wurden.

Verhindert dass Health-Watchdog oder Daily-Restart einen vom User absichtlich
gestoppten Server wieder hochfaehrt.

State-File: data/manual_stop_state.json
Format: {"<server_id>": "<ISO-Timestamp wann gestoppt>", ...}

Operationen sind atomic (tmp + os.replace) und mehrfach-sicher per asyncio.Lock.
Sync-Read fuer schnelle Lookups in Watchdog-Loop (lesen ist file-read OK).

Server-IDs (Marco-facing): "satisfactory", "mc_bmc", "mc_vanilla"
Service-Names (systemd): "satisfactory.service", "minecraft-bmc.service", "minecraft-vanilla.service"

Mapping in beide Richtungen via SERVER_ID_TO_SERVICE und SERVICE_TO_SERVER_ID.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger("monitoring.manual_stop_state")

# Pfade ---------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
STATE_FILE = _PROJECT_ROOT / "data" / "manual_stop_state.json"

# Mapping -------------------------------------------------------------------
SERVER_ID_TO_SERVICE: dict[str, str] = {
    "satisfactory": "satisfactory.service",
    "mc_bmc": "minecraft-bmc.service",
    "mc_vanilla": "minecraft-vanilla.service",
}
SERVICE_TO_SERVER_ID: dict[str, str] = {v: k for k, v in SERVER_ID_TO_SERVICE.items()}

_write_lock = asyncio.Lock()


def _read_state() -> dict[str, str]:
    """Liest State synchron (klein, schneller als Lock fuer Lookups)."""
    try:
        if STATE_FILE.exists():
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
    except (OSError, json.JSONDecodeError) as e:
        logger.warning(f"manual_stop_state read fehlgeschlagen: {e}")
    return {}


def _write_state_unlocked(state: dict[str, str]) -> None:
    """Atomic write — .tmp + os.replace. NUR aufrufen wenn _write_lock gehalten wird."""
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATE_FILE.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
        os.replace(tmp, STATE_FILE)
    except OSError as e:
        logger.error(f"manual_stop_state write fehlgeschlagen: {e}")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def mark_stopped(server_id: str) -> None:
    """Markiert Server als manuell gestoppt (Auto-Restart-Block).

    F03-Fix: Read-Modify-Write komplett unter _write_lock → kein Lost-Update
    bei gleichzeitigen Mark-Operationen.
    """
    async with _write_lock:
        state = _read_state()
        state[server_id] = datetime.now(timezone.utc).isoformat()
        _write_state_unlocked(state)
    logger.info(f"Server '{server_id}' als manuell-gestoppt markiert (Auto-Restart blockiert)")


async def mark_started(server_id: str) -> None:
    """Loescht manuelle-Stop-Markierung (Auto-Restart wieder aktiv).

    F03-Fix: Read-Modify-Write komplett unter _write_lock.
    """
    async with _write_lock:
        state = _read_state()
        if server_id not in state:
            return
        del state[server_id]
        _write_state_unlocked(state)
    logger.info(f"Server '{server_id}' wieder gestartet — Auto-Restart-Block aufgehoben")


def is_manually_stopped(server_id: str) -> bool:
    """Synchron-Check ob Server manuell gestoppt wurde."""
    return server_id in _read_state()


def is_service_manually_stopped(service_name: str) -> bool:
    """Wie is_manually_stopped, aber mit systemd-Service-Namen als Input.

    Fuer Watchdog der mit Service-Namen arbeitet. Unbekannte Services -> False
    (nicht blocken, kein Override).
    """
    server_id = SERVICE_TO_SERVER_ID.get(service_name)
    if not server_id:
        return False
    return is_manually_stopped(server_id)


def get_stopped_servers() -> dict[str, str]:
    """Liste aller aktuell manuell-gestoppten Server (server_id -> ISO-Timestamp)."""
    return _read_state()


def stopped_at(server_id: str) -> Optional[str]:
    """ISO-Timestamp wann Server gestoppt wurde, oder None."""
    return _read_state().get(server_id)
