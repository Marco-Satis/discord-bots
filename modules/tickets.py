"""
Ticket Manager — Support-Ticket-System fuer den Admin Bot

Verwaltet Support-Tickets mit Transcripts und Konfiguration.
Tickets werden als private Channels in einer konfigurierbaren
Kategorie erstellt und koennen nach dem Schliessen als Transcript
in einen Log-Channel gesendet werden.

Persistenz (Dual-Read):
  - Primaer:       SQLite via db_manager (tickets / ticket_messages Tabellen)
  - JSON-Fallback: data/admin/tickets.json  (nur Lesen beim Start, kein Schreiben)
  - Konfiguration: data/admin/ticket_config.json  (bleibt JSON)

Ablauf beim Start:
  1. __init__ laedt JSON-Fallback in _data (synchron)
  2. Cog ruft load_from_db() auf — ueberschreibt _data mit SQLite-Daten

Schreiboperationen gehen NUR noch in die SQLite-Datenbank.
_save() ist ein No-Op (JSON-Ticket-Schreibung deaktiviert).

Datenformat (In-Memory _data):
{
  "next_id": 1,
  "tickets": {
    "ticket_id_str": {
      "ticket_id": int,
      "channel_id": int,
      "user_id": int,
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

Konfiguration (ticket_config.json — bleibt JSON):
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
from modules.database.db_manager import get_db

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

    Dual-Read-Modus:
    - Lesen:    Aus dem In-Memory-Cache (_data), befuellt via SQLite
                oder JSON-Fallback
    - Schreiben: Direkt in die SQLite-Datenbank + In-Memory-Update

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
            data_file: Pfad zur Ticket-Daten-JSON (Fallback, Standard: data/admin/tickets.json)
            config_file: Pfad zur Config-JSON (Standard: data/admin/ticket_config.json)
        """
        self.data_file = data_file or TICKETS_FILE
        self.config_file = config_file or TICKET_CONFIG_FILE
        self._data: dict[str, Any] = {"next_id": 1, "tickets": {}}
        self._config: dict[str, Any] = _default_config()
        self._db_loaded: bool = False
        self._load()
        self._load_config()

    # ------------------------------------------------------------------
    # Persistence — Ticket-Daten (JSON-Fallback, nur Lesen)
    # ------------------------------------------------------------------

    def _load(self) -> None:
        """Ticket-Daten von JSON laden (Fallback fuer Erststart / Migration)."""
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
                    f"Ticket-Daten (JSON-Fallback) geladen: {ticket_count} Tickets "
                    f"({open_count} offen)"
                )
            else:
                logger.info("Keine Ticket-JSON vorhanden, starte leer")
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"Ticket-Daten (JSON) laden fehlgeschlagen: {e}")

    def _save(self) -> None:
        """No-Op — JSON-Ticket-Schreibung deaktiviert (SQLite ist primaer)."""
        # Schreiboperationen gehen direkt in die SQLite-Datenbank.
        # Diese Methode bleibt als No-Op erhalten, damit bestehende Aufrufe
        # keinen Fehler verursachen.
        pass

    # ------------------------------------------------------------------
    # Persistence — SQLite (primaere Datenquelle)
    # ------------------------------------------------------------------

    async def load_from_db(self) -> None:
        """
        Ticket-Daten aus der SQLite-Datenbank laden und _data befuellen.

        Ueberschreibt die JSON-Fallback-Daten komplett.
        Wird typischerweise einmal beim Cog-Start aufgerufen.
        """
        try:
            db = await get_db()

            # Alle Tickets laden
            cursor = await db.execute(
                "SELECT id, channel_id, user_id, subject, status, "
                "created_at, closed_at, closed_by FROM tickets "
                "ORDER BY id ASC"
            )
            rows = await cursor.fetchall()

            tickets: dict[str, Any] = {}
            max_id = 0

            for row in rows:
                ticket_id = row[0]
                if ticket_id > max_id:
                    max_id = ticket_id

                # channel_id und user_id als int konvertieren (DB speichert TEXT)
                channel_id_raw = row[1]
                user_id_raw = row[2]
                closed_by_raw = row[7]

                try:
                    channel_id = int(channel_id_raw) if channel_id_raw else 0
                except (ValueError, TypeError):
                    channel_id = 0

                try:
                    user_id = int(user_id_raw) if user_id_raw else 0
                except (ValueError, TypeError):
                    user_id = 0

                try:
                    closed_by = int(closed_by_raw) if closed_by_raw else None
                except (ValueError, TypeError):
                    closed_by = None

                # created_at / closed_at als ISO-String
                created_at = row[5]
                if created_at and not isinstance(created_at, str):
                    created_at = str(created_at)

                closed_at = row[6]
                if closed_at and not isinstance(closed_at, str):
                    closed_at = str(closed_at)

                ticket: dict[str, Any] = {
                    "ticket_id": ticket_id,
                    "channel_id": channel_id,
                    "user_id": user_id,
                    "subject": row[3] or "",
                    "status": row[4] or "open",
                    "created_at": created_at or datetime.now().isoformat(),
                    "closed_at": closed_at,
                    "closed_by": closed_by,
                    "transcript": [],
                }

                tickets[str(ticket_id)] = ticket

            # Transcript-Nachrichten fuer alle Tickets laden
            if tickets:
                msg_cursor = await db.execute(
                    "SELECT ticket_id, user_id, content, timestamp "
                    "FROM ticket_messages ORDER BY id ASC"
                )
                msg_rows = await msg_cursor.fetchall()

                for msg_row in msg_rows:
                    tid_str = str(msg_row[0])
                    if tid_str in tickets:
                        msg_user_id = msg_row[1] or "0"
                        msg_timestamp = msg_row[3]
                        if msg_timestamp and not isinstance(msg_timestamp, str):
                            msg_timestamp = str(msg_timestamp)

                        entry = {
                            "author": f"User {msg_user_id}",
                            "author_id": int(msg_user_id) if msg_user_id else 0,
                            "content": msg_row[2] or "",
                            "timestamp": msg_timestamp or datetime.now().isoformat(),
                        }
                        tickets[tid_str]["transcript"].append(entry)

            self._data = {
                "next_id": max_id + 1,
                "tickets": tickets,
            }
            self._db_loaded = True

            ticket_count = len(tickets)
            open_count = sum(
                1 for t in tickets.values() if t.get("status") == "open"
            )
            logger.info(
                f"Ticket-Daten aus SQLite geladen: {ticket_count} Tickets "
                f"({open_count} offen)"
            )

        except Exception as e:
            logger.error(
                f"Ticket-Daten aus SQLite laden fehlgeschlagen: {e} "
                f"— verwende JSON-Fallback"
            )
            # _data bleibt auf JSON-Fallback-Stand

    # ------------------------------------------------------------------
    # Persistence — Konfiguration (bleibt JSON)
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

    async def create_ticket(
        self,
        user_id: int,
        subject: str,
        channel_id: int = 0,
    ) -> dict[str, Any]:
        """
        Neues Ticket erstellen und in SQLite speichern.

        Vergibt automatisch eine fortlaufende Ticket-ID via AUTOINCREMENT.
        Der channel_id wird typischerweise nach der Channel-Erstellung
        via update_ticket_channel() gesetzt.

        Args:
            user_id: Discord-User-ID des Ticket-Erstellers
            subject: Betreff / Beschreibung des Tickets
            channel_id: Discord-Channel-ID (0 wenn noch nicht erstellt)

        Returns:
            Dict mit allen Ticket-Daten (inklusive ticket_id)
        """
        now = datetime.now()

        # In SQLite einfuegen
        db_ticket_id: int | None = None
        try:
            db = await get_db()
            cursor = await db.execute(
                "INSERT INTO tickets (channel_id, user_id, subject, status, created_at) "
                "VALUES (?, ?, ?, 'open', ?)",
                (str(channel_id), str(user_id), subject, now.isoformat()),
            )
            await db.commit()
            db_ticket_id = cursor.lastrowid
            logger.info(
                f"Ticket #{db_ticket_id} in SQLite erstellt von User {user_id}: {subject}"
            )
        except Exception as e:
            logger.error(f"Ticket in SQLite erstellen fehlgeschlagen: {e}")

        # Ticket-ID bestimmen: DB-ID bevorzugen, sonst Fallback auf next_id
        if db_ticket_id is not None:
            ticket_id = db_ticket_id
            # next_id aktualisieren falls noetig
            if ticket_id >= self._data["next_id"]:
                self._data["next_id"] = ticket_id + 1
        else:
            ticket_id = self._data["next_id"]
            self._data["next_id"] += 1

        ticket_id_str = str(ticket_id)

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

        # In-Memory-Cache aktualisieren
        self._data["tickets"][ticket_id_str] = ticket

        logger.info(
            f"Ticket #{ticket_id} erstellt von User {user_id}: {subject}"
        )
        return dict(ticket)

    # ------------------------------------------------------------------
    # Ticket aktualisieren
    # ------------------------------------------------------------------

    async def update_ticket_channel(self, ticket_id: int, channel_id: int) -> None:
        """
        Channel-ID eines Tickets nachtraeglich setzen.

        Wird aufgerufen nachdem der Discord-Channel erfolgreich
        erstellt wurde.

        Args:
            ticket_id: Ticket-ID
            channel_id: Discord-Channel-ID
        """
        tid = str(ticket_id)

        # In-Memory-Cache aktualisieren
        if tid in self._data["tickets"]:
            self._data["tickets"][tid]["channel_id"] = channel_id

        # In SQLite aktualisieren
        try:
            db = await get_db()
            await db.execute(
                "UPDATE tickets SET channel_id = ? WHERE id = ?",
                (str(channel_id), ticket_id),
            )
            await db.commit()
        except Exception as e:
            logger.error(
                f"Ticket #{ticket_id} channel_id in SQLite aktualisieren fehlgeschlagen: {e}"
            )

    # ------------------------------------------------------------------
    # Ticket schliessen
    # ------------------------------------------------------------------

    async def close_ticket(
        self,
        ticket_id: int,
        closed_by: int,
        reason: str | None = None,
    ) -> dict[str, Any] | None:
        """
        Ticket schliessen und Status in SQLite aktualisieren.

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

        # In-Memory-Cache aktualisieren
        ticket["status"] = "closed"
        ticket["closed_at"] = now.isoformat()
        ticket["closed_by"] = closed_by

        # In SQLite aktualisieren
        try:
            db = await get_db()
            await db.execute(
                "UPDATE tickets SET status = 'closed', closed_at = ?, closed_by = ? "
                "WHERE id = ?",
                (now.isoformat(), str(closed_by), ticket_id),
            )
            await db.commit()
        except Exception as e:
            logger.error(
                f"Ticket #{ticket_id} in SQLite schliessen fehlgeschlagen: {e}"
            )

        if reason:
            await self.add_transcript_entry(
                ticket_id,
                author="System",
                content=f"Ticket geschlossen: {reason}",
                author_id=0,
            )

        logger.info(
            f"Ticket #{ticket_id} geschlossen von User {closed_by}"
            + (f" — Grund: {reason}" if reason else "")
        )
        return dict(ticket)

    # ------------------------------------------------------------------
    # Transcript
    # ------------------------------------------------------------------

    async def add_transcript_entry(
        self,
        ticket_id: int,
        author: str,
        content: str,
        author_id: int = 0,
    ) -> bool:
        """
        Nachricht zum Ticket-Transcript hinzufuegen (SQLite + In-Memory).

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

        now = datetime.now()

        entry = {
            "author": author,
            "author_id": author_id,
            "content": content,
            "timestamp": now.isoformat(),
        }

        # In-Memory-Cache aktualisieren
        ticket["transcript"].append(entry)

        # In SQLite einfuegen
        try:
            db = await get_db()
            await db.execute(
                "INSERT INTO ticket_messages (ticket_id, user_id, content, timestamp) "
                "VALUES (?, ?, ?, ?)",
                (ticket_id, str(author_id), content, now.isoformat()),
            )
            await db.commit()
        except Exception as e:
            logger.error(
                f"Transcript-Eintrag fuer Ticket #{ticket_id} in SQLite "
                f"speichern fehlgeschlagen: {e}"
            )

        return True

    # ------------------------------------------------------------------
    # Abfragen (lesen aus In-Memory-Cache, synchron)
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
    # Konfiguration (bleibt JSON)
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
