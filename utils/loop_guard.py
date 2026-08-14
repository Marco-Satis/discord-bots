"""
Gemeinsamer Fehler-Handler fuer ``discord.ext.tasks``-Schleifen.

Warum es das braucht: discord.py beendet eine ``tasks.loop`` bei jeder Ausnahme
ausserhalb der eigenen Retry-Liste **endgueltig**
(``discord/ext/tasks/__init__.py`` — ``except Exception: … raise exc``). Der
Prozess bleibt danach ``active``, die Aufgabe ist tot. Ein Ausfall sieht also
genauso aus wie Ruhe: keine Backups mehr, kein Offline-Alarm, keine ablaufenden
Timeouts — und niemand merkt es.

Zwei Fallen aus der Bibliothek, die dieser Code bewusst umgeht:

* Im ``.error``-Handler laeuft der Task noch, ``is_running()`` ist dort **True**.
  Ein Waechter ``if not loop.is_running(): loop.restart()`` greift deshalb nie —
  genau daran scheiterte der einzige bisher vorhandene Handler.
* Nach dem Tod ist ``restart()`` ein stiller No-op (die Schleife kann nicht mehr
  abgebrochen werden). Der Neustart muss **verzoegert** ueber ``start()`` laufen,
  wenn der alte Task wirklich beendet ist.

Benutzung::

    from utils.loop_guard import guard, guard_all, set_reporter

    set_reporter(melde_funktion)      # optional, z.B. Admin-Kanal
    guard_all(*core_tasks)            # Modul-Schleifen
    guard(self.scheduler_tick)        # Cog-Schleife (gebundene Kopie!)
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set

from discord.ext import tasks

from utils.logger import get_logger

logger = get_logger("loop_guard")

# Neustart-Budget je Schleife. Mehr als MAX_NEUSTARTS in WINDOW_SEKUNDEN heisst:
# der Fehler ist nicht voruebergehend. Dann bleibt die Schleife gestoppt —
# endloses Neustarten wuerde den Ausfall nur zu Rauschen machen.
MAX_NEUSTARTS = 5
WINDOW_SEKUNDEN = 3600.0
BACKOFF_SEKUNDEN = (5.0, 15.0, 60.0, 300.0, 900.0)

# Neustart-Tasks festhalten: eine nur lokal referenzierte Task kann vom
# Garbage Collector eingesammelt werden, bevor sie laeuft.
_laufende: Set[asyncio.Task] = set()
_historie: Dict[str, List[float]] = {}

# Alle bewachten Schleifen, damit das Herunterfahren sie geordnet stoppen kann.
# Ohne das laufen die Modul-Schleifen (die zu keinem Cog gehoeren und deshalb
# kein cog_unload haben) noch waehrend des Shutdowns weiter und greifen auf
# bereits geschlossene Verbindungen zu.
_bewacht: Dict[str, tasks.Loop] = {}

_melder: Optional[Callable[[str, str], Awaitable[None]]] = None


def set_reporter(fn: Optional[Callable[[str, str], Awaitable[None]]]) -> None:
    """Meldeweg registrieren: ``(titel, text) -> awaitable``. Optional."""
    global _melder
    _melder = fn


def reset_historie() -> None:
    """Neustart-Zaehler und Schleifen-Verzeichnis leeren (fuer Tests)."""
    _historie.clear()
    _bewacht.clear()


async def _wiederbeleben(schleife: tasks.Loop, name: str, verzoegerung: float) -> None:
    await asyncio.sleep(verzoegerung)
    if schleife.is_running():
        return
    try:
        schleife.start()
        logger.info("Schleife %s nach %.0fs neu gestartet", name, verzoegerung)
    except RuntimeError as e:
        # start() wirft, wenn inzwischen jemand anders neu gestartet hat.
        logger.warning("Neustart von %s abgelehnt: %s", name, e)


def _faehrt_herunter() -> bool:
    """Laeuft gerade ein Shutdown? Traege importiert wegen des Ringschlusses."""
    try:
        from utils.shutdown import is_shutting_down
        return is_shutting_down()
    except ImportError:
        return False


def alle_stoppen() -> List[str]:
    """
    Alle bewachten Schleifen abbrechen. Wird beim Herunterfahren aufgerufen.

    ``cancel()`` statt ``stop()``: ``stop()`` laesst den laufenden Durchlauf
    zu Ende laufen, und genau der wuerde noch in eine gerade geschlossene
    Datenbank schreiben. Gibt die Namen der Schleifen zurueck, die liefen.

    Achtung: das ist ein Endzustand. Nach ``alle_stoppen()`` startet nichts
    von selbst wieder — der Fehler-Handler weigert sich waehrend des
    Shutdowns ebenfalls, neu zu starten.
    """
    gestoppt: List[str] = []
    for name, schleife in _bewacht.items():
        try:
            if schleife.is_running():
                schleife.cancel()
                gestoppt.append(name)
        except Exception as e:  # noqa: BLE001 — ein Fehler hier darf den Rest nicht aufhalten
            logger.warning("Schleife %s liess sich nicht stoppen: %s", name, e)
    if gestoppt:
        logger.info("%d Hintergrund-Schleifen gestoppt: %s",
                    len(gestoppt), ", ".join(sorted(gestoppt)))
    return gestoppt


def guard(schleife: tasks.Loop, name: Optional[str] = None) -> tasks.Loop:
    """
    Den gemeinsamen ``.error``-Handler auf einer Schleife registrieren.

    Gibt dieselbe Schleife zurueck, damit sich Aufrufe verketten lassen.
    Bei Cog-Schleifen die **gebundene** Kopie uebergeben (``self.mein_task``),
    nicht das Klassenattribut — discord.py erzeugt je Instanz eine eigene Kopie.
    """
    label = name or getattr(schleife.coro, "__qualname__", "unbenannt")

    async def _bei_fehler(*args: Any) -> None:
        # Signatur ist (fehler,) bei Modul-Schleifen und (self, fehler) bei
        # Cog-Schleifen — der Fehler steht immer am Ende.
        fehler = args[-1]

        # Beim Herunterfahren brechen Schleifen an geschlossenen Verbindungen
        # ab. Das ist kein Ausfall: es waere sinnlos, deswegen den Admin-Kanal
        # zu belegen und einen Neustart zu planen, den niemand mehr erlebt.
        if _faehrt_herunter():
            logger.info("Schleife %s beim Herunterfahren beendet: %s", label, fehler)
            return

        jetzt = time.monotonic()
        treffer = [t for t in _historie.get(label, []) if jetzt - t < WINDOW_SEKUNDEN]
        treffer.append(jetzt)
        _historie[label] = treffer
        versuch = len(treffer)

        logger.error(
            "Schleife %s gestorben (Versuch %d/%d in %.0f min): %s",
            label, versuch, MAX_NEUSTARTS, WINDOW_SEKUNDEN / 60, fehler,
            exc_info=fehler,
        )

        if _melder is not None:
            try:
                await _melder(
                    f"Hintergrund-Schleife gestorben: {label}",
                    f"`{type(fehler).__name__}: {fehler}`\n"
                    f"Neustart {versuch}/{MAX_NEUSTARTS}.",
                )
            except Exception as e:  # noqa: BLE001 — Melden darf nicht mitsterben
                logger.warning("Meldung fuer %s fehlgeschlagen: %s", label, e)

        if versuch > MAX_NEUSTARTS:
            logger.critical(
                "Schleife %s bleibt gestoppt — %d Fehlschlaege in %.0f min.",
                label, versuch, WINDOW_SEKUNDEN / 60,
            )
            return

        verzoegerung = BACKOFF_SEKUNDEN[min(versuch - 1, len(BACKOFF_SEKUNDEN) - 1)]
        task = asyncio.create_task(
            _wiederbeleben(schleife, label, verzoegerung),
            name=f"loop_guard.restart[{label}]",
        )
        _laufende.add(task)
        task.add_done_callback(_laufende.discard)

    schleife.error(_bei_fehler)  # type: ignore[arg-type]
    _bewacht[label] = schleife
    return schleife


def guard_all(*schleifen: tasks.Loop) -> None:
    """Bequemform fuer mehrere Schleifen auf einmal."""
    for schleife in schleifen:
        guard(schleife)


__all__ = ["guard", "guard_all", "set_reporter", "reset_historie",
           "alle_stoppen", "MAX_NEUSTARTS", "WINDOW_SEKUNDEN"]
