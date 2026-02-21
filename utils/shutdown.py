"""
F61: Graceful Shutdown Handler — Sauberes Herunterfahren bei SIGTERM/SIGINT

Registriert Signal-Handler und sorgt fuer geordnetes Cleanup:
1. shutting_down Flag setzen
2. Background-Tasks cancel + await
3. Letzte Daten speichern
4. Connections schliessen
5. Log schreiben

SQLite-spezifische Teile (WAL-Checkpoint) werden in Phase 2 ergaenzt.
"""

import signal
import asyncio
from typing import Optional, Callable, Coroutine, Any

from utils.logger import get_logger

logger = get_logger("shutdown")

# Globaler Shutdown-Status
shutting_down: bool = False

# Registrierte Cleanup-Callbacks (async Funktionen)
_cleanup_callbacks: list[Callable[[], Coroutine[Any, Any, None]]] = []

# Timeout fuer erzwungenes Beenden (Sekunden)
SHUTDOWN_TIMEOUT = 15


def is_shutting_down() -> bool:
    """Prueft ob ein Shutdown im Gange ist."""
    return shutting_down


def register_cleanup(callback: Callable[[], Coroutine[Any, Any, None]]) -> None:
    """
    Registriert eine async Cleanup-Funktion die beim Shutdown aufgerufen wird.

    Args:
        callback: Async Funktion ohne Parameter
    """
    _cleanup_callbacks.append(callback)
    logger.debug(f"Cleanup-Callback registriert: {callback.__name__}")


def setup_signal_handlers(bot, bot_name: str = "Bot",
                          admin_channel_id: Optional[int] = None) -> None:
    """
    Registriert SIGTERM und SIGINT Handler fuer graceful shutdown.

    Args:
        bot: discord.ext.commands.Bot Instanz
        bot_name: Name des Bots fuer Logging
        admin_channel_id: Optional Channel-ID fuer Shutdown-Nachricht
    """
    loop = asyncio.get_event_loop()

    def _signal_handler(sig: signal.Signals) -> None:
        global shutting_down
        if shutting_down:
            logger.warning(f"Zweites Signal empfangen ({sig.name}), erzwinge Beenden...")
            raise SystemExit(1)

        shutting_down = True
        sig_name = sig.name
        logger.info(f"Signal {sig_name} empfangen — starte graceful shutdown fuer {bot_name}")

        # Shutdown-Task im Event-Loop starten
        asyncio.ensure_future(_graceful_shutdown(bot, bot_name, admin_channel_id))

    # Signal-Handler registrieren
    try:
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, lambda s=sig: _signal_handler(s))
        logger.info(f"Signal-Handler registriert fuer {bot_name} (SIGTERM, SIGINT)")
    except NotImplementedError:
        # Windows unterstuetzt add_signal_handler nicht
        logger.warning("Signal-Handler nicht verfuegbar (Windows) — "
                       "verwende signal.signal() Fallback")
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, lambda s, f, _sig=sig: _signal_handler(_sig))


async def _graceful_shutdown(bot, bot_name: str,
                             admin_channel_id: Optional[int] = None) -> None:
    """Fuehrt den geordneten Shutdown durch."""
    logger.info(f"Graceful Shutdown fuer {bot_name} gestartet...")

    # 1. Admin-Channel benachrichtigen
    if admin_channel_id and bot.is_ready():
        try:
            channel = bot.get_channel(admin_channel_id)
            if channel:
                await channel.send(
                    f"**{bot_name}** wird heruntergefahren..."
                )
        except Exception as e:
            logger.debug(f"Shutdown-Nachricht fehlgeschlagen: {e}")

    # 2. Registrierte Cleanup-Callbacks ausfuehren
    for callback in reversed(_cleanup_callbacks):
        try:
            logger.debug(f"Cleanup: {callback.__name__}")
            await asyncio.wait_for(callback(), timeout=5.0)
        except asyncio.TimeoutError:
            logger.warning(f"Cleanup-Timeout fuer {callback.__name__}")
        except Exception as e:
            logger.warning(f"Cleanup-Fehler in {callback.__name__}: {e}")

    # 3. Bot schliessen
    try:
        logger.info(f"{bot_name} wird geschlossen...")
        await bot.close()
    except Exception as e:
        logger.warning(f"Bot.close() Fehler: {e}")

    logger.info(f"{bot_name} sauber heruntergefahren.")
