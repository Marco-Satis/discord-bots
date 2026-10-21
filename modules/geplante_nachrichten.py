"""
Geplante Nachrichten — gemeinsame Ablage für Recon (`/schedule`) und Dashboard (21.10.2026).

Bisher hielt der Scheduler-Cog die Liste im Speicher und schrieb sie nur zurück.
Damit das Dashboard Nachrichten anlegen und löschen kann, ohne dass der nächste
Speichervorgang von Recon sie wieder überschreibt, läuft jeder Zugriff über diese
Funktionen: Datei lesen → ändern → atomar schreiben, alles unter einer Dateisperre
(fcntl, prozessübergreifend; Recon und Dashboard laufen beide als botuser).

Datei und Format bleiben wie bisher: data/scheduled_messages.json mit
{"messages": [...], "next_id": n}. Synchrone Funktionen — aus async-Code per
asyncio.to_thread aufrufen.
"""
from __future__ import annotations

import fcntl
import json
import os
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any, Iterator, Optional
from zoneinfo import ZoneInfo

from utils.config import DATA_DIR
from utils.logger import get_logger

logger = get_logger("geplante_nachrichten")

DATEI = DATA_DIR / "scheduled_messages.json"
SPERRE = DATA_DIR / "scheduled_messages.lock"
BERLIN = ZoneInfo("Europe/Berlin")
MAX_AKTIV = 20
MAX_LAENGE = 2000
WIEDERHOLUNG = {"einmalig": None, "taeglich": timedelta(days=1), "woechentlich": timedelta(weeks=1)}


@contextmanager
def _gesperrt() -> Iterator[None]:
    SPERRE.parent.mkdir(parents=True, exist_ok=True)
    with open(SPERRE, "a+") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def _lesen() -> dict[str, Any]:
    try:
        with open(DATEI, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("kein Objekt")
    except FileNotFoundError:
        data = {}
    except (ValueError, OSError) as e:
        logger.error(f"Geplante Nachrichten nicht lesbar: {e}")
        data = {}
    data.setdefault("messages", [])
    data.setdefault("next_id", max([m.get("id", 0) for m in data["messages"]] + [0]) + 1)
    return data


def _schreiben(data: dict[str, Any]) -> None:
    DATEI.parent.mkdir(parents=True, exist_ok=True)
    tmp = DATEI.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, DATEI)


def liste() -> list[dict[str, Any]]:
    """Alle aktiven Einträge, nächster Termin zuerst."""
    with _gesperrt():
        msgs = _lesen()["messages"]
    return sorted(msgs, key=lambda m: m.get("next_run", ""))


def hinzufuegen(text: str, channel_id: int, channel_name: str, zeitpunkt: datetime,
                wiederholung: str, von: str, jetzt: Optional[datetime] = None) -> dict[str, Any]:
    """Neuen Eintrag anlegen. ValueError mit deutscher Meldung bei ungültigen Werten."""
    jetzt = jetzt or datetime.now(BERLIN)
    text = (text or "").strip()
    if not text:
        raise ValueError("Die Nachricht ist leer.")
    if len(text) > MAX_LAENGE:
        raise ValueError(f"Die Nachricht darf höchstens {MAX_LAENGE} Zeichen haben.")
    if wiederholung not in WIEDERHOLUNG:
        raise ValueError("Unbekannte Wiederholung.")
    if zeitpunkt.tzinfo is None:
        zeitpunkt = zeitpunkt.replace(tzinfo=BERLIN)
    if zeitpunkt <= jetzt:
        raise ValueError("Der Zeitpunkt liegt in der Vergangenheit.")
    with _gesperrt():
        data = _lesen()
        if len(data["messages"]) >= MAX_AKTIV:
            raise ValueError(f"Es sind schon {MAX_AKTIV} Nachrichten geplant (Höchstzahl).")
        eintrag = {
            "id": data["next_id"],
            "message": text,
            "channel_id": int(channel_id),
            "channel_name": channel_name,
            "next_run": zeitpunkt.isoformat(),
            "repeat": wiederholung,
            "created_by": von,
            "created_at": jetzt.isoformat(),
        }
        data["messages"].append(eintrag)
        data["next_id"] += 1
        _schreiben(data)
    return eintrag


def loeschen(eintrag_id: int) -> bool:
    """Eintrag löschen. False, wenn es ihn nicht gibt."""
    with _gesperrt():
        data = _lesen()
        vorher = len(data["messages"])
        data["messages"] = [m for m in data["messages"] if m.get("id") != eintrag_id]
        if len(data["messages"]) == vorher:
            return False
        _schreiben(data)
    return True


def faellige(jetzt: Optional[datetime] = None) -> list[dict[str, Any]]:
    """Einträge, deren Termin erreicht ist (Kopien)."""
    jetzt = jetzt or datetime.now(BERLIN)
    aus = []
    for m in liste():
        try:
            if datetime.fromisoformat(m["next_run"]) <= jetzt:
                aus.append(dict(m))
        except (KeyError, ValueError):
            logger.warning(f"Geplante Nachricht #{m.get('id', '?')} hat keinen gültigen Termin")
    return aus


def erledigt(ids: list[int], jetzt: Optional[datetime] = None) -> None:
    """
    Nach dem Senden: wiederkehrende auf den nächsten Termin in der Zukunft setzen,
    einmalige entfernen. Inzwischen gelöschte IDs werden übergangen.
    """
    jetzt = jetzt or datetime.now(BERLIN)
    with _gesperrt():
        data = _lesen()
        behalten = []
        for m in data["messages"]:
            if m.get("id") not in ids:
                behalten.append(m)
                continue
            schritt = WIEDERHOLUNG.get(m.get("repeat", "einmalig"))
            if schritt is None:
                continue
            naechster = datetime.fromisoformat(m["next_run"])
            while naechster <= jetzt:          # nach Ausfall nicht mehrfach nachsenden
                naechster += schritt
            m["next_run"] = naechster.isoformat()
            behalten.append(m)
        data["messages"] = behalten
        _schreiben(data)


def vergangene_einmalige_entfernen(jetzt: Optional[datetime] = None) -> int:
    """Beim Start: einmalige, deren Termin während eines Ausfalls verstrich, verwerfen."""
    jetzt = jetzt or datetime.now(BERLIN)
    with _gesperrt():
        data = _lesen()
        vorher = len(data["messages"])
        data["messages"] = [
            m for m in data["messages"]
            if m.get("repeat", "einmalig") != "einmalig" or datetime.fromisoformat(m["next_run"]) > jetzt
        ]
        entfernt = vorher - len(data["messages"])
        if entfernt:
            _schreiben(data)
    return entfernt
