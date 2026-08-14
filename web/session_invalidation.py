"""
Abmeldung, die wirklich abmeldet.

Das Dashboard authentifiziert über ein signiertes JWT im Cookie. Ein solches
Token ist bis zu seinem Ablauf gültig — auch nachdem der Nutzer „Abmelden"
geklickt hat, denn das Löschen des Cookies passiert nur im Browser. Wer das
Cookie vorher kopiert hat, kommt damit weiter rein (bis 2026-08-14 volle
24 Stunden lang).

Gegenmittel ist ein Zeitstempel je Nutzer: beim Abmelden wird „ab jetzt gilt
nichts Älteres" vermerkt. Die Token-Prüfung vergleicht das `iat`-Feld dagegen.
Damit sind alle Sitzungen dieses Nutzers auf allen Geräten beendet — genau das,
was man von einer Abmeldung erwartet.

Bewusst eine Datei und keine Tabelle: die Token-Prüfung (`_decode_jwt`) ist
synchron, die Datenbank des Projekts ist async. Ein Umbau der Prüfung auf async
würde jede Middleware berühren; die Datei ist ein paar hundert Byte groß und
wird mit kurzem Zwischenspeicher gelesen.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Dict, Optional

from utils.config import DATA_DIR
from utils.logger import get_logger

logger = get_logger("web.session_invalidation")

SPERRDATEI: Path = Path(DATA_DIR) / "session_invalidation.json"

# Der Zwischenspeicher hält die Datei kurz — die Prüfung läuft bei jedem
# Request. Fünf Sekunden sind kurz genug, dass eine Abmeldung sofort wirkt,
# und lang genug, dass daraus kein Dauer-Lesen wird.
_CACHE_SEKUNDEN = 5.0
_cache: Dict[str, float] = {}
_cache_zeit: float = 0.0


def _laden() -> Dict[str, float]:
    global _cache, _cache_zeit
    jetzt = time.monotonic()
    if _cache_zeit and (jetzt - _cache_zeit) < _CACHE_SEKUNDEN:
        return _cache
    try:
        with open(SPERRDATEI, encoding="utf-8") as f:
            daten = json.load(f)
        _cache = {str(k): float(v) for k, v in daten.items()}
    except FileNotFoundError:
        _cache = {}
    except (json.JSONDecodeError, ValueError, OSError) as e:
        # Eine kaputte Sperrdatei darf niemanden aussperren und niemanden
        # durchlassen, den sie aussperren sollte — sichtbar melden und mit
        # leerem Stand weiterarbeiten.
        logger.warning(f"Sperrliste nicht lesbar ({e}) — als leer behandelt")
        _cache = {}
    _cache_zeit = jetzt
    return _cache


def abmelden(user_id: str) -> None:
    """Alle bestehenden Token dieses Nutzers ungültig machen."""
    if not user_id:
        return
    daten = dict(_laden())
    daten[str(user_id)] = time.time()
    try:
        SPERRDATEI.parent.mkdir(parents=True, exist_ok=True)
        tmp = SPERRDATEI.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(daten, indent=2), encoding="utf-8")
        os.replace(tmp, SPERRDATEI)
        os.chmod(SPERRDATEI, 0o600)
    except OSError as e:
        # Schlägt das Schreiben fehl, bleibt das Token gültig. Das ist der
        # Zustand von vorher — aber es muss auffallen.
        logger.error(f"Abmeldung konnte nicht vermerkt werden: {e}")
        return
    global _cache, _cache_zeit
    _cache, _cache_zeit = daten, time.monotonic()
    logger.info(f"Sitzungen von {user_id} beendet")


def ist_abgemeldet(user_id: Optional[str], ausgestellt: Optional[float]) -> bool:
    """
    Wurde dieses Token vor der letzten Abmeldung des Nutzers ausgestellt?

    Args:
        user_id: `sub` aus dem Token.
        ausgestellt: `iat` aus dem Token (Sekunden seit Epoch).
    """
    if not user_id or ausgestellt is None:
        return False
    grenze = _laden().get(str(user_id))
    if grenze is None:
        return False
    # Eine Sekunde Toleranz: `iat` wird auf ganze Sekunden gerundet, ein
    # frisches Token nach dem Abmelden soll nicht an Rundung scheitern.
    return float(ausgestellt) < (grenze - 1.0)


def aufraeumen(aelter_als_sekunden: float = 60 * 60 * 24 * 30) -> int:
    """Einträge entfernen, die älter sind als jede mögliche Token-Laufzeit."""
    daten = dict(_laden())
    grenze = time.time() - aelter_als_sekunden
    behalten = {k: v for k, v in daten.items() if v >= grenze}
    entfernt = len(daten) - len(behalten)
    if entfernt:
        try:
            tmp = SPERRDATEI.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(behalten, indent=2), encoding="utf-8")
            os.replace(tmp, SPERRDATEI)
            global _cache, _cache_zeit
            _cache, _cache_zeit = behalten, time.monotonic()
        except OSError as e:
            logger.warning(f"Aufräumen der Sperrliste fehlgeschlagen: {e}")
            return 0
    return entfernt


__all__ = ["abmelden", "ist_abgemeldet", "aufraeumen", "SPERRDATEI"]
