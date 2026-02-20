"""
Ticket Manager — Support-Ticket-System fuer den Admin Bot

Verwaltet Support-Tickets mit Transcripts und Konfiguration.
Tickets werden als private Channels in einer konfigurierbaren
Kategorie erstellt und koennen nach dem Schliessen als Transcript
in einen Log-Channel gesendet werden.

Persistenz:
  - Ticket-Daten:  data/admin/tickets.json
  - Konfiguration: data/admin/ticket_config.json

Datenformat (tickets.json):
{
  "next_id": 1,
  "tickets": {
    "ticket_id_str": {
      "channel_id": 123456,
      "user_id": 789012,
      "subject": "Betreff",
      "created_at": "2025-01-01T00:00:00",
      "status": "open",
      "closed_at": null,
      "closed_by": null,
      "transcript": [
        {"author": "User#1234", "author_id": 123, "content": "...", "timestamp": "..."}
      ]
    }
  }
}

Konfiguration (ticket_config.json):
{
  "support_roles": [role_id, ...],
  "ticket_category_id": category_channel_id,
  "log_channel_id": log_channel_id
}
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from utils.logger import get_logger
from utils.config import ADMIN_DATA_DIR

logger = get_logger("ticket_manager")

# Standard-Pfade
TICKETS_FILE = ADMIN_DATA_DIR / "tickets.json"
TICKET_CONFIG_FILE = ADMIN_DATA_DIR / "ticket_config.json"


def _default_config() -> dict[str, Any]:
    """Standard-Konfiguration fuer das Ticket-System zurueckgeben."""
    return {
        "support_roles": [],
        "ticket_category_id": None,
        "log_channel_id": None,
    }


class TicketManager:
    """
    Verwaltet Support-Tickets mit Persistenz und Transcripts.

    Funktionen:
    - Tickets erstellen und schliessen
    - Transcript-Eintraege speichern
    - Offene Tickets abfragen (gesamt oder pro User)
    - Automatische Ticket-ID-Vergabe

    Der Manager kuemmert sich NUR um die Datenverwaltung.
    Das Erstellen von Channels, Setzen von Berechtigungen
    und Senden von Embeds wird vom Cog uebernommen.
    """

    def __init__(
        self,
        data_file: Path | None = None,
        config_file: Path | None = None,
    ) -> None:
        """
        Args:
            data_file: Pfad zur Ticket-Daten-JSON (Standard: data/admin/tickets.json)
            config_file: Pfad zur Config-JSON (Standard: data/admin/ticket_config.json)
        """
        self.data_file = data_file or TICKETS_FILE
        self.config_file = config_file or TICKET_CONFIG_FILE
        self._data: dict[str, Any] = {"next_id": 1, "tickets": {}}
        self._config: dict[str, Any] = _default_config()
        self._load()
        self._load_config()

    # ------------------------------------------------------------------
    # Persistence — Ticket-Daten
    # ------------------------------------------------------------------

    def _load(self) -> None:
        """Ticket-Daten von Disk laden."""
        try:
            if self.data_file.exists():
                with open(self.data_file, "r", encoding="utf-8") as f:
                    self._data = json.load(f)
                self._data.setdefault("next_id", 1)
                self._data.setdefault("tickets", {})
                ticket_count = len(self._data["tickets"])
                open_count = sum(
                    1 for t in self._data["tickets"].values()
                    if t.get("status") == "open"
                )
                logger.info(
                    f"Ticket-Daten geladen: {ticket_count} Tickets "
                    f"({open_count} offen)"
                )
            else:
                logger.info("Keine Ticket-Datei vorhanden, starte leer")
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"Ticket-Daten laden fehlgeschlagen: {e}")

    def _save(self) -> None:
        """Ticket-Daten auf Disk speichern."""
        try:
            self.data_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.data_file, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2, ensure_ascii=False)
        except IOError as e:
            logger.error(f"Ticket-Daten speichern fehlgeschlagen: {e}")

    # ------------------------------------------------------------------
    # Persistence — Konfiguration
    # ------------------------------------------------------------------

    def _load_config(self) -> None:
        """Ticket-Konfiguration von Disk laden."""
        try:
            if self.config_file.exists():
                with open(self.config_file, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                defaults = _default_config()
                for key, value in defaults.items():
                    loaded.setdefault(key, value)
                self._config = loaded
                logger.info("Ticket-Konfiguration geladen")
            else:
                logger.info(
                    "Keine Ticket-Config vorhanden, verwende Standardwerte"
                )
                self._save_config()
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"Ticket-Config laden fehlgeschlagen: {e}")

    def _save_config(self) -> None:
        """Ticket-Konfiguration auf Disk speichern."""
        try:
            self.config_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.config_file, "w", encoding="utf-8") as f:
                json.dump(self._config, f, indent=2, ensure_ascii=False)
        except IOError as e:
            logger.error(f"Ticket-Config speichern fehlgeschlagen: {e}")

    # ------------------------------------------------------------------
    # Ticket erstellen
    # ------------------------------------------------------------------

    def create_ticket(
        self,
        user_id: int,
        subject: str,
        channel_id: int = 0,
    ) -> dict[str, Any]:
        """
        Neues Ticket erstellen und speichern.

        Vergibt automatisch eine fortlaufende Ticket-ID.
        Der channel_id wird typischerweise nach der Channel-Erstellung
        via update_ticket_channel() gesetzt.

        Args:
            user_id: Discord-User-ID des Ticket-Erstellers
            subject: Betreff / Beschreibung des Tickets
            channel_id: Discord-Channel-ID (0 wenn noch nicht erstellt)

        Returns:
            Dict mit allen Ticket-Daten (inklusive ticket_id)
        """
        ticket_id = self._data["next_id"]
        self._data["next_id"] += 1

        ticket_id_str = str(ticket_id)
        now = datetime.now()

        ticket: dict[str, Any] = {
            "ticket_id": ticket_id,
            "channel_id": channel_id,
            "user_id": user_id,
            "subject": subject,
            "created_at": now.isoformat(),
            "status": "open",
            "closed_at": None,
            "closed_by": None,
            "transcript": [],
        }

        self._data["tickets"][ticket_id_str] = ticket
        self._save()

        logger.info(
            f"Ticket #{ticket_id} erstellt von User {user_id}: {subject}"
        )
        return dict(ticket)

    # ------------------------------------------------------------------
    # Ticket aktualisieren
    # ------------------------------------------------------------------

    def update_ticket_channel(self, ticket_id: int, channel_id: int) -> None:
        """
        Channel-ID eines Tickets nachtraeglich setzen.

        Wird aufgerufen nachdem der Discord-Channel erfolgreich
        erstellt wurde.

        Args:
            ticket_id: Ticket-ID
            channel_id: Discord-Channel-ID
        """
        tid = str(ticket_id)
        if tid in self._data["tickets"]:
            self._data["tickets"][tid]["channel_id"] = channel_id
            self._save()

    # ------------------------------------------------------------------
    # Ticket schliessen
    # ------------------------------------------------------------------

    def close_ticket(
        self,
        ticket_id: int,
        closed_by: int,
        reason: str | None = None,
    ) -> dict[str, Any] | None:
        """
        Ticket schliessen und Status aktualisieren.

        Args:
            ticket_id: Ticket-ID
            closed_by: Discord-User-ID des Schliessenden
            reason: Optionaler Grund fuer das Schliessen

        Returns:
            Ticket-Dict oder None wenn nicht gefunden
        """
        tid = str(ticket_id)
        ticket = self._data["tickets"].get(tid)

        if not ticket:
            return None

        if ticket.get("status") == "closed":
            logger.warning(f"Ticket #{ticket_id} ist bereits geschlossen")
            return dict(ticket)

        now = datetime.now()
        ticket["status"] = "closed"
        ticket["closed_at"] = now.isoformat()
        ticket["closed_by"] = closed_by

        if reason:
            self.add_transcript_entry(
                ticket_id,
                author="System",
                content=f"Ticket geschlossen: {reason}",
                author_id=0,
            )

        self._save()

        logger.info(
            f"Ticket #{ticket_id} geschlossen von User {closed_by}"
            + (f" — Grund: {reason}" if reason else "")
        )
        return dict(ticket)

    # ------------------------------------------------------------------
    # Transcript
    # ------------------------------------------------------------------

    def add_transcript_entry(
        self,
        ticket_id: int,
        author: str,
        content: str,
        author_id: int = 0,
    ) -> bool:
        """
        Nachricht zum Ticket-Transcript hinzufuegen.

        Args:
            ticket_id: Ticket-ID
            author: Anzeigename des Autors
            content: Nachrichteninhalt
            author_id: Discord-User-ID des Autors (0 fuer System)

        Returns:
            True wenn erfolgreich, False wenn Ticket nicht gefunden
        """
        tid = str(ticket_id)
        ticket = self._data["tickets"].get(tid)

        if not ticket:
            return False

        entry = {
            "author": author,
            "author_id": author_id,
            "content": content,
            "timestamp": datetime.now().isoformat(),
        }

        ticket["transcript"].append(entry)
        self._save()
        return True

    # ------------------------------------------------------------------
    # Abfragen
    # ------------------------------------------------------------------

    def get_ticket(self, ticket_id: int) -> dict[str, Any] | None:
        """
        Ein bestimmtes Ticket zurueckgeben.

        Args:
            ticket_id: Ticket-ID

        Returns:
            Ticket-Dict (Kopie) oder None
        """
        tid = str(ticket_id)
        ticket = self._data["tickets"].get(tid)
        if ticket:
            return dict(ticket)
        return None

    def get_ticket_by_channel(self, channel_id: int) -> dict[str, Any] | None:
        """
        Ticket anhand der Channel-ID finden.

        Args:
            channel_id: Discord-Channel-ID

        Returns:
            Ticket-Dict (Kopie) oder None
        """
        for ticket in self._data["tickets"].values():
            if ticket.get("channel_id") == channel_id:
                return dict(ticket)
        return None

    def get_open_tickets(
        self,
        user_id: int | None = None,
    ) -> list[dict[str, Any]]:
        """
        Alle offenen Tickets zurueckgeben.

        Args:
            user_id: Wenn angegeben, nur Tickets dieses Users.
                     Sonst alle offenen Tickets.

        Returns:
            Liste von Ticket-Dicts (neueste zuerst)
        """
        tickets: list[dict[str, Any]] = []

        for ticket in self._data["tickets"].values():
            if ticket.get("status") != "open":
                continue
            if user_id is not None and ticket.get("user_id") != user_id:
                continue
            tickets.append(dict(ticket))

        # Nach Erstelldatum sortieren (neueste zuerst)
        tickets.sort(
            key=lambda t: t.get("created_at", ""),
            reverse=True,
        )
        return tickets

    def get_user_ticket_count(self, user_id: int) -> int:
        """
        Anzahl offener Tickets eines Users zurueckgeben.

        Args:
            user_id: Discord-User-ID

        Returns:
            Anzahl offener Tickets
        """
        return sum(
            1 for t in self._data["tickets"].values()
            if t.get("user_id") == user_id and t.get("status") == "open"
        )

    # ------------------------------------------------------------------
    # Konfiguration
    # ------------------------------------------------------------------

    @property
    def support_roles(self) -> list[int]:
        """Support-Rollen-IDs zurueckgeben."""
        return list(self._config.get("support_roles", []))

    @property
    def ticket_category_id(self) -> int | None:
        """Ticket-Kategorie-Channel-ID zurueckgeben."""
        return self._config.get("ticket_category_id")

    @property
    def log_channel_id(self) -> int | None:
        """Log-Channel-ID fuer Transcripts zurueckgeben."""
        return self._config.get("log_channel_id")

    def set_config(
        self,
        support_roles: list[int] | None = None,
        ticket_category_id: int | None = None,
        log_channel_id: int | None = None,
    ) -> None:
        """
        Ticket-Konfiguration aktualisieren.

        Nur uebergebene Werte werden geaendert (None = unveraendert).

        Args:
            support_roles: Liste von Support-Rollen-IDs
            ticket_category_id: Kategorie-Channel-ID fuer neue Tickets
            log_channel_id: Channel-ID fuer Transcript-Logs
        """
        if support_roles is not None:
            self._config["support_roles"] = support_roles
        if ticket_category_id is not None:
            self._config["ticket_category_id"] = ticket_category_id
        if log_channel_id is not None:
            self._config["log_channel_id"] = log_channel_id

        self._save_config()
        logger.info("Ticket-Konfiguration aktualisiert")

    @property
    def config(self) -> dict[str, Any]:
        """Aktuelle Konfiguration zurueckgeben (Kopie)."""
        return dict(self._config)

    # ------------------------------------------------------------------
    # Transcript formatieren
    # ------------------------------------------------------------------

    def format_transcript(self, ticket_id: int) -> str:
        """
        Ticket-Transcript als lesbaren Text formatieren.

        Args:
            ticket_id: Ticket-ID

        Returns:
            Formatierter Transcript-Text oder leerer String
        """
        ticket = self.get_ticket(ticket_id)
        if not ticket:
            return ""

        transcript = ticket.get("transcript", [])
        if not transcript:
            return "(Kein Transcript vorhanden)"

        lines: list[str] = []
        lines.append(
            f"=== Ticket #{ticket_id} — {ticket.get('subject', 'Kein Betreff')} ==="
        )
        lines.append(
            f"Erstellt von: User {ticket.get('user_id')} "
            f"am {ticket.get('created_at', '?')}"
        )
        lines.append(f"Status: {ticket.get('status', '?')}")
        lines.append("")

        for entry in transcript:
            timestamp = entry.get("timestamp", "?")
            try:
                dt = datetime.fromisoformat(timestamp)
                timestamp = dt.strftime("%d.%m.%Y %H:%M:%S")
            except (ValueError, TypeError):
                pass

            author = entry.get("author", "?")
            content = entry.get("content", "")
            lines.append(f"[{timestamp}] {author}: {content}")

        return "\n".join(lines)
