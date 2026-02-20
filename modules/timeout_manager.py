"""
Timeout Manager — Serveruebergreifendes temporaeres Ban-System

Verwaltet Timeouts (temporaere Bans) zentral in einer JSON-Datei.
Unterstuetzt mehrere Server gleichzeitig (SAT, BMC, Vanilla, etc.)
und trackt aktive Timeouts sowie Ban-Historie.

Der Manager kuemmert sich NUR um die Datenverwaltung (Speichern,
Laden, Ablauf-Pruefung). Das eigentliche Bannen/Entbannen auf
den Servern wird vom Cog uebernommen.

Dateiformat (data/timeouts.json):
{
  "active": {
    "discord_user_id_str": {
      "player_name": "Name",
      "discord_id": 123456,
      "reason": "Grund",
      "set_by": "AdminName",
      "set_by_id": 789,
      "set_at": "2025-01-01T00:00:00",
      "expires_at": "2025-01-01T01:00:00",
      "servers": ["sat", "bmc", "vanilla"],
      "ip": "1.2.3.4",
      "active": true
    }
  },
  "history": [...]
}
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from utils.logger import get_logger

logger = get_logger("timeout_manager")

# Maximale Anzahl an History-Eintraegen (aelteste werden entfernt)
MAX_HISTORY = 100


class TimeoutManager:
    """
    Verwaltet temporaere Bans (Timeouts) serveruebergreifend.

    Funktionen:
    - Timeout setzen/aufheben fuer mehrere Server gleichzeitig
    - Automatische Ablauf-Erkennung (get_expired)
    - Persistenz via JSON-Datei
    - Ban-Historie mit max. 100 Eintraegen

    Der Manager speichert und verwaltet nur die Daten.
    Das tatsaechliche Kicken/Bannen auf den Servern
    muss vom aufrufenden Cog durchgefuehrt werden.
    """

    def __init__(self, data_file: Path) -> None:
        """
        Args:
            data_file: Pfad zur JSON-Datei (z.B. data/timeouts.json)
        """
        self.data_file = data_file
        self._data: dict[str, Any] = {"active": {}, "history": []}
        self._lock = asyncio.Lock()
        self._load()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self) -> None:
        """Timeout-Daten von Disk laden"""
        try:
            if self.data_file.exists():
                with open(self.data_file, "r", encoding="utf-8") as f:
                    self._data = json.load(f)
                # Sicherstellen dass beide Keys existieren
                self._data.setdefault("active", {})
                self._data.setdefault("history", [])
                active_count = len(self._data["active"])
                history_count = len(self._data["history"])
                logger.info(
                    f"Timeout-Daten geladen: {active_count} aktiv, "
                    f"{history_count} in Historie"
                )
            else:
                logger.info("Keine Timeout-Datei vorhanden, starte leer")
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"Timeout-Daten laden fehlgeschlagen: {e}")

    def _save(self) -> None:
        """Timeout-Daten auf Disk speichern"""
        try:
            self.data_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.data_file, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2, ensure_ascii=False)
        except IOError as e:
            logger.error(f"Timeout-Daten speichern fehlgeschlagen: {e}")

    # ------------------------------------------------------------------
    # Timeout setzen
    # ------------------------------------------------------------------

    async def set_timeout(
        self,
        player_name: str,
        discord_id: int,
        duration_minutes: int,
        reason: str,
        set_by: str,
        set_by_id: int,
        servers: list[str],
        bot: Any = None,
    ) -> tuple[bool, str, list[str]]:
        """
        Neuen Timeout-Eintrag erstellen und speichern.

        Erstellt nur den Datensatz — das tatsaechliche Bannen auf
        den Servern (RCON, UFW, etc.) muss der Cog uebernehmen.

        Args:
            player_name: Spielername (Minecraft/Satisfactory)
            discord_id: Discord-User-ID des Spielers
            duration_minutes: Timeout-Dauer in Minuten
            reason: Grund fuer den Timeout
            set_by: Display-Name des Admins
            set_by_id: Discord-ID des Admins
            servers: Liste von Server-IDs (z.B. ["sat", "bmc", "vanilla"])
            bot: Bot-Instanz (fuer zukuenftige Erweiterungen, aktuell unused)

        Returns:
            (success, message, action_results)
            - success: True wenn Eintrag erstellt
            - message: Status-Nachricht
            - action_results: Liste von Info-Strings (was gemacht wurde)
        """
        action_results: list[str] = []

        if not discord_id:
            return (
                False,
                f"Kein Discord-Account fuer **{player_name}** gefunden. "
                "Timeout kann nicht gesetzt werden.",
                [],
            )

        async with self._lock:
            return await self._set_timeout_locked(
                player_name, discord_id, duration_minutes, reason,
                set_by, set_by_id, servers, bot, action_results,
            )

    async def _set_timeout_locked(
        self,
        player_name: str,
        discord_id: int,
        duration_minutes: int,
        reason: str,
        set_by: str,
        set_by_id: int,
        servers: list[str],
        bot: Any,
        action_results: list[str],
    ) -> tuple[bool, str, list[str]]:
        """Interner set_timeout — wird unter Lock ausgefuehrt."""
        from datetime import timedelta

        discord_id_str = str(discord_id)

        # Pruefen ob bereits ein aktiver Timeout besteht
        if discord_id_str in self._data["active"]:
            existing = self._data["active"][discord_id_str]
            expires = existing.get("expires_at", "?")
            return (
                False,
                f"**{player_name}** hat bereits einen aktiven Timeout "
                f"(laeuft ab: {expires})",
                [],
            )

        now = datetime.now()
        expires_at = now + timedelta(minutes=duration_minutes)

        # IP aus IP-Trackern ermitteln falls Bot verfuegbar
        ip: str | None = None
        if bot:
            ip = self._resolve_player_ip(player_name, bot)
            if ip:
                action_results.append(f"IP ermittelt: {ip}")

        # Timeout-Eintrag erstellen
        entry: dict[str, Any] = {
            "player_name": player_name,
            "discord_id": discord_id,
            "reason": reason,
            "set_by": set_by,
            "set_by_id": set_by_id,
            "set_at": now.isoformat(),
            "expires_at": expires_at.isoformat(),
            "servers": [s.lower() for s in servers],
            "ip": ip,
            "active": True,
        }

        self._data["active"][discord_id_str] = entry
        self._save()

        # Dauer-Anzeige formatieren
        if duration_minutes >= 60:
            display_duration = f"{duration_minutes // 60}h {duration_minutes % 60}m"
        else:
            display_duration = f"{duration_minutes} Min"

        servers_str = ", ".join(entry["servers"])
        action_results.append(f"Timeout gespeichert: {display_duration}")
        action_results.append(f"Server: {servers_str}")

        message = (
            f"Timeout fuer **{player_name}** gesetzt: "
            f"{display_duration} auf {servers_str}"
        )

        logger.info(
            f"Timeout gesetzt: {player_name} (Discord: {discord_id}) "
            f"fuer {duration_minutes}min auf [{servers_str}] "
            f"von {set_by} — {reason}"
        )

        return True, message, action_results

    # ------------------------------------------------------------------
    # Timeout aufheben
    # ------------------------------------------------------------------

    async def lift_timeout(
        self,
        discord_id: int,
        ended_by: str | None = None,
    ) -> tuple[bool, dict | None]:
        """
        Aktiven Timeout aufheben und in Historie verschieben.

        Args:
            discord_id: Discord-User-ID des Spielers
            ended_by: Wer den Timeout aufgehoben hat (None = automatisch/expired)

        Returns:
            (was_active, timeout_data)
            - was_active: True wenn ein aktiver Timeout gefunden wurde
            - timeout_data: Der verschobene Eintrag oder None
        """
        async with self._lock:
            return self._lift_timeout_locked(discord_id, ended_by)

    def _lift_timeout_locked(
        self,
        discord_id: int,
        ended_by: str | None = None,
    ) -> tuple[bool, dict | None]:
        """Interner lift_timeout — wird unter Lock ausgefuehrt."""
        discord_id_str = str(discord_id)

        if discord_id_str not in self._data["active"]:
            return False, None

        entry = self._data["active"].pop(discord_id_str)
        now = datetime.now()

        # Bestimmen ob manuell oder abgelaufen
        if ended_by:
            ended_reason = "manual"
        else:
            ended_reason = "expired"

        # History-Eintrag erstellen
        history_entry: dict[str, Any] = {
            "player_name": entry.get("player_name", "?"),
            "discord_id": entry.get("discord_id"),
            "reason": entry.get("reason", "?"),
            "set_by": entry.get("set_by", "?"),
            "set_at": entry.get("set_at"),
            "expires_at": entry.get("expires_at"),
            "ended_at": now.isoformat(),
            "ended_reason": ended_reason,
            "ended_by": ended_by,
            "servers": entry.get("servers", []),
        }

        # IP uebernehmen falls vorhanden
        if entry.get("ip"):
            history_entry["ip"] = entry["ip"]

        self._data["history"].append(history_entry)

        # Historie nach set_at absteigend sortieren
        self._data["history"].sort(
            key=lambda h: h.get("set_at", ""),
            reverse=True,
        )

        # Historie auf MAX_HISTORY begrenzen
        if len(self._data["history"]) > MAX_HISTORY:
            self._data["history"] = self._data["history"][:MAX_HISTORY]

        self._save()

        player_name = entry.get("player_name", "?")
        logger.info(
            f"Timeout aufgehoben: {player_name} (Discord: {discord_id}) "
            f"— Grund: {ended_reason}"
            + (f" von {ended_by}" if ended_by else "")
        )

        return True, entry

    # ------------------------------------------------------------------
    # Abfragen
    # ------------------------------------------------------------------

    def get_active_timeout(self, discord_id: int) -> dict | None:
        """
        Aktiven Timeout fuer einen Discord-User zurueckgeben.

        Args:
            discord_id: Discord-User-ID

        Returns:
            Timeout-Dict oder None wenn kein aktiver Timeout
        """
        return self._data["active"].get(str(discord_id))

    def get_all_active(self) -> list[dict]:
        """
        Alle aktiven Timeouts zurueckgeben.

        Returns:
            Liste aller aktiven Timeout-Eintraege
        """
        return list(self._data["active"].values())

    def get_expired(self) -> list[dict]:
        """
        Abgelaufene Timeouts zurueckgeben (fuer Background-Task).

        Vergleicht datetime.now() mit expires_at jedes aktiven Timeouts.

        Returns:
            Liste von Timeout-Eintraegen deren Ablaufzeit ueberschritten ist
        """
        now = datetime.now()
        expired: list[dict] = []

        for entry in self._data["active"].values():
            expires_at_str = entry.get("expires_at")
            if not expires_at_str:
                continue
            try:
                expires_at = datetime.fromisoformat(expires_at_str)
                if expires_at < now:
                    expired.append(entry)
            except (ValueError, TypeError):
                logger.warning(
                    f"Ungueltiges expires_at fuer {entry.get('player_name')}: "
                    f"{expires_at_str}"
                )

        return expired

    def get_history(self, discord_id: int | None = None) -> list[dict]:
        """
        Timeout-Historie zurueckgeben.

        Args:
            discord_id: Wenn angegeben, nur Eintraege dieses Users.
                        Sonst die letzten 20 Eintraege.

        Returns:
            Liste von History-Eintraegen (neueste zuerst)
        """
        history = self._data.get("history", [])

        if discord_id is not None:
            return [
                h for h in history
                if h.get("discord_id") == discord_id
            ]

        # Ohne Filter: letzte 20 zurueckgeben
        return history[:20]

    def is_timed_out(self, discord_id: int) -> bool:
        """
        Pruefen ob ein Discord-User aktuell einen Timeout hat.

        Args:
            discord_id: Discord-User-ID

        Returns:
            True wenn aktiver Timeout vorhanden
        """
        return str(discord_id) in self._data["active"]

    # ------------------------------------------------------------------
    # Hilfsmethoden
    # ------------------------------------------------------------------

    def _resolve_player_ip(self, player_name: str, bot: Any) -> str | None:
        """
        IP-Adresse eines Spielers aus den IP-Trackern des Bots ermitteln.

        Durchsucht bot.mc_ip_trackers und bot.player_ip_tracker
        nach der IP des Spielers.

        Args:
            player_name: Spielername
            bot: Bot-Instanz mit IP-Tracker-Attributen

        Returns:
            IP-Adresse als String oder None
        """
        # Minecraft IP-Tracker durchsuchen (GameServer Bot)
        mc_trackers = getattr(bot, "mc_ip_trackers", None)
        if mc_trackers and isinstance(mc_trackers, dict):
            for tracker in mc_trackers.values():
                try:
                    ip = tracker.get_ip(player_name)
                    if ip:
                        return ip
                except Exception as e:
                    logger.warning(f"MC IP-Lookup fehlgeschlagen: {e}")

        # SAT IP-Tracker (Monitor Bot)
        sat_tracker = getattr(bot, "player_ip_tracker", None)
        if sat_tracker:
            try:
                ip = sat_tracker.get_ip(player_name)
                if ip:
                    return ip
            except Exception as e:
                logger.warning(f"SAT IP-Lookup fehlgeschlagen: {e}")

        return None
