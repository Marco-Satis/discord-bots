"""Asyncio-Task-Tracking gegen Fire-and-Forget-GC.

Hintergrund: CPython haelt asyncio.Tasks nur ueber Weak-References — wer
`asyncio.create_task(coro)` aufruft und das Ergebnis nicht speichert, riskiert
dass der Garbage-Collector den Task einsammelt bevor er fertig ist. Dann wird
das `coro` stillschweigend verworfen, kein Fehler, kein Log.

Dieser Helper bietet eine modul-globale `set`-Registry plus eine Klassen-Mixin.
Beides nutzt `add_done_callback(discard)` damit fertige Tasks die Referenz
freigeben und der Set nicht waechst.

Quelle: CPython Issue 91887 / Python Bug-Tracker; dokumentiert seit Python 3.10.
Audit-Lesson 2026-05-17 (async-reviewer H2).
"""

from __future__ import annotations

import asyncio
from typing import Awaitable, Optional, Set

from utils.logger import get_logger

logger = get_logger(__name__)

# Modul-globale Registry — fuer freistehende Coros ohne Klasse drumherum.
# Bei Cleanup (atexit / shutdown) optional iterieren und cancelln.
_global_bg_tasks: Set[asyncio.Task] = set()


def track_task(coro: Awaitable, *, name: Optional[str] = None) -> asyncio.Task:
    """Erstellt einen asyncio.Task und schuetzt ihn vor GC.

    Args:
        coro: Awaitable / Coroutine das ausgefuehrt werden soll.
        name: optionaler Task-Name fuer Debug-Output.

    Returns:
        Der erstellte Task. Aufrufer muss nichts speichern — der Helper haelt
        eine starke Referenz bis der Task done ist.

    Raises:
        RuntimeError: wenn kein Event-Loop laeuft.
    """
    task = asyncio.create_task(coro, name=name) if name else asyncio.create_task(coro)
    _global_bg_tasks.add(task)
    task.add_done_callback(_on_task_done)
    return task


def _on_task_done(task: asyncio.Task) -> None:
    """Callback: entfernt Task aus Registry, loggt unbehandelte Exceptions."""
    _global_bg_tasks.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        task_name = task.get_name() if hasattr(task, "get_name") else "?"
        logger.error(f"Background-Task '{task_name}' fehlgeschlagen: {exc!r}",
                     exc_info=exc)


def schedule_from_sync(coro: Awaitable, *,
                       name: Optional[str] = None) -> Optional[asyncio.Task]:
    """Plant einen Task aus synchronem Code wenn ein Event-Loop laeuft.

    Gibt None zurueck wenn kein Loop verfuegbar ist (statt RuntimeError), damit
    Caller in Edge-Cases nicht crashen muessen. Sonst identisch zu `track_task`.
    """
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        logger.debug(f"Kein Event-Loop fuer schedule_from_sync('{name or '?'}')")
        return None
    if not loop.is_running():
        logger.debug(f"Event-Loop nicht running fuer schedule_from_sync('{name or '?'}')")
        return None
    return track_task(coro, name=name)


def get_pending_count() -> int:
    """Anzahl laufender getrackter Tasks (fuer Monitoring / Tests)."""
    return len(_global_bg_tasks)


class BackgroundTaskMixin:
    """Mixin fuer Klassen die eigene Tasks tracken wollen (ohne Global-State).

    Usage:
        class MyService(BackgroundTaskMixin):
            def __init__(self):
                super().__init__()
                ...

            def trigger(self):
                self._track_bg(self._do_work())
    """

    def __init__(self) -> None:  # noqa: D401
        # Verwende ein eigenes Set pro Instanz damit `self._bg_tasks` nicht
        # mit anderen Mixin-Nutzern kollidiert.
        self._bg_tasks: Set[asyncio.Task] = set()

    def _track_bg(self, coro: Awaitable, *,
                  name: Optional[str] = None) -> asyncio.Task:
        """Wie track_task, aber haelt die Referenz an der Instanz statt global."""
        task = asyncio.create_task(coro, name=name) if name else asyncio.create_task(coro)
        self._bg_tasks.add(task)
        task.add_done_callback(self._on_bg_task_done)
        return task

    def _on_bg_task_done(self, task: asyncio.Task) -> None:
        """Per-Instanz Done-Callback. Entfernt Task, loggt Exceptions."""
        self._bg_tasks.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            cls_name = type(self).__name__
            task_name = task.get_name() if hasattr(task, "get_name") else "?"
            logger.error(f"[{cls_name}] Background-Task '{task_name}' "
                         f"fehlgeschlagen: {exc!r}", exc_info=exc)

    def _cancel_all_bg(self) -> None:
        """Bricht alle laufenden Tasks ab (z.B. bei Shutdown)."""
        for task in list(self._bg_tasks):
            if not task.done():
                task.cancel()
